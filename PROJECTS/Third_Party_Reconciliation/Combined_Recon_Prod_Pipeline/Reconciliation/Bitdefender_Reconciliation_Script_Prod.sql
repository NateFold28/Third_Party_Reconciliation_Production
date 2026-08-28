-- =============================================================================
-- STEP 2: BITDEFENDER FINAL RECONCILIATION  (Proofpoint-aligned)
-- =============================================================================
-- Vendor side  = BITDEFENDER_USAGE_PROD, materialized from
--                PRODUCT_MANAGEMENT__ROYALTIES (what CW owes Bitdefender; all
--                THIRD_PARTY_TYPEs: Usage + Contract + Marketplace)
-- CW side      = live Zuora source v2 (Posted BillRun) + Manage/NetSuite
--                Evergreen Marketplace (CARR__ALL_TRANSACTIONS)
-- Grain        = (sf_id, billing_month)  -- partner-month (the Bitdefender manual
--                recon DATA-tab grain). Products LISTAGG'd; a primary
--                sku_match_group is resolved per account for the contract overlay.
--
-- KEY CHANGES vs prior build (2026-08-03):
--  1. FX FIX (systemic). Live Zuora source charge_amount / unit_price
--     are NATIVE currency (ACCOUNT_CURRENCY; HOME_CURRENCY is 100% NULL). ~740
--     non-USD Bitdefender rows/mo (CAD/GBP/AUD/EUR) were summed as if USD. Now
--     converted to USD via analytics.dbo_seed_files.seed__fpa_budget_exchange_rates
--     (latest year, join ACCOUNT_CURRENCY, USD=1) before summing.
--  2. MARKETPLACE lane switched from the Royalties 'Marketplace' rows (which are
--     part of the vendor side) to the real Manage/NetSuite-Evergreen billing in
--     CARR__ALL_TRANSACTIONS (BD SKUs 3PARTYONPREMBTCD*/CLDSECGZ). Verified: Zuora
--     BillRun (~390k/mo) already ~= total Royalties vendor (~386k/mo), i.e. Zuora
--     already covers the Contract (3PARTYONPREM) seats, so CARR is a REDUNDANT
--     parallel record for accounts that are also in Zuora (same finding as Auvik
--     GAPS 7b). Summing CARR wholesale would DOUBLE-COUNT. Therefore marketplace
--     contributes to total_billing ONLY where Zuora is absent for that
--     account-month (the pure-Contract accounts previously blind-CLEAR'd), and
--     same-sku_match_group Zuora+Marketplace overlap is flagged DUPLICATE_BILLING
--     (informational; not treated as a variance, since for BD it is legitimate
--     dual-channel billing of different seat pools, not double billing).
--  3. CONTRACT overlay now joins PER sku_match_group (was hardcoded
--     vendor_product='GRAVITYZONE'). Each Royalties product line is mapped to
--     its sku_match_group and priced with that group's contract rate (XDR $0.58,
--     Secure Bundle $1.47, EMAIL $0.82, ATS_EDR $0.90, ...), rates rebuilt from
--     the SIGNED CONTRACT BOOK (Addendum No 9, 2024-04-15) in 00_reference_maps.
--     contract_cost_basis_amount = SUM over products of (qty x group rate), so the
--     blended per-account cost basis is per-product-accurate at partner-month grain.
--
-- Output: BITDEFENDER_RECON_DETAIL (46 cols) + BITDEFENDER_RECON_SUMMARY (29 cols),
-- matching AUVIK_RECON_DETAIL / _SUMMARY.
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

CREATE OR REPLACE TABLE BITDEFENDER_RECON_DETAIL AS

-- FX: convert native-currency Zuora amounts to USD (Proofpoint/Auvik mechanism).
WITH fx_rates AS (
    SELECT UPPER(currency_id) AS currency_id, budget_ex_rate
    FROM analytics.dbo_seed_files.seed__fpa_budget_exchange_rates
    WHERE year(start_date) = (SELECT MAX(year(start_date)) FROM analytics.dbo_seed_files.seed__fpa_budget_exchange_rates)
),

