-- =============================================================================
-- STEP 2: SENTINELONE FINAL RECONCILIATION
-- =============================================================================
-- Proofpoint-style contract adapted to SentinelOne:
--   Vendor usage truth  = SENTINELONE_USAGE product quantities.
--   Billing truth       = Zuora Posted BillRun + Marketplace.
--   TRT/internal meter  = supporting validation only; it never fills billing.
--
-- Grain:
--   Main detail is account-month-sku_match_group. Vendor product labels are
--   retained/listagged, but billing quantities are grouped by sku_match_group so
--   shared bridge SKUs (for example Ranger / Ranger Insights / Ranger AD) do not
--   duplicate billed quantity.
--
-- Vendor matching rules:
--   * SENTINELONE_USAGE already resolves the invoice-match product into
--     VENDOR_PRODUCT_SKU during ingestion.
--   * Total Active Agents is loaded as Complete / Control / Core.
--   * Data Retention is loaded as "Data Retention - <tier>".
--   * All other vendor products are loaded as their sku_match_group label.
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

-- ACCOUNT_MERGE_RESOLVER + SENTINELONE_SF_ID_ALIAS_RESOLVER (curated duplicate-
-- mapping fixes) are unified as SENTINELONE_SF_ID_RESOLVER in
-- 00_reference_maps.sql and reused here to canonicalize Zuora and Marketplace
-- billing sf_ids. The V5 partner map is already canonicalized at build time,
-- so only external billing tables still need runtime resolution.

CREATE OR REPLACE TABLE SENTINELONE_RECON_DETAIL AS

WITH fx_rates AS (
    SELECT UPPER(currency_id) AS currency_id, budget_ex_rate
    FROM analytics.dbo_seed_files.seed__fpa_budget_exchange_rates
    WHERE year(start_date) = (
        SELECT MAX(year(start_date))
        FROM analytics.dbo_seed_files.seed__fpa_budget_exchange_rates
    )
),

merged_account_resolver AS (
    -- 2026-08-12: carries merge_effective_month so billing-side joins can
    -- gate the merge on BILLING_MONTH >= merge_effective_month. Rows whose
    -- billing month PRE-dates the merge keep their original sf_id
    -- (historical truth of who was actually billed).
    SELECT old_sf_id, canonical_sf_id, resolver_depth AS merge_depth,
           merge_effective_month
    FROM SENTINELONE_SF_ID_RESOLVER
),

sku_map AS (
    SELECT
        vendor_product,
        UPPER(TRIM(vendor_product)) AS vendor_product_key,
        vendor_sku_invoices,
        UPPER(TRIM(cw_sku)) AS cw_sku,
        sku_match_group,
        SKU_MATCH_KEY              AS sku_match_group,
        MAPPING_NOTES              AS mapping_source,
        CW_RETAIL_RATE             AS vendor_invoice_unit_price,
        VENDOR_SKU                 AS vendor_invoice_sku,
        'RECON_SKU_MAP'            AS vendor_invoice_rate_source
    FROM (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'SentinelOne')
    WHERE SKU_MATCH_KEY IS NOT NULL
),

-- One row per sku_match_group with the canonical invoice-derived unit price
-- (all CW_SKU aliases in the same group carry the same rate). This drives
-- vendor_amount downstream so the app can show a real vendor-charge column
-- instead of the previous NULL/zero.
sku_group_invoice_rate AS (
    SELECT
        sku_match_group,
        MAX(vendor_invoice_unit_price) AS vendor_invoice_unit_price,
        MAX(vendor_invoice_sku)        AS vendor_invoice_sku,
        MAX(vendor_invoice_rate_source) AS vendor_invoice_rate_source
    FROM sku_map
    WHERE vendor_invoice_unit_price IS NOT NULL
    GROUP BY sku_match_group
),

sku_group_map AS (
    SELECT
        sku_match_group,
        ARRAY_AGG(DISTINCT cw_sku) WITHIN GROUP (ORDER BY cw_sku) AS mapped_cw_skus,
        LISTAGG(DISTINCT vendor_product, ' | ') WITHIN GROUP (ORDER BY vendor_product) AS mapped_vendor_products,
        LISTAGG(DISTINCT vendor_sku_invoices, ' | ') WITHIN GROUP (ORDER BY vendor_sku_invoices) AS mapped_vendor_invoice_skus,
        COUNT_IF(cw_sku IS NOT NULL AND cw_sku <> '' AND cw_sku <> 'UNMAPPED') AS mapped_cw_sku_count
    FROM sku_map
    GROUP BY 1
),

vendor_product_group_map AS (
    SELECT DISTINCT
        vendor_product_key,
        sku_match_group
    FROM sku_map
),

cw_sku_group_map AS (
    SELECT DISTINCT
        cw_sku,
        sku_match_group
    FROM sku_map
    WHERE cw_sku IS NOT NULL
      AND cw_sku <> ''
      AND cw_sku <> 'UNMAPPED'
),

partner_map AS (
    -- V5 map is pre-canonicalized in 00_reference_maps.sql, so sf_id here is
    -- already the current Salesforce canonical id.
    SELECT TRIM(partner_name) AS partner_name, sf_id, zuora_name
    FROM RECON_PARTNER_MAP
    WHERE sf_id IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY UPPER(TRIM(partner_name))
        ORDER BY zuora_name DESC NULLS LAST
    ) = 1
),

-- Canonical partner name per sf_id. Prefer rows that were NEVER merged
-- (merge_depth IS NULL â€” the surviving entity), then shortest / lowest-alpha
-- as tie-breaker so we display a single stable label instead of pipe-joining
-- the merged-away aliases (e.g. INTEGRIS not "INTEGRIS | TECHMD | TECHMD-INTERNAL").
partner_canonical_name AS (
    SELECT
        sf_id,
        partner_name AS canonical_partner_name
    FROM RECON_PARTNER_MAP
    WHERE sf_id IS NOT NULL
      AND partner_name IS NOT NULL
      AND TRIM(partner_name) <> ''
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY sf_id
        ORDER BY
            IFF(parent_company IS NOT NULL, 0, 1),
            IFF(zuora_name IS NOT NULL, 0, 1),
            -- Prefer non-INTERNAL / non-CORPORATE labels
            IFF(UPPER(partner_name) LIKE '%-INTERNAL', 1, 0),
            IFF(UPPER(partner_name) LIKE '%-CORPORATE', 1, 0),
            -- Then shortest & alpha for determinism
            LENGTH(partner_name),
            UPPER(partner_name)
    ) = 1
),