-- Royalties PRODUCT_DESCRIPTION -> sku_match_group (per-product, for the contract
-- overlay and for the primary group). Most-specific ILIKE pattern wins.
roy_group_patterns AS (
    SELECT column1::VARCHAR AS pattern, column2::VARCHAR AS sku_match_group
    FROM VALUES
        ('%Cloud Sec%GravityZone%',        'GRAVITYZONE'),
        ('%Cloud Security Gravity Zone%',  'GRAVITYZONE'),
        ('%GravityZone Email Security%',   'EMAIL'),
        ('%Email Security%',               'EMAIL'),
        ('%ATS & EDR%',                    'ATS_EDR'),
        ('%Advanced Threat Security%',     'ATS_EDR'),
        ('%EDR (MSP Secure)%',             'MSP_SECURE'),
        ('%Secure Plus%',                  'MSP_SECURE_PLUS'),
        ('%Secure Extra%',                 'MSP_SECURE_EXTRA'),
        ('%Cloud Encryption%',             'ENCRYPTION'),
        ('%Patch Management%',             'PATCH'),
        ('%PHASR%',                        'PHASR'),
        ('%XDR%',                          'XDR'),
        ('%Mobile%',                       'MOBILE'),
        ('%Security for Virtualized%',     'VS')
),
roy_desc_group AS (
    SELECT PRODUCT_DESCRIPTION, sku_match_group FROM (
        SELECT d.PRODUCT_DESCRIPTION, gp.sku_match_group,
               ROW_NUMBER() OVER (PARTITION BY d.PRODUCT_DESCRIPTION ORDER BY LENGTH(gp.pattern) DESC) AS rn
        FROM (
              SELECT DISTINCT VENDOR_PRODUCT_SKU AS PRODUCT_DESCRIPTION
              FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
              WHERE VENDOR = 'Bitdefender' AND VENDOR_PRODUCT_SKU IS NOT NULL
        ) d
        LEFT JOIN roy_group_patterns gp ON d.PRODUCT_DESCRIPTION ILIKE gp.pattern
    ) WHERE rn = 1
),

partner_lookup AS (
    SELECT partner_name, sf_id
    FROM RECON_PARTNER_MAP
    WHERE partner_name IS NOT NULL
      AND sf_id IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY UPPER(partner_name)
        ORDER BY IFF(zuora_name IS NOT NULL, 0, 1), IFF(cms_id IS NOT NULL, 0, 1), sf_id
    ) = 1
),

royalties_base AS (
    SELECT
        pm.sf_id,
        r.VENDOR_PARTNER_NAME AS company_name,
        r.BILLING_MONTH::DATE AS billing_month,
        SPLIT_PART(r.MODIFIER, ' | ', 1) AS billing_type,
        r.VENDOR_PRODUCT_SKU AS product_sku,
        r.VENDOR_PRODUCT_SKU AS product_description,
        COALESCE(g.sku_match_group, 'GRAVITYZONE') AS sku_match_group,
        r.QUANTITY AS quantity,
        r.AMOUNT
    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD r
    LEFT JOIN partner_lookup pm
        ON UPPER(pm.partner_name) = UPPER(r.vendor_partner_name)
    LEFT JOIN roy_desc_group g ON g.PRODUCT_DESCRIPTION = r.VENDOR_PRODUCT_SKU
    WHERE r.VENDOR = 'Bitdefender'
      AND r.BILLING_MONTH >= '2026-01-01'
      AND r.BILLING_MONTH <= DATE_TRUNC('MONTH', CURRENT_DATE())
      AND COALESCE(r.QUANTITY, 0) <> 0
),

-- Vendor side aggregated to partner-month (the manual DATA-tab grain).
royalties_agg AS (
    SELECT
        sf_id,
        billing_month,
        MAX(COMPANY_NAME) AS company_name,
        LISTAGG(DISTINCT billing_type, ' | ') WITHIN GROUP (ORDER BY billing_type) AS billing_types,
        LISTAGG(DISTINCT PRODUCT_DESCRIPTION, ' | ') WITHIN GROUP (ORDER BY PRODUCT_DESCRIPTION) AS products,
        ARRAY_AGG(DISTINCT product_sku) WITHIN GROUP (ORDER BY product_sku) AS cw_skus,
        SUM(quantity) AS vendor_quantity,
        SUM(AMOUNT) AS vendor_amount,
        COUNT(*) AS vendor_row_count
    FROM royalties_base
    GROUP BY sf_id, billing_month
),

-- Primary sku_match_group per account-month (largest-qty product group).
royalties_primary_group AS (
    SELECT sf_id, billing_month, sku_match_group AS primary_sku_match_group
    FROM (
        SELECT sf_id, billing_month, sku_match_group,
               ROW_NUMBER() OVER (PARTITION BY sf_id, billing_month ORDER BY SUM(quantity) DESC) AS rn
        FROM royalties_base
        GROUP BY sf_id, billing_month, sku_match_group
    ) WHERE rn = 1
),

-- PER-sku_match_group contract cost: price each Royalties product line at its
-- group's contract rate, then sum to the account-month. This is the Proofpoint
-- per-product overlay applied at partner-month grain (no single blended rate).
royalties_contract_cost AS (
    SELECT
        rb.sf_id,
        rb.billing_month,
        SUM(rb.quantity * COALESCE(cr.contract_cost_rate, 0))::NUMBER(18,2) AS contract_cost_basis_amount,
        SUM(CASE WHEN cr.contract_cost_rate IS NOT NULL THEN rb.quantity ELSE 0 END) AS rated_quantity,
        LISTAGG(DISTINCT cr.source_doc, ' | ') WITHIN GROUP (ORDER BY cr.source_doc) AS contract_rate_source_docs
    FROM royalties_base rb
    LEFT JOIN BITDEFENDER_CONTRACT_RATES cr
        ON cr.vendor_product = rb.sku_match_group
       AND cr.currency = 'USD'
       AND rb.billing_month BETWEEN cr.valid_from AND cr.valid_to
    GROUP BY rb.sf_id, rb.billing_month
),

-- CW side, Zuora BillRun, FX-converted to USD.
zuora_rows AS (
    SELECT
                z.sf_id,
                z.billing_month::DATE AS billing_month,
                z.product_sku,
                z.charge_name,
                COALESCE(z.qty, 0) AS quantity,
                z.unit_price_usd,
                COALESCE(z.charge_amount_usd, 0) AS charge_amount_usd
        FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD z
        WHERE z.vendor = 'Bitdefender'
            AND z.sf_id ILIKE 'ACT-%'
            AND z.billing_month >= '2026-01-01'
            AND COALESCE(z.charge_amount_usd, 0) <> 0
),
zuora_agg AS (
    SELECT
        sf_id,
        billing_month,
        MAX(CHARGE_NAME) AS zuora_account_name,
        ARRAY_AGG(DISTINCT PRODUCT_SKU) WITHIN GROUP (ORDER BY PRODUCT_SKU) AS zuora_skus,
        SUM(quantity) AS zuora_quantity,
        AVG(NULLIF(unit_price_usd, 0)) AS zuora_unit_price,
        SUM(charge_amount_usd) AS zuora_amount,
        COUNT(*) AS zuora_row_count
    FROM zuora_rows
    GROUP BY sf_id, billing_month
),
-- Zuora at (account, month, sku_match_group) for same-group duplicate detection.
zuora_group AS (
    SELECT DISTINCT zr.sf_id, zr.billing_month, ctg.sku_match_group
    FROM zuora_rows zr
    JOIN BITDEFENDER_CHARGE_TO_GROUP ctg ON ctg.CHARGE_NAME = zr.CHARGE_NAME
    WHERE ctg.sku_match_group IS NOT NULL
),