mapped_sf_ids AS (
    -- V5 partner map (canonical sf_ids only) PLUS any OLD sf_ids from the
    -- merge resolver. Adding the old sf_ids ensures that Zuora rows on a
    -- merged-away sf_id whose BILLING_MONTH pre-dates the merge (and thus
    -- are intentionally NOT collapsed onto the canonical) still pass the
    -- "known partner" filter downstream and surface in recon. Without this,
    -- date-aware pre-merge Zuora rows would silently disappear from the
    -- reconciliation detail.
    SELECT DISTINCT sf_id
    FROM RECON_PARTNER_MAP
    WHERE sf_id IS NOT NULL
    UNION
    SELECT DISTINCT old_sf_id AS sf_id
    FROM SENTINELONE_SF_ID_RESOLVER
    WHERE old_sf_id IS NOT NULL
),

partner_map_monthly AS (
    -- Monthly seed sf_ids are canonicalized at query time (the seed itself is
    -- raw from XLSX and may still hold merged-away sf_ids). Date-aware:
    -- only apply the canonical id for billing months on/after the merge
    -- effective month; earlier months keep the seed's original sf_id so the
    -- partner map reflects who was actually mapped at the time of billing.
    SELECT
        TRIM(s.VENDOR_PARTNER_NAME) AS partner_name,
        s.BILLING_MONTH::DATE AS billing_month,
        COALESCE(
            CASE WHEN s.BILLING_MONTH::DATE >= mr.merge_effective_month
                 THEN mr.canonical_sf_id END,
            s.sf_id
        ) AS sf_id,
        s.zuora_name
    FROM RECON_PARTNER_MAP s
    LEFT JOIN merged_account_resolver mr
        ON mr.old_sf_id = s.sf_id
    WHERE s.sf_id IS NOT NULL
      AND s.PARTNER_NAME IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY s.PARTNER_NAME::DATE, UPPER(TRIM(s.PARTNER_NAME))
        ORDER BY s.zuora_name DESC NULLS LAST
    ) = 1
),

parent_rollup AS (
    SELECT DISTINCT
        a_child.cws_account_unique_identifier_c AS child_sf_id,
        a_parent.cws_account_unique_identifier_c AS parent_sf_id,
        p.parent_name
    FROM analytics.dbo_seed_files.seed__partner_parent_child_relationships p
    LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__account a_child
        ON a_child.id = p.sf_account_id
       AND a_child.is_deleted = FALSE
    LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__account a_parent
        ON a_parent.id = p.parent_id
       AND a_parent.is_deleted = FALSE
    WHERE a_child.cws_account_unique_identifier_c IS NOT NULL
      AND a_parent.cws_account_unique_identifier_c IS NOT NULL
      AND a_child.cws_account_unique_identifier_c <> a_parent.cws_account_unique_identifier_c
),

vendor_usage_normalized AS (
    SELECT
        u.BILLING_MONTH::DATE AS billing_month,
        u.VENDOR_PARTNER_NAME,
        COALESCE(pm_month.sf_id, pm_exact.sf_id, pm_accent.sf_id, pm_prefix.sf_id) AS raw_sf_id,
        TRIM(u.VENDOR_PRODUCT_SKU) AS source_vendor_product,
        NULL::VARCHAR AS entity,
        NULL::VARCHAR AS retention_desc,
        TRIM(u.VENDOR_PRODUCT_SKU) AS vendor_product,
        CASE
            WHEN pm_month.sf_id IS NOT NULL THEN 'MONTHLY_PARTNER_MAP'
            WHEN pm_exact.sf_id IS NOT NULL THEN 'PARTNER_MAP_EXACT'
            WHEN pm_accent.sf_id IS NOT NULL THEN 'PARTNER_MAP_ACCENT_NORM'
            WHEN pm_prefix.sf_id IS NOT NULL THEN 'PARTNER_MAP_PREFIX'
            ELSE 'UNMAPPED'
        END AS partner_match_method,
        SUM(u.QUANTITY) AS quantity,
        COUNT(*) AS source_row_count
    FROM SENTINELONE_USAGE u
    LEFT JOIN partner_map_monthly pm_month
        ON UPPER(pm_month.partner_name) = UPPER(TRIM(u.VENDOR_PARTNER_NAME))
       AND pm_month.billing_month = u.BILLING_MONTH::DATE
    LEFT JOIN partner_map pm_exact
        ON pm_month.sf_id IS NULL
       AND UPPER(pm_exact.partner_name) = UPPER(TRIM(u.VENDOR_PARTNER_NAME))
    -- Accent-normalized fallback: translates common diacritics to ASCII so
    -- PARROINFODÃ‰VELOPPEMENT matches PARROINFODEVELOPPEMENT.
    LEFT JOIN partner_map pm_accent
        ON pm_month.sf_id IS NULL
       AND pm_exact.sf_id IS NULL
       AND UPPER(TRANSLATE(pm_accent.partner_name,
           'Ã Ã¡Ã¢Ã£Ã¤Ã¥Ã¨Ã©ÃªÃ«Ã¬Ã­Ã®Ã¯Ã²Ã³Ã´ÃµÃ¶Ã¹ÃºÃ»Ã¼Ã½Ã€ÃÃ‚ÃƒÃ„Ã…ÃˆÃ‰ÃŠÃ‹ÃŒÃÃŽÃÃ’Ã“Ã”Ã•Ã–Ã™ÃšÃ›ÃœÃ',
           'aaaaaaeeeeiiiiooooouuuuyAAAAAAEEEEIIIIOOOOOUUUUY'))
           = UPPER(TRIM(u.VENDOR_PARTNER_NAME))
    LEFT JOIN partner_map pm_prefix
        ON pm_month.sf_id IS NULL
       AND pm_exact.sf_id IS NULL
       AND pm_accent.sf_id IS NULL
       AND UPPER(pm_prefix.partner_name) = UPPER(TRIM(SPLIT_PART(u.VENDOR_PARTNER_NAME, '-', 1)))
       AND LENGTH(TRIM(SPLIT_PART(u.VENDOR_PARTNER_NAME, '-', 1))) >= 3
       AND CONTAINS(u.VENDOR_PARTNER_NAME, '-')
    WHERE u.BILLING_MONTH >= '2026-01-01'
      AND COALESCE(u.QUANTITY, 0) > 0
      AND u.VENDOR_PARTNER_NAME IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
),