-- Marketplace: Manage / NetSuite-Evergreen billing (CARR), the real "billed
-- outside Zuora" channel. CARR amounts are already USD (budget rate).
marketplace_rows AS (
    SELECT
        COALESCE(a.cws_account_unique_identifier_c, 'UNMAPPED') AS sf_id,
        DATE_TRUNC('month', c.month_year)::DATE AS billing_month,
        c.prod_sku,
        CASE WHEN c.prod_sku ILIKE '%ENCP%' THEN 'ENCRYPTION' ELSE 'GRAVITYZONE' END AS sku_match_group,
        c.ns_usage_qty AS quantity,
        COALESCE(c.product_usage_arr_usd, 0) / 12 AS amount
    FROM ANALYTICS.DBO.CARR__ALL_TRANSACTIONS c
    LEFT JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT a
        ON a.id = c.acc_id AND a.is_deleted = FALSE
    WHERE c.transaction_source IN (
            'Netsuite Evergreen Usage CW', 'Netsuite Evergreen Usage',
            'Netsuite Evergreen Credit Memos CW', 'Netsuite Evergreen Credit Memos'
          )
      AND c.prod_sku ILIKE '3PARTYONPREM%'
      AND (c.prod_sku ILIKE '%BTCD%' OR c.prod_sku ILIKE '%CLDSECGZ%')
      AND DATE_TRUNC('month', c.month_year)::DATE >= '2026-01-01'
      AND COALESCE(c.ns_usage_qty, 0) <> 0
),
marketplace_agg AS (
    SELECT
        sf_id,
        billing_month,
        ARRAY_AGG(DISTINCT prod_sku) WITHIN GROUP (ORDER BY prod_sku) AS marketplace_skus,
        SUM(quantity) AS marketplace_quantity,
        SUM(amount) AS marketplace_amount
    FROM marketplace_rows
    WHERE sf_id <> 'UNMAPPED'
    GROUP BY sf_id, billing_month
),
marketplace_group AS (
    SELECT DISTINCT sf_id, billing_month, sku_match_group
    FROM marketplace_rows
    WHERE sf_id <> 'UNMAPPED'
),
-- DUPLICATE_BILLING: same account/month/sku_match_group present in BOTH Zuora and
-- Marketplace. For Bitdefender this is legitimate dual-channel billing (different
-- seat pools), so it is surfaced as a flag, not scored as a variance.
dup_flags AS (
    SELECT DISTINCT zg.sf_id, zg.billing_month
    FROM zuora_group zg
    JOIN marketplace_group mg
      ON mg.sf_id = zg.sf_id AND mg.billing_month = zg.billing_month AND mg.sku_match_group = zg.sku_match_group
),