vendor_usage_mapped_pre AS (
    -- 2026-08-12: RSO and Forensics map to the same REMOTEOPS group. When a
    -- partner has both at the same qty (e.g. NOVATECH: RSO=11808, Forensics=11808),
    -- they represent the SAME seats (Forensics includes RSO scripting). Suppress
    -- the RSO row to avoid double-counting. Partners with RSO-only (no Forensics)
    -- keep their RSO rows â€” those are the free tier.
    WITH vendor_deduped AS (
        SELECT v.*,
            COALESCE(m.sku_match_group, 'UNMAPPED_VENDOR_PRODUCT') AS sku_match_group_resolved
        FROM vendor_usage_normalized v
        LEFT JOIN vendor_product_group_map m
            ON m.vendor_product_key = UPPER(TRIM(v.vendor_product))
            OR UPPER(TRIM(m.sku_match_group)) = UPPER(TRIM(v.vendor_product))
    ),
    forensics_partners AS (
        -- Partners that have Forensics usage in a given month
        SELECT DISTINCT billing_month, raw_sf_id
        FROM vendor_deduped
        WHERE UPPER(TRIM(vendor_product)) = 'FORENSICS'
          AND quantity > 0
    )
    SELECT
        v.billing_month,
        v.raw_sf_id,
        v.vendor_partner_name,
        v.sku_match_group_resolved AS sku_match_group,
        v.vendor_product,
        v.source_vendor_product,
        v.entity,
        v.retention_desc,
        v.partner_match_method,
        SUM(v.quantity) AS vendor_quantity,
        SUM(v.source_row_count) AS vendor_row_count
    FROM vendor_deduped v
    LEFT JOIN forensics_partners fp
        ON fp.billing_month = v.billing_month AND fp.raw_sf_id = v.raw_sf_id
    WHERE NOT (
        -- Suppress RSO rows when Forensics exists for same partner/month
        UPPER(TRIM(v.vendor_product)) = 'RSO'
        AND fp.raw_sf_id IS NOT NULL
    )
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
),

billing_category_map AS (
    SELECT DISTINCT UPPER(TRIM(product_sku)) AS product_sku, billing_category
    FROM SENTINELONE_CHARGE_TO_GROUP
),