joined AS (
    SELECT
        COALESCE(r.sf_id, z.sf_id, m.sf_id) AS sf_id,
        COALESCE(r.billing_month, z.billing_month, m.billing_month) AS billing_month,
        r.company_name AS vendor_partner_name,
        r.products AS vendor_product,
        r.cw_skus,
        z.zuora_skus,
        m.marketplace_skus,
        COALESCE(pg.primary_sku_match_group, 'GRAVITYZONE') AS sku_match_group,
        CASE
            WHEN z.zuora_quantity IS NOT NULL AND m.marketplace_quantity IS NOT NULL THEN 'ZUORA_AND_MARKETPLACE'
            WHEN z.zuora_quantity IS NOT NULL THEN 'ZUORA_ONLY'
            WHEN m.marketplace_quantity IS NOT NULL THEN 'MARKETPLACE_ONLY'
            WHEN r.sf_id IS NOT NULL THEN 'NO_BILLING_SOURCE'
            ELSE 'BILLING_ONLY'
        END AS billing_source_mix,
        COALESCE(r.vendor_quantity, 0)::NUMBER AS vendor_quantity,
        CASE WHEN r.vendor_quantity > 0 THEN r.vendor_amount / r.vendor_quantity ELSE NULL END::NUMBER AS vendor_unit_price,
        COALESCE(r.vendor_amount, 0)::NUMBER AS vendor_amount,
        z.zuora_quantity,
        z.zuora_unit_price,
        z.zuora_amount,
        m.marketplace_quantity,
        m.marketplace_amount,
        -- Zuora BillRun and CARR Marketplace are OVERLAPPING views of billing, not
        -- additive. For Bitdefender, verified (both-present GravityZone keys, Jan-Jun
        -- 2026): Zuora qty materially exceeds CARR in 658/664 keys (967k vs 442k) --
        -- Zuora is the fuller view; CARR is a subset except for pure-Contract
        -- accounts with no Zuora at all. Take GREATEST (the fuller view) to avoid the
        -- ~2x double-count that summing would cause, while still giving the
        -- marketplace-only (Contract) accounts a real billed quantity.
        GREATEST(COALESCE(z.zuora_quantity, 0), COALESCE(m.marketplace_quantity, 0)) AS total_billing_quantity,
        GREATEST(COALESCE(z.zuora_amount, 0),   COALESCE(m.marketplace_amount, 0))   AS total_billing_amount,
        COALESCE(r.vendor_row_count, 0) AS vendor_row_count,
        r.billing_types AS partner_match_methods,
        'ROYALTIES_VS_ZUORA_MARKETPLACE' AS sku_mapping_sources,
        (d.sf_id IS NOT NULL) AS is_duplicate_billing,
        rcc.contract_cost_basis_amount,
        rcc.contract_rate_source_docs,
        COALESCE(
            ARRAY_TO_STRING(z.zuora_skus, '|') ILIKE '%CW-EPSEC-BITDEFENDER%'
            OR ARRAY_TO_STRING(z.zuora_skus, '|') ILIKE '%M2MEPSEC-BITDEFENDER%',
            FALSE
        ) AS mdr_bundle_billed_flag,
        COALESCE(r.products ILIKE '%Gravity%Zone%', FALSE)
            AND COALESCE((r.products ILIKE '%ATS & EDR%' OR r.products ILIKE '%Advanced Threat%'), FALSE)
            AS mdr_component_royalty_flag
    FROM royalties_agg r
    FULL OUTER JOIN zuora_agg z ON z.sf_id = r.sf_id AND z.billing_month = r.billing_month
    LEFT JOIN marketplace_agg m ON m.sf_id = COALESCE(r.sf_id, z.sf_id) AND m.billing_month = COALESCE(r.billing_month, z.billing_month)
    LEFT JOIN royalties_primary_group pg ON pg.sf_id = r.sf_id AND pg.billing_month = r.billing_month
    LEFT JOIN royalties_contract_cost rcc ON rcc.sf_id = r.sf_id AND rcc.billing_month = r.billing_month
    LEFT JOIN dup_flags d ON d.sf_id = COALESCE(r.sf_id, z.sf_id) AND d.billing_month = COALESCE(r.billing_month, z.billing_month)
),

scored AS (
    SELECT
        *,
        total_billing_amount / NULLIF(total_billing_quantity, 0) AS total_billing_unit_price,
        total_billing_quantity - vendor_quantity AS qty_delta,
        ABS(total_billing_quantity - vendor_quantity) AS abs_qty_delta,
        total_billing_amount - vendor_amount AS amount_delta,
        ABS(total_billing_amount - vendor_amount) AS abs_amount_delta,
        is_duplicate_billing AS duplicate_billing_flag,
        FALSE AS marketplace_timing_flag,
        0::FLOAT AS marketplace_timing_quantity,
        -- Blended per-account contract rate = per-product cost basis / vendor qty.
        CASE WHEN vendor_quantity > 0 THEN contract_cost_basis_amount / vendor_quantity ELSE NULL END AS contract_cost_rate,
        CASE
            WHEN sf_id IS NULL THEN 'PARTNER_MAPPING_REQUIRED'
            WHEN partner_match_methods ILIKE '%Contract%'
              OR partner_match_methods ILIKE '%Marketplace%'
              OR mdr_bundle_billed_flag
              OR mdr_component_royalty_flag
                THEN 'CLEAR'
            -- Billed OUTSIDE Zuora BillRun (Contract / Manage-Marketplace channels):
            -- there is no independent CW BillRun meter to reconcile against, so the
            -- vendor's own record is the source of truth and these are CLEAR -- the
            -- manual team's treatment of billed-elsewhere accounts. The CARR
            -- Marketplace qty is surfaced for visibility (and duplicate detection)
            -- but is a parallel/subset view, not an independent CW meter, so it does
            -- not create a variance here.
            WHEN COALESCE(zuora_quantity, 0) = 0
                AND (partner_match_methods ILIKE '%Contract%' OR partner_match_methods ILIKE '%Marketplace%')
                THEN 'CLEAR'
            WHEN vendor_quantity > 0 AND total_billing_quantity = 0 THEN 'NO_BILLING_NO_HISTORY'
            WHEN vendor_quantity = 0 AND total_billing_quantity > 0 THEN 'BILLING_OVER_VENDOR'
            WHEN total_billing_quantity = vendor_quantity THEN 'CLEAR'
            WHEN ABS(total_billing_quantity - vendor_quantity) <= GREATEST(3, vendor_quantity * 0.03) THEN 'CLEAR'
            WHEN total_billing_quantity > vendor_quantity THEN 'BILLING_OVER_VENDOR'
            WHEN total_billing_quantity < vendor_quantity THEN 'VENDOR_OVER_BILLING'
            ELSE 'REVIEW_EXCEPTION'
        END AS base_outcome_flag
    FROM joined
    WHERE COALESCE(vendor_quantity, 0) > 0 OR COALESCE(total_billing_quantity, 0) > 0
)

SELECT
    s.billing_month AS BILLING_MONTH,
    s.sf_id,
    s.vendor_partner_name,
    s.vendor_product,
    s.cw_skus,
    s.zuora_skus,
    s.marketplace_skus,
    s.billing_source_mix,
    s.vendor_quantity,
    s.vendor_unit_price,
    s.vendor_amount,
    s.zuora_quantity,
    s.zuora_unit_price,
    s.zuora_amount,
    s.marketplace_quantity,
    s.marketplace_amount,
    s.total_billing_quantity,
    s.total_billing_unit_price,
    s.total_billing_amount,
    s.qty_delta,
    s.abs_qty_delta,
    s.amount_delta,
    s.abs_amount_delta,
    s.duplicate_billing_flag,
    s.marketplace_timing_flag,
    s.marketplace_timing_quantity,
    s.vendor_row_count AS vendor_source_row_count,
    s.partner_match_methods,
    s.sku_mapping_sources,
    s.mdr_bundle_billed_flag,
    s.mdr_component_royalty_flag,
    -- Contract overlay: per-sku_match_group cost basis (see royalties_contract_cost)
    s.contract_cost_basis_amount / NULLIF(s.contract_cost_rate, 0) AS contract_cost_basis_quantity,
    s.contract_cost_basis_amount,
    s.contract_cost_rate,
    CASE WHEN s.contract_cost_rate IS NOT NULL AND s.total_billing_quantity > 0
        THEN (s.total_billing_amount / s.total_billing_quantity) - s.contract_cost_rate
        ELSE NULL END AS billing_vs_cost_delta_per_seat,
    CASE WHEN s.contract_cost_rate IS NOT NULL AND s.total_billing_quantity > 0
        THEN ((s.total_billing_amount / s.total_billing_quantity) - s.contract_cost_rate) * s.total_billing_quantity
        ELSE NULL END AS billing_vs_cost_dollar_impact,
    CASE WHEN s.contract_cost_rate IS NOT NULL AND s.contract_cost_rate > 0 AND s.total_billing_quantity > 0
        THEN ROUND(((s.total_billing_amount / s.total_billing_quantity) - s.contract_cost_rate) / s.contract_cost_rate * 100, 1)
        ELSE NULL END AS billing_vs_cost_pct,
    CASE
        WHEN s.contract_cost_rate IS NULL OR s.contract_cost_rate = 0 THEN NULL
        WHEN s.total_billing_quantity = 0 THEN NULL
        WHEN (s.total_billing_amount / s.total_billing_quantity) > s.contract_cost_rate * 1.05 THEN 'ABOVE_COST'
        WHEN (s.total_billing_amount / s.total_billing_quantity) >= s.contract_cost_rate * 0.95 THEN 'AT_COST'
        ELSE 'BELOW_COST'
    END AS contract_price_flag,
    CASE WHEN s.contract_cost_rate IS NOT NULL AND s.contract_cost_rate > 0 AND s.total_billing_quantity > 0
        AND (s.total_billing_amount / s.total_billing_quantity) < s.contract_cost_rate * 0.80
        THEN TRUE ELSE FALSE END AS material_below_cost_flag,
    s.contract_rate_source_docs,
    CURRENT_TIMESTAMP() AS recon_run_ts,
    s.base_outcome_flag,
    CASE
        WHEN s.base_outcome_flag = 'CLEAR' THEN 'LOW'
        WHEN s.abs_qty_delta <= GREATEST(10, ABS(s.vendor_quantity) * 0.05) THEN 'LOW'
        WHEN s.abs_qty_delta <= GREATEST(25, ABS(s.vendor_quantity) * 0.10) THEN 'MEDIUM'
        ELSE 'HIGH'
    END AS materiality_band,
    CASE
        WHEN s.billing_source_mix = 'ZUORA_AND_MARKETPLACE' THEN 'MARKETPLACE_OVERLAP'
        WHEN s.billing_source_mix = 'MARKETPLACE_ONLY' THEN 'MARKETPLACE_PRIMARY'
        WHEN s.billing_source_mix = 'NO_BILLING_SOURCE' THEN 'NO_BILLING_SOURCE'
        ELSE 'NON_MARKETPLACE'
    END AS marketplace_classification,
    CASE
        WHEN UPPER(COALESCE(s.vendor_product, '')) LIKE '%OVERAGE%'
          OR UPPER(COALESCE(s.vendor_product, '')) LIKE '%ADDON%'
          OR UPPER(COALESCE(s.vendor_product, '')) LIKE '%ADD-ON%'
          OR UPPER(COALESCE(s.vendor_product, '')) LIKE '%EXCESS%'
            THEN 'OVERAGE_OR_ADDON'
        ELSE 'BASE_OR_BUNDLE'
    END AS overage_classification,
    CONCAT(
        s.base_outcome_flag,
        '|MAT_',
        CASE
            WHEN s.base_outcome_flag = 'CLEAR' THEN 'LOW'
            WHEN s.abs_qty_delta <= GREATEST(10, ABS(s.vendor_quantity) * 0.05) THEN 'LOW'
            WHEN s.abs_qty_delta <= GREATEST(25, ABS(s.vendor_quantity) * 0.10) THEN 'MEDIUM'
            ELSE 'HIGH'
        END,
        '|SRC_',
        CASE
            WHEN s.billing_source_mix = 'ZUORA_AND_MARKETPLACE' THEN 'MARKETPLACE_OVERLAP'
            WHEN s.billing_source_mix = 'MARKETPLACE_ONLY' THEN 'MARKETPLACE_PRIMARY'
            WHEN s.billing_source_mix = 'NO_BILLING_SOURCE' THEN 'NO_BILLING_SOURCE'
            ELSE 'NON_MARKETPLACE'
        END,
        '|OVR_',
        CASE
            WHEN UPPER(COALESCE(s.vendor_product, '')) LIKE '%OVERAGE%'
              OR UPPER(COALESCE(s.vendor_product, '')) LIKE '%ADDON%'
              OR UPPER(COALESCE(s.vendor_product, '')) LIKE '%ADD-ON%'
              OR UPPER(COALESCE(s.vendor_product, '')) LIKE '%EXCESS%'
                THEN 'OVERAGE_OR_ADDON'
            ELSE 'BASE_OR_BUNDLE'
        END
    ) AS outcome_flag,
    CASE
        WHEN s.base_outcome_flag = 'NO_BILLING_NO_HISTORY' THEN 'In Royalties but no matching Zuora/Marketplace billing found.'
        WHEN s.base_outcome_flag = 'VENDOR_OVER_BILLING' THEN 'Royalties qty exceeds total billing (Zuora + Marketplace) qty.'
        WHEN s.base_outcome_flag = 'BILLING_OVER_VENDOR' THEN 'Total billing (Zuora + Marketplace) exceeds Royalties qty.'
        ELSE NULL
    END AS investigation_reason,
    CASE WHEN s.base_outcome_flag IN ('NO_BILLING_NO_HISTORY', 'VENDOR_OVER_BILLING') THEN TRUE ELSE FALSE END AS billing_action_required,
    NULL::NUMBER AS vendor_vs_contract_delta_per_seat,
    NULL::NUMBER AS vendor_vs_contract_pct,
    NULL::VARCHAR AS vendor_vs_contract_flag,
    NULL::NUMBER AS vendor_vs_contract_dollar_impact