zuora_billing AS (
    -- Date-aware merge: only collapse onto canonical id for billing months
    -- on/after the merge effective month. Pre-merge months keep the sf_id
    -- that was actually billed (that's historical truth).
    SELECT
        COALESCE(
            CASE WHEN z.BILLING_MONTH::DATE >= mr.merge_effective_month
                 THEN mr.canonical_sf_id END,
            z.SFDC_ACCOUNT_NUMBER
        ) AS sf_id,
        z.BILLING_MONTH::DATE AS billing_month,
        m.sku_match_group,
        ARRAY_AGG(DISTINCT z.PRODUCT_SKU) WITHIN GROUP (ORDER BY z.PRODUCT_SKU) AS zuora_skus,
        ARRAY_AGG(DISTINCT z.INVOICE_NUMBER) WITHIN GROUP (ORDER BY z.INVOICE_NUMBER) AS zuora_invoice_numbers,
        SUM(COALESCE(z.QUANTITY, 0)) AS zuora_quantity,
        AVG(NULLIF(z.UNIT_PRICE * COALESCE(fx.budget_ex_rate, 1), 0)) AS zuora_unit_price,
        SUM(COALESCE(z.CHARGE_AMOUNT, 0) * COALESCE(fx.budget_ex_rate, 1)) AS zuora_amount,
        -- MDR decomposition: what portion of billing comes from MDR-bundled SKUs
        SUM(CASE WHEN bc.billing_category = 'MDR_BUNDLE'
            THEN COALESCE(z.CHARGE_AMOUNT, 0) * COALESCE(fx.budget_ex_rate, 1) ELSE 0 END) AS mdr_bundle_amount,
        SUM(CASE WHEN bc.billing_category = 'MDR_BUNDLE'
            THEN COALESCE(z.QUANTITY, 0) ELSE 0 END) AS mdr_bundle_quantity,
        -- Dominant billing category for this (sf_id, month, sku_group) grain
        MODE(bc.billing_category) AS dominant_billing_category
    FROM ANALYTICS_DEV.DBT_NFOLD.ZUORA_THIRD_PARTY_RECON_BASE z
    LEFT JOIN merged_account_resolver mr
        ON mr.old_sf_id = z.SFDC_ACCOUNT_NUMBER
    JOIN cw_sku_group_map m
        ON m.cw_sku = UPPER(TRIM(z.PRODUCT_SKU))
    LEFT JOIN fx_rates fx
        ON fx.currency_id = UPPER(z.ACCOUNT_CURRENCY)
    LEFT JOIN billing_category_map bc
        ON bc.product_sku = UPPER(TRIM(z.PRODUCT_SKU))
    WHERE z.VENDOR_NAME = 'SentinelOne'
      AND z.INVOICE_STATUS = 'Posted'
      AND z.INVOICE_SOURCE = 'BillRun'
      AND z.BILLING_MONTH >= '2026-01-01'
      AND z.SFDC_ACCOUNT_NUMBER IS NOT NULL
    GROUP BY 1, 2, 3
),

marketplace_billing AS (
    -- Date-aware merge: see zuora_billing above.
    SELECT
        COALESCE(
            CASE WHEN DATE_TRUNC('MONTH', c.month_year)::DATE >= mr.merge_effective_month
                 THEN mr.canonical_sf_id END,
            a.cws_account_unique_identifier_c
        ) AS sf_id,
        DATE_TRUNC('MONTH', c.month_year)::DATE AS billing_month,
        m.sku_match_group,
        ARRAY_AGG(DISTINCT c.prod_sku) WITHIN GROUP (ORDER BY c.prod_sku) AS marketplace_skus,
        ARRAY_AGG(DISTINCT c.ns_transaction_id) WITHIN GROUP (ORDER BY c.ns_transaction_id) AS marketplace_transaction_ids,
        SUM(c.ns_usage_qty) AS marketplace_quantity,
        SUM(c.arr_budget_rate / 12) AS marketplace_amount
    FROM ANALYTICS.DBO.CARR__ALL_TRANSACTIONS c
    LEFT JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT a
        ON a.id = c.acc_id
       AND a.is_deleted = FALSE
    LEFT JOIN merged_account_resolver mr
        ON mr.old_sf_id = a.cws_account_unique_identifier_c
    JOIN cw_sku_group_map m
        ON m.cw_sku = UPPER(TRIM(c.prod_sku))
    WHERE c.transaction_source IN (
            'Netsuite Evergreen Usage CW',
            'Netsuite Evergreen Usage',
            'Netsuite Evergreen Credit Memos CW',
            'Netsuite Evergreen Credit Memos'
          )
      AND DATE_TRUNC('MONTH', c.month_year)::DATE >= '2026-01-01'
      AND a.cws_account_unique_identifier_c IS NOT NULL
    GROUP BY 1, 2, 3
),

product_billing AS (
    SELECT
        COALESCE(z.sf_id, m.sf_id) AS sf_id,
        COALESCE(z.billing_month, m.billing_month) AS billing_month,
        COALESCE(z.sku_match_group, m.sku_match_group) AS sku_match_group,
        z.zuora_skus,
        z.zuora_invoice_numbers,
        m.marketplace_skus,
        m.marketplace_transaction_ids,
        COALESCE(z.zuora_quantity, 0) AS zuora_quantity,
        z.zuora_unit_price,
        COALESCE(z.zuora_amount, 0) AS zuora_amount,
        COALESCE(m.marketplace_quantity, 0) AS marketplace_quantity,
        COALESCE(m.marketplace_amount, 0) AS marketplace_amount,
        COALESCE(z.zuora_quantity, 0) + COALESCE(m.marketplace_quantity, 0) AS total_billing_quantity,
        COALESCE(z.zuora_amount, 0) + COALESCE(m.marketplace_amount, 0) AS total_billing_amount,
        COALESCE(z.mdr_bundle_amount, 0) AS mdr_bundle_amount,
        COALESCE(z.mdr_bundle_quantity, 0) AS mdr_bundle_quantity,
        z.dominant_billing_category
    FROM zuora_billing z
    FULL OUTER JOIN marketplace_billing m
        ON m.sf_id = z.sf_id
       AND m.billing_month = z.billing_month
       AND m.sku_match_group = z.sku_match_group
),

vendor_usage_mapped AS (
    SELECT
        v.billing_month,
        COALESCE(
            IFF(
                v.raw_sf_id IS NOT NULL
                AND COALESCE(child_billing.total_billing_quantity, 0) = 0
                AND COALESCE(parent_billing.total_billing_quantity, 0) > 0,
                pr.parent_sf_id,
                NULL
            ),
            v.raw_sf_id
        ) AS sf_id,
        v.sku_match_group,
        -- Kept for audit/debug: every raw vendor label that rolled into this
        -- (sf_id, month, sku_group) grain. NOT surfaced to the app.
        LISTAGG(DISTINCT v.vendor_partner_name, ' | ') WITHIN GROUP (ORDER BY v.vendor_partner_name) AS vendor_partner_name_aliases,
        LISTAGG(DISTINCT v.vendor_product, ' | ') WITHIN GROUP (ORDER BY v.vendor_product) AS vendor_product,
        LISTAGG(DISTINCT v.source_vendor_product, ' | ') WITHIN GROUP (ORDER BY v.source_vendor_product) AS source_vendor_products,
        LISTAGG(DISTINCT NULLIF(v.retention_desc, ''), ' | ') WITHIN GROUP (ORDER BY NULLIF(v.retention_desc, '')) AS retention_descs,
        SUM(v.vendor_quantity) AS vendor_quantity,
        SUM(v.vendor_row_count) AS vendor_row_count,
        LISTAGG(DISTINCT
            CASE
                WHEN v.raw_sf_id IS NULL THEN 'UNMAPPED'
                WHEN COALESCE(child_billing.total_billing_quantity, 0) = 0
                 AND COALESCE(parent_billing.total_billing_quantity, 0) > 0 THEN 'VENDOR_PORTAL|PARENT_ROLLUP'
                ELSE v.partner_match_method
            END,
            ' | '
        ) WITHIN GROUP (ORDER BY
            CASE
                WHEN v.raw_sf_id IS NULL THEN 'UNMAPPED'
                WHEN COALESCE(child_billing.total_billing_quantity, 0) = 0
                 AND COALESCE(parent_billing.total_billing_quantity, 0) > 0 THEN 'VENDOR_PORTAL|PARENT_ROLLUP'
                ELSE v.partner_match_method
            END
        ) AS partner_match_methods
    FROM vendor_usage_mapped_pre v
    LEFT JOIN parent_rollup pr
        ON pr.child_sf_id = v.raw_sf_id
    LEFT JOIN product_billing child_billing
        ON child_billing.sf_id = v.raw_sf_id
       AND child_billing.billing_month = v.billing_month
       AND child_billing.sku_match_group = v.sku_match_group
    LEFT JOIN product_billing parent_billing
        ON parent_billing.sf_id = pr.parent_sf_id
       AND parent_billing.billing_month = v.billing_month
       AND parent_billing.sku_match_group = v.sku_match_group
    GROUP BY 1, 2, 3
),

trt_endpoint AS (
    SELECT
        sf_id,
        billing_month,
        SUM(trt_agents_avg) AS trt_agents
    FROM SENTINELONE_TRT_USAGE_MONTHLY
    WHERE sf_id NOT ILIKE 'UNMAPPED_%'
      AND s1_group IN ('COMPLETE', 'CONTROL', 'ENDPOINT')
    GROUP BY 1, 2
),

product_joined AS (
    SELECT
        COALESCE(v.sf_id, b.sf_id) AS sf_id,
        COALESCE(v.billing_month, b.billing_month) AS billing_month,
        COALESCE(v.sku_match_group, b.sku_match_group) AS sku_match_group,
        -- Prefer canonical Salesforce-account partner name (single row per sf_id
        -- from partner_canonical_name). Fall back to the audit alias LISTAGG
        -- only when the account isn't in the partner map (unmapped rows).
        COALESCE(pcn.canonical_partner_name, v.vendor_partner_name_aliases) AS vendor_partner_name,
        v.vendor_partner_name_aliases,
        COALESCE(v.vendor_product, gm.mapped_vendor_products, b.sku_match_group) AS vendor_product,
        v.source_vendor_products,
        v.retention_descs,
        gm.mapped_cw_skus AS cw_skus,
        gm.mapped_cw_sku_count,
        b.zuora_skus,
        b.zuora_invoice_numbers,
        b.marketplace_skus,
        b.marketplace_transaction_ids,
        CASE
            WHEN COALESCE(b.zuora_quantity, 0) > 0 AND COALESCE(b.marketplace_quantity, 0) > 0 THEN 'ZUORA_AND_MARKETPLACE'
            WHEN COALESCE(b.zuora_quantity, 0) > 0 THEN 'ZUORA_ONLY'
            WHEN COALESCE(b.marketplace_quantity, 0) > 0 THEN 'MARKETPLACE_ONLY'
            ELSE 'NO_BILLING_SOURCE'
        END AS billing_source_mix,
        COALESCE(v.vendor_quantity, 0) AS vendor_quantity,
        COALESCE(b.zuora_quantity, 0) AS zuora_quantity,
        b.zuora_unit_price,
        COALESCE(b.zuora_amount, 0) AS zuora_amount,
        COALESCE(b.marketplace_quantity, 0) AS marketplace_quantity,
        COALESCE(b.marketplace_amount, 0) AS marketplace_amount,
        COALESCE(b.total_billing_quantity, 0) AS total_billing_quantity,
        COALESCE(b.total_billing_amount, 0) AS total_billing_amount,
        COALESCE(b.mdr_bundle_amount, 0) AS mdr_bundle_amount,
        COALESCE(b.mdr_bundle_quantity, 0) AS mdr_bundle_quantity,
        b.dominant_billing_category,
        COALESCE(v.vendor_row_count, 0) AS vendor_row_count,
        COALESCE(v.partner_match_methods, 'BILLING_ONLY|PARTNER_MAP') AS partner_match_methods,
        IFF(
            COALESCE(v.sku_match_group, b.sku_match_group) IN ('COMPLETE', 'CONTROL'),
            COALESCE(t.trt_agents, 0),
            0
        ) AS trt_agents
    FROM vendor_usage_mapped v
    FULL OUTER JOIN product_billing b
        ON b.sf_id = v.sf_id
       AND b.billing_month = v.billing_month
       AND b.sku_match_group = v.sku_match_group
    LEFT JOIN sku_group_map gm
        ON gm.sku_match_group = COALESCE(v.sku_match_group, b.sku_match_group)
    LEFT JOIN mapped_sf_ids ms
        ON ms.sf_id = b.sf_id
    LEFT JOIN partner_canonical_name pcn
        ON pcn.sf_id = COALESCE(v.sf_id, b.sf_id)
    LEFT JOIN trt_endpoint t
        ON t.sf_id = COALESCE(v.sf_id, b.sf_id)
       AND t.billing_month = COALESCE(v.billing_month, b.billing_month)
    WHERE v.sf_id IS NOT NULL
       OR v.partner_match_methods = 'UNMAPPED'
       OR (
            v.sf_id IS NULL
            AND b.sf_id IS NOT NULL
            AND ms.sf_id IS NOT NULL
            AND COALESCE(b.total_billing_quantity, 0) >= 25
       )
),

detail_pre AS (
    SELECT
        pj.billing_month,
        pj.sf_id,
        pj.vendor_partner_name,
        pj.sku_match_group,
        pj.vendor_product,
        pj.source_vendor_products,
        pj.retention_descs,
        pj.cw_skus,
        pj.zuora_skus,
        pj.zuora_invoice_numbers,
        pj.marketplace_skus,
        pj.marketplace_transaction_ids,
        pj.billing_source_mix,
        pj.vendor_quantity,
        -- Vendor invoice pricing (loaded from
        -- SENTINELONE_SKU_INVOICE_RATES via seed CSV and merged into
        -- (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'SentinelOne')). One canonical price per sku_match_group.
        -- Rows whose SKU has no invoice line (e.g. CORE) fall back to zero
        -- so the pipeline never crashes, but the app can show them as
        -- unpriced.
        sgir.vendor_invoice_unit_price AS vendor_unit_price,
        COALESCE(sgir.vendor_invoice_unit_price, 0) * pj.vendor_quantity AS vendor_amount,
        sgir.vendor_invoice_sku,
        sgir.vendor_invoice_rate_source,
        pj.zuora_quantity,
        pj.zuora_unit_price,
        pj.zuora_amount,
        pj.marketplace_quantity,
        pj.marketplace_amount,
        pj.total_billing_quantity,
        pj.total_billing_amount / NULLIF(pj.total_billing_quantity, 0) AS total_billing_unit_price,
        pj.total_billing_amount,
        pj.total_billing_quantity - pj.vendor_quantity AS qty_delta,
        ABS(pj.total_billing_quantity - pj.vendor_quantity) AS abs_qty_delta,
        -- amount_delta is now the DIRECT dollar exposure between what
        -- SentinelOne charged and what CW billed:
        --   amount_delta = total_billing_amount - vendor_amount
        --                = (bill_qty * cw_price) - (vendor_qty * s1_price)
        -- Positive means CW over-billed relative to the vendor invoice,
        -- negative means CW under-billed. When invoice pricing is not
        -- available for a SKU (CORE / REMOTEOPS), the vendor_amount is zero
        -- and amount_delta falls back to the seat-imputed exposure so the
        -- Est. $ Impact stays comparable across categories.
        CASE
            WHEN sgir.vendor_invoice_unit_price IS NOT NULL
                THEN pj.total_billing_amount
                     - (sgir.vendor_invoice_unit_price * pj.vendor_quantity)
            WHEN pj.total_billing_quantity IS NULL OR pj.total_billing_quantity = 0
                THEN 0::FLOAT
            ELSE (pj.total_billing_quantity - pj.vendor_quantity)
                 * (pj.total_billing_amount / pj.total_billing_quantity)
        END AS amount_delta,
        CASE
            WHEN sgir.vendor_invoice_unit_price IS NOT NULL
                THEN ABS(
                    pj.total_billing_amount
                    - (sgir.vendor_invoice_unit_price * pj.vendor_quantity)
                )
            WHEN pj.total_billing_quantity IS NULL OR pj.total_billing_quantity = 0
                THEN 0::FLOAT
            ELSE ABS(
                (pj.total_billing_quantity - pj.vendor_quantity)
                * (pj.total_billing_amount / pj.total_billing_quantity)
            )
        END AS abs_amount_delta,
        (pj.zuora_quantity > 0 AND pj.marketplace_quantity > 0) AS duplicate_billing_flag,
        FALSE AS marketplace_timing_flag,
        0::FLOAT AS marketplace_timing_quantity,
        pj.vendor_row_count,
        pj.partner_match_methods,
        'SENTINELONE_USAGE_PRODUCT|SKU_MAP_V5|ZUORA_PLUS_MARKETPLACE' AS sku_mapping_sources,
        pj.trt_agents,
        pj.mdr_bundle_amount,
        pj.mdr_bundle_quantity,
        pj.dominant_billing_category,
        CASE
            -- ---------- Structural / data-issue classifications ----------
            -- Root cause is data flow, not billing arithmetic. Investigate the
            -- pipeline (mapping, SKU catalog, contract) before treating as a
            -- billing discrepancy.
            WHEN pj.sf_id IS NULL THEN 'PARTNER_MAPPING_REQUIRED'
            -- Known SentinelOne add-on features that CW does not rebill under a
            -- distinct SKU. Vendor telemetry is real (partners use these features)
            -- but there is no CW billing counterpart, so treat as informational
            -- rather than a reconciliation variance.
            -- VENDOR_ADDON_NO_CW_SKU: S1 invoices for these products but CW has
            -- NO corresponding rebill SKU in the catalog at all. True catalog gap.
            WHEN pj.vendor_quantity > 0
                 AND pj.total_billing_quantity = 0
                 AND pj.sku_match_group IN (
                    'CLOUD_FUNNEL','PURPLE_AI','RANGER_INSIGHTS',
                    'RANGER_AD','SINGULARITY_IDENTITY','THREAT_INTELLIGENCE','CORE'
                 ) THEN 'VENDOR_ADDON_NO_CW_SKU'
            -- NOTE: REMOTEOPS (Forensics/RSO) intentionally NOT listed above.
            -- CW HAS the SKUs (SP-RSO-ND-T1-C at $0.60 retail). S1 charges $0.30
            -- as "RemoteOps Forensics". They are counterparts â€” same product.
            -- Partners without active subscriptions flow through to the standard
            -- structural flags below (STRUCTURAL_VENDOR_ONLY_*) which correctly
            -- identifies them as needing subscription activation, not a catalog fix.
            WHEN pj.vendor_quantity > 0
                 AND (pj.sku_match_group = 'UNMAPPED_VENDOR_PRODUCT'
                      OR COALESCE(pj.mapped_cw_sku_count, 0) = 0)
                 THEN 'VENDOR_PRODUCT_NO_CW_SKU'
            WHEN pj.zuora_quantity > 0 AND pj.marketplace_quantity > 0
                 THEN 'DUPLICATE_BILLING'

            -- STRUCTURAL_BILLING_ONLY: CW bills material qty (>=50) but the
            -- vendor XLSX effectively shows nothing (<=5% of what CW billed
            -- OR literally zero). Symptom of SentinelOne-side site-attribution
            -- errors (e.g. Intelica case: CW bills 4,400 Complete/mo, vendor
            -- file shows 1). Not a billing dispute; audit vendor attribution.
            WHEN pj.total_billing_quantity >= 50
                 AND (
                    pj.vendor_quantity = 0
                    OR pj.vendor_quantity <= pj.total_billing_quantity * 0.05
                 )
                 THEN 'STRUCTURAL_BILLING_ONLY'

            -- STRUCTURAL_VENDOR_ONLY: vendor bills material qty (>=50) but CW
            -- bills nothing / near-zero. If TRT confirms usage, tag as
            -- TRT-supported so Finance can bill; otherwise it's a contract or
            -- rebill-catalog gap.
            WHEN pj.vendor_quantity >= 50
                 AND (
                    pj.total_billing_quantity = 0
                    OR pj.total_billing_quantity <= pj.vendor_quantity * 0.05
                 )
                 AND pj.trt_agents > 0
                 THEN 'STRUCTURAL_VENDOR_ONLY_TRT_CONFIRMED'
            WHEN pj.vendor_quantity >= 50
                 AND (
                    pj.total_billing_quantity = 0
                    OR pj.total_billing_quantity <= pj.vendor_quantity * 0.05
                 )
                 THEN 'STRUCTURAL_VENDOR_ONLY_NO_CONTRACT'

            -- Legacy fallback for edge cases where one side is zero but the
            -- other is below the structural threshold (<50). Kept for
            -- completeness so nothing falls through unclassified.
            WHEN pj.total_billing_quantity = 0 AND pj.trt_agents > 0
                 THEN 'TRT_VENDOR_USAGE_NOT_BILLED'
            WHEN pj.total_billing_quantity = 0
                 THEN 'NO_BILLING_NO_HISTORY'
            WHEN pj.vendor_quantity = 0
                 THEN 'BILLING_ONLY_NO_VENDOR_USAGE'

            -- ---------- Billing / quantity classifications ----------
            -- Both sides have material quantity; this is a genuine reconciliation
            -- delta, not a data-flow problem.
            WHEN ABS(pj.total_billing_quantity - pj.vendor_quantity)
                 <= GREATEST(5, pj.vendor_quantity * 0.02)
                 THEN 'CLEAR'
            WHEN ABS(pj.total_billing_quantity - pj.vendor_quantity)
                 <= GREATEST(25, pj.vendor_quantity * 0.05)
                 THEN 'MINOR_DRIFT'
            WHEN pj.total_billing_quantity > pj.vendor_quantity
                 AND ABS(pj.total_billing_quantity - pj.vendor_quantity)
                     > pj.vendor_quantity * 0.25
                 THEN 'MATERIAL_OVER_VENDOR'
            WHEN pj.total_billing_quantity < pj.vendor_quantity
                 AND ABS(pj.total_billing_quantity - pj.vendor_quantity)
                     > pj.vendor_quantity * 0.25
                 THEN 'MATERIAL_UNDER_VENDOR'
            WHEN pj.total_billing_quantity > pj.vendor_quantity
                 THEN 'BILLING_DIFFERENTIAL_OVER'
            WHEN pj.total_billing_quantity < pj.vendor_quantity
                 THEN 'BILLING_DIFFERENTIAL_UNDER'
            ELSE 'REVIEW_EXCEPTION'
        END AS outcome_flag
    FROM product_joined pj
    LEFT JOIN sku_group_invoice_rate sgir
        ON sgir.sku_match_group = pj.sku_match_group
)

SELECT
    'SentinelOne'::VARCHAR AS vendor,
    billing_month,
    sf_id,
    vendor_partner_name,
    vendor_product,
    sku_match_group,
    source_vendor_products,
    retention_descs,
    cw_skus,
    zuora_skus,
    marketplace_skus,
    billing_source_mix,
    ARRAY_TO_STRING(zuora_invoice_numbers, ' | ') AS zuora_inv,
    ARRAY_TO_STRING(marketplace_transaction_ids, ' | ') AS mp_inv,
    vendor_quantity,
    vendor_unit_price,
    vendor_amount,
    vendor_invoice_sku,
    vendor_invoice_rate_source,
    zuora_quantity,
    zuora_unit_price,
    zuora_amount,
    marketplace_quantity,
    marketplace_amount,
    total_billing_quantity,
    total_billing_unit_price,
    total_billing_amount,
    qty_delta,
    abs_qty_delta,
    amount_delta,
    abs_amount_delta,
    duplicate_billing_flag,
    marketplace_timing_flag,
    marketplace_timing_quantity,
    vendor_row_count AS vendor_source_row_count,
    partner_match_methods,
    sku_mapping_sources,
    -- Contract cost analysis (populated from CW internal product catalog rates)
    vendor_quantity AS contract_cost_basis_quantity,
    ROUND(vendor_quantity * COALESCE(vendor_unit_price, 0), 2) AS contract_cost_basis_amount,
    vendor_unit_price AS contract_cost_rate,
    CASE
        WHEN total_billing_quantity > 0 AND vendor_unit_price IS NOT NULL AND vendor_unit_price > 0
            THEN ROUND(total_billing_unit_price - vendor_unit_price, 4)
        ELSE NULL
    END AS billing_vs_cost_delta_per_seat,
    CASE
        WHEN total_billing_amount > 0 AND vendor_amount > 0
            THEN ROUND(total_billing_amount - vendor_amount, 2)
        ELSE NULL
    END AS billing_vs_cost_dollar_impact,
    CASE
        WHEN total_billing_amount > 0 AND vendor_amount > 0
            THEN ROUND((total_billing_amount - vendor_amount) / NULLIF(vendor_amount, 0) * 100, 1)
        ELSE NULL
    END AS billing_vs_cost_pct,
    CASE
        WHEN total_billing_amount > 0 AND vendor_amount > 0 AND total_billing_amount < vendor_amount
            THEN 'BELOW_COST_DISCOUNT'
        WHEN total_billing_amount > 0 AND vendor_amount > 0 AND total_billing_amount >= vendor_amount
            THEN 'ABOVE_COST'
        ELSE NULL
    END AS contract_price_flag,
    CASE
        WHEN total_billing_amount > 0 AND vendor_amount > 0
             AND total_billing_amount < vendor_amount
             AND (vendor_amount - total_billing_amount) > 50
            THEN TRUE
        ELSE FALSE
    END AS material_below_cost_flag,
    sku_mapping_sources AS contract_rate_source_docs,
    CURRENT_TIMESTAMP() AS recon_run_ts,
    outcome_flag,
    CASE
        WHEN outcome_flag = 'CLEAR' THEN 'CLEAR: vendor quantity within 2% (or 5 endpoints) of CW billing. No action.'
        WHEN outcome_flag = 'MINOR_DRIFT' THEN 'Minor drift: 2-5% quantity delta (or <=25 endpoints). Below operational review threshold. No action.'
        WHEN outcome_flag = 'PARTNER_MAPPING_REQUIRED' THEN 'Vendor usage exists but no Salesforce ID was resolved from the governed SentinelOne partner map. Partner Ops: add to map.'
        WHEN outcome_flag = 'VENDOR_ADDON_NO_CW_SKU' THEN 'Vendor add-on (Cloud Funnel / Purple AI / Ranger Insights / Ranger AD / Singularity Identity / Threat Intelligence / Core) is invoiced by S1 at real cost but CW has NO rebill SKU in catalog. TRUE REVENUE LEAKAGE. Product/Catalog: create rebill SKU.'
        WHEN outcome_flag = 'VENDOR_PRODUCT_NO_CW_SKU' THEN 'Vendor product exists in usage but has no active CW billing SKU in RECON_SKU_MAP (VENDOR=SentinelOne). Product/Catalog: map the SKU.'
        WHEN outcome_flag = 'DUPLICATE_BILLING' THEN 'Same SentinelOne SKU group billed through both Zuora and Marketplace in the same account/month. Billing Ops: dedupe source.'
        WHEN outcome_flag = 'STRUCTURAL_BILLING_ONLY' THEN 'CW bills material qty for this partner but SentinelOne vendor file reports ~0. Suggests SentinelOne-side site attribution error (partner-hosted sites reported under end-customer names). Data/vendor issue, NOT a billing dispute. Recon Team: escalate to SentinelOne for site-attribution audit.'
        WHEN outcome_flag = 'STRUCTURAL_VENDOR_ONLY_TRT_CONFIRMED' THEN 'Vendor bills material qty and TRT internal metering confirms real usage, but CW has no Zuora/Marketplace bill. Finance: bill this partner (TRT proves consumption).'
        WHEN outcome_flag = 'STRUCTURAL_VENDOR_ONLY_NO_CONTRACT' THEN 'Vendor bills material qty but no CW billing and no TRT signal. Likely no active Zuora contract. Sales/Ops: partner outreach; onboard to a rebill contract.'
        WHEN outcome_flag = 'TRT_VENDOR_USAGE_NOT_BILLED' THEN 'Vendor endpoint usage present with TRT support, but no Zuora/Marketplace billing. Finance: bill the TRT-supported usage.'
        WHEN outcome_flag = 'NO_BILLING_NO_HISTORY' THEN 'Vendor product usage exists with no Zuora/Marketplace billing and no TRT support. Sales/Ops: no active contract found.'
        WHEN outcome_flag = 'BILLING_ONLY_NO_VENDOR_USAGE' THEN 'CW billed non-material seats with no matching vendor usage (below structural threshold). Ops: review small stale-quantity subscriptions.'
        WHEN outcome_flag = 'MATERIAL_OVER_VENDOR' THEN 'CW billing exceeds vendor usage by >25%. Likely multi-tenant consolidation (Zuora bills multiple S1 tenants under one account) or vendor data lag. Finance/Vendor Ops: verify tenant count with SentinelOne.'
        WHEN outcome_flag = 'MATERIAL_UNDER_VENDOR' THEN 'Vendor usage exceeds CW billing by >25%. Real under-billing (not a data flow issue). Finance: true-up the partner invoice.'
        WHEN outcome_flag = 'BILLING_DIFFERENTIAL_OVER' THEN 'CW billing exceeds vendor usage by 5-25%. Normal recon workflow: verify seat count with partner and vendor.'
        WHEN outcome_flag = 'BILLING_DIFFERENTIAL_UNDER' THEN 'Vendor usage exceeds CW billing by 5-25%. Normal recon workflow: verify partner seat count and true-up if needed.'
        ELSE 'Review SentinelOne exception.'
    END AS investigation_reason,
    outcome_flag NOT IN ('CLEAR', 'MINOR_DRIFT') AS billing_action_required,
    -- Vendor vs contract rate analysis: compares what S1 actually charged per seat
    -- (vendor_unit_price from invoice) vs the governed contract cost rate. Since
    -- the pipeline applies the same invoice-derived rate to all rows within a
    -- sku_match_group, this will only flag if the invoice rate differs from the
    -- contract (e.g., tier changes or rate renegotiations). Currently S1 rates
    -- are stable at contracted levels (Control=$0.72, Complete=$1.01, etc.).
    NULL::NUMBER AS vendor_vs_contract_delta_per_seat,
    NULL::NUMBER AS vendor_vs_contract_pct,
    NULL::VARCHAR AS vendor_vs_contract_flag,
    NULL::NUMBER AS vendor_vs_contract_dollar_impact,
    -- MDR bundling decomposition (2026-08-12)
    dominant_billing_category AS billing_category,
    mdr_bundle_amount,
    mdr_bundle_quantity,
    CASE
        WHEN total_billing_amount > 0 AND mdr_bundle_amount > 0
            THEN total_billing_amount - mdr_bundle_amount
        ELSE total_billing_amount
    END AS standalone_license_amount,
    CASE
        WHEN total_billing_amount > 0 AND vendor_amount > 0
            THEN ROUND((total_billing_amount - vendor_amount) / NULLIF(total_billing_amount, 0) * 100, 1)
        ELSE NULL
    END AS gross_margin_pct,
    CASE
        WHEN mdr_bundle_amount > 0 AND vendor_amount > 0
            THEN ROUND(((total_billing_amount - mdr_bundle_amount) - vendor_amount)
                 / NULLIF(total_billing_amount - mdr_bundle_amount, 0) * 100, 1)
        WHEN total_billing_amount > 0 AND vendor_amount > 0
            THEN ROUND((total_billing_amount - vendor_amount) / NULLIF(total_billing_amount, 0) * 100, 1)
        ELSE NULL
    END AS s1_license_margin_pct
FROM detail_pre;

-- =============================================================================
-- ADD-ON AUDIT DETAIL
-- =============================================================================
-- Compatibility table for app/report consumers. Add-ons are now part of the main
-- product-level recon detail; this table is a filtered view of non-endpoint groups.
CREATE OR REPLACE TABLE SENTINELONE_ADDON_RECON_DETAIL AS
SELECT
    billing_month,
    sf_id,
    vendor_partner_name,
    vendor_product,
    sku_match_group AS addon_group,
    zuora_skus AS billing_skus,
    vendor_quantity,
    total_billing_quantity AS billing_quantity,
    qty_delta,
    abs_qty_delta,
    total_billing_amount AS billing_amount,
    CASE
        WHEN outcome_flag = 'CLEAR' THEN 'ADDON_QUANTITY_ALIGNED'
        WHEN outcome_flag = 'BILLING_ONLY_NO_VENDOR_USAGE' THEN 'ADDON_BILLING_NO_VENDOR_USAGE'
        WHEN outcome_flag = 'PARTNER_MAPPING_REQUIRED' THEN 'ADDON_PARTNER_MAPPING_REQUIRED'
        WHEN outcome_flag IN ('NO_BILLING_NO_HISTORY', 'TRT_VENDOR_USAGE_NOT_BILLED', 'VENDOR_PRODUCT_NO_CW_SKU') THEN 'ADDON_VENDOR_USAGE_NO_CONFIRMED_BILLING'
        WHEN outcome_flag = 'BILLING_OVER_VENDOR' THEN 'ADDON_BILLING_OVER_VENDOR'
        WHEN outcome_flag = 'VENDOR_OVER_BILLING' THEN 'ADDON_VENDOR_OVER_BILLING'
        ELSE 'ADDON_REVIEW_EXCEPTION'
    END AS addon_outcome_flag,
    recon_run_ts
FROM SENTINELONE_RECON_DETAIL
WHERE sku_match_group NOT IN ('COMPLETE', 'CONTROL');

-- =============================================================================
-- SUMMARY
-- =============================================================================
CREATE OR REPLACE TABLE SENTINELONE_RECON_SUMMARY AS
SELECT
    BILLING_MONTH,
    COUNT(*) AS total_rows,
    COUNT_IF(outcome_flag = 'CLEAR') AS perfect_match_rows,
    ROUND(COUNT_IF(outcome_flag = 'CLEAR') * 100.0 / NULLIF(COUNT(*), 0), 1) AS perfect_match_pct,
    SUM(abs_qty_delta) AS abs_qty_variance,
    SUM(vendor_quantity)::NUMBER AS total_vendor_seats,
    SUM(zuora_quantity) AS total_zuora_seats,
    SUM(marketplace_quantity) AS total_marketplace_seats,
    SUM(total_billing_quantity) AS total_billing_seats,
    SUM(COALESCE(vendor_amount, 0))::NUMBER AS total_vendor_amount,
    SUM(total_billing_amount) AS total_billing_amount,
    -- MDR decomposition summary
    SUM(COALESCE(mdr_bundle_amount, 0)) AS total_mdr_bundle_amount,
    SUM(total_billing_amount) - SUM(COALESCE(mdr_bundle_amount, 0)) AS total_standalone_license_amount,
    ROUND(SUM(COALESCE(mdr_bundle_amount, 0)) * 100.0 / NULLIF(SUM(total_billing_amount), 0), 1) AS mdr_bundle_pct_of_revenue,
    ROUND((SUM(total_billing_amount) - SUM(COALESCE(vendor_amount, 0)))
        * 100.0 / NULLIF(SUM(total_billing_amount), 0), 1) AS gross_margin_pct,
    ROUND(((SUM(total_billing_amount) - SUM(COALESCE(mdr_bundle_amount, 0))) - SUM(COALESCE(vendor_amount, 0)))
        * 100.0 / NULLIF(SUM(total_billing_amount) - SUM(COALESCE(mdr_bundle_amount, 0)), 0), 1) AS s1_license_only_margin_pct,
    -- Billing category breakdown
    COUNT_IF(billing_category = 'MDR_BUNDLE') AS mdr_bundle_rows,
    COUNT_IF(billing_category = 'STANDALONE') AS standalone_rows,
    COUNT_IF(billing_category = 'SWO_BUNDLE') AS swo_bundle_rows,
    COUNT_IF(billing_category = 'TRIAL_ADMIN') AS trial_admin_rows,
    COUNT_IF(billing_category = 'ADDON') AS addon_rows,
    -- Outcome flag counts
    COUNT_IF(duplicate_billing_flag = TRUE) AS duplicate_billing_rows,
    SUM(IFF(duplicate_billing_flag, vendor_quantity, 0))::NUMBER AS duplicate_billing_vendor_seats,
    SUM(IFF(duplicate_billing_flag, zuora_quantity, 0)) AS duplicate_billing_zuora_seats,
    SUM(IFF(duplicate_billing_flag, marketplace_quantity, 0)) AS duplicate_billing_marketplace_seats,
    SUM(IFF(duplicate_billing_flag, abs_qty_delta, 0)) AS duplicate_billing_abs_qty_variance_impact,
    SUM(IFF(duplicate_billing_flag, abs_amount_delta, 0)) AS duplicate_billing_abs_amount_variance_impact,
    COUNT_IF(outcome_flag = 'PARTNER_MAPPING_REQUIRED') AS unmapped_rows,
    COUNT_IF(outcome_flag = 'STRUCTURAL_VENDOR_ONLY_NO_CONTRACT' AND sku_match_group = 'REMOTEOPS') AS remoteops_unbilled_rows,
    COUNT_IF(outcome_flag IN ('NO_BILLING_NO_HISTORY', 'TRT_VENDOR_USAGE_NOT_BILLED')) AS no_billing_rows,
    COUNT_IF(outcome_flag IN ('BILLING_OVER_VENDOR', 'BILLING_ONLY_NO_VENDOR_USAGE')) AS billing_over_rows,
    COUNT_IF(outcome_flag = 'VENDOR_OVER_BILLING') AS vendor_over_rows,
    COUNT_IF(outcome_flag = 'VENDOR_PRODUCT_NO_CW_SKU') AS vendor_product_no_cw_sku_rows,
    COUNT_IF(outcome_flag = 'VENDOR_ADDON_NO_CW_SKU') AS vendor_addon_no_cw_sku_rows,
    -- Revenue leakage: vendor cost for add-ons that S1 invoices but CW doesn't rebill
    SUM(IFF(outcome_flag = 'VENDOR_ADDON_NO_CW_SKU', COALESCE(vendor_amount, 0), 0))::NUMBER AS addon_revenue_leakage_amount,
    COUNT_IF(contract_price_flag = 'ABOVE_COST') AS contract_above_cost_rows,
    COUNT_IF(contract_price_flag = 'AT_COST') AS contract_at_cost_rows,
    COUNT_IF(contract_price_flag = 'BELOW_COST') AS contract_below_cost_rows,
    COUNT_IF(material_below_cost_flag = TRUE) AS contract_material_below_cost_rows,
    COALESCE(SUM(IFF(contract_price_flag = 'BELOW_COST', billing_vs_cost_dollar_impact, 0)), 0) AS contract_below_cost_loss_dollars,
    COALESCE(SUM(IFF(material_below_cost_flag = TRUE, billing_vs_cost_dollar_impact, 0)), 0) AS contract_material_below_cost_loss_dollars
FROM SENTINELONE_RECON_DETAIL
GROUP BY BILLING_MONTH
ORDER BY BILLING_MONTH;