FROM scored s;

-- =============================================================================
-- SUMMARY (matches AUVIK_RECON_SUMMARY / PROOFPOINT_RECON_SUMMARY 29-column schema)
-- =============================================================================
CREATE OR REPLACE TABLE BITDEFENDER_RECON_SUMMARY AS
SELECT
    BILLING_MONTH,
    COUNT(*) AS total_rows,
    COUNT_IF(base_outcome_flag = 'CLEAR') AS perfect_match_rows,
    ROUND(COUNT_IF(base_outcome_flag = 'CLEAR') * 100.0 / NULLIF(COUNT(*), 0), 1) AS perfect_match_pct,
    SUM(abs_qty_delta) AS abs_qty_variance,
    SUM(vendor_quantity)::NUMBER AS total_vendor_seats,
    SUM(zuora_quantity) AS total_zuora_seats,
    SUM(marketplace_quantity) AS total_marketplace_seats,
    SUM(total_billing_quantity) AS total_billing_seats,
    SUM(COALESCE(vendor_amount, 0))::NUMBER AS total_vendor_amount,
    SUM(total_billing_amount) AS total_billing_amount,
    COUNT_IF(duplicate_billing_flag = TRUE) AS duplicate_billing_rows,
    SUM(IFF(duplicate_billing_flag, vendor_quantity, 0))::NUMBER AS duplicate_billing_vendor_seats,
    SUM(IFF(duplicate_billing_flag, zuora_quantity, 0)) AS duplicate_billing_zuora_seats,
    SUM(IFF(duplicate_billing_flag, marketplace_quantity, 0)) AS duplicate_billing_marketplace_seats,
    SUM(IFF(duplicate_billing_flag, abs_qty_delta, 0)) AS duplicate_billing_abs_qty_variance_impact,
    SUM(IFF(duplicate_billing_flag, abs_amount_delta, 0)) AS duplicate_billing_abs_amount_variance_impact,
    COUNT_IF(base_outcome_flag = 'PARTNER_MAPPING_REQUIRED') AS unmapped_rows,
    COUNT_IF(base_outcome_flag = 'NO_BILLING_NO_HISTORY') AS no_billing_rows,
    COUNT_IF(base_outcome_flag = 'BILLING_OVER_VENDOR') AS billing_over_rows,
    COUNT_IF(base_outcome_flag = 'VENDOR_OVER_BILLING') AS vendor_over_rows,
    COUNT_IF(contract_price_flag = 'ABOVE_COST') AS contract_above_cost_rows,
    COUNT_IF(contract_price_flag = 'AT_COST') AS contract_at_cost_rows,
    COUNT_IF(contract_price_flag = 'BELOW_COST') AS contract_below_cost_rows,
    COUNT_IF(material_below_cost_flag = TRUE) AS contract_material_below_cost_rows,
    COUNT_IF(contract_price_flag IS NULL) AS contract_no_rate_rows,
    COUNT_IF(mdr_bundle_billed_flag = TRUE) AS mdr_bundle_billed_rows,
    COUNT_IF(mdr_component_royalty_flag = TRUE) AS mdr_component_royalty_rows,
    COALESCE(SUM(IFF(contract_price_flag = 'ABOVE_COST', billing_vs_cost_dollar_impact, 0)), 0) AS contract_above_cost_margin_dollars,
    COALESCE(SUM(IFF(contract_price_flag = 'BELOW_COST', billing_vs_cost_dollar_impact, 0)), 0) AS contract_below_cost_loss_dollars,
    COALESCE(SUM(IFF(material_below_cost_flag = TRUE, billing_vs_cost_dollar_impact, 0)), 0) AS contract_material_below_cost_loss_dollars
FROM BITDEFENDER_RECON_DETAIL
GROUP BY BILLING_MONTH
ORDER BY BILLING_MONTH;

