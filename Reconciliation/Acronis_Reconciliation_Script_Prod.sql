-- =============================================================================
-- STEP 2: ACRONIS FINAL RECONCILIATION  (Proofpoint-style, vendor-SKU grain)
-- =============================================================================
-- 2026-08-03 REBUILD to the Proofpoint / Auvik pattern.
--   Vendor side = THIRD_PARTY_RECON_VENDOR_USAGE_PROD filtered to Acronis
--                 (vendor consumption; AMOUNT = qty * observed USD price)
--   CW side     = THIRD_PARTY_RECON_SOURCE_ZUORA_PROD + THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
--                 resolved to Acronis sku_match_group inside this script
--   Grain       = (sf_id, billing_month, sku_match_group), sku_match_group = VENDOR SKU code
--
-- total_billing = GREATEST(zuora, marketplace) NOT zuora+marketplace. Unlike
-- Proofpoint/SentinelOne additive billing feeds, Acronis Zuora BillRun and
-- CARR Marketplace can be overlapping views of the same billing. Both-present
-- is therefore not automatically duplicate billing for Acronis; it becomes
-- DUPLICATE_BILLING only when the two billing views materially diverge.
--
-- 2026-08-12: Added merged-account resolver (SentinelOne parity). The billing
-- side already collapses to canonical sf_id in 01_billing_sources.sql; here
-- we also canonicalize the vendor-side lookup tables (partner_map and
-- combined_map) so vendor usage and CW billing meet on the same sf_id even
-- after Salesforce account merges. Date-aware: pre-merge months keep their
-- original sf_id (historical truth of who was mapped at the time).
--
-- Output: ACRONIS_RECON_DETAIL + ACRONIS_RECON_SUMMARY
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

CREATE OR REPLACE TABLE ACRONIS_RECON_DETAIL AS

WITH merged_account_resolver AS (
    -- Shared table built in SentinelOne / Proofpoint 00_reference_maps.sql as a
    -- recursive walk of ANALYTICS.DBO.CW_DW__MERGED_ACCOUNT_MAP. Date-aware:
    -- merge_effective_month gates the merge on BILLING_MONTH so pre-merge
    -- billing keeps the historical sf_id.
    SELECT old_sf_id, canonical_sf_id, merge_effective_month, canonical_source
    FROM RECON_ACCOUNT_MERGE_RESOLVER
),
billing_presence AS (
    -- Billing existence signal used to stabilize partner_name fallback mapping.
    SELECT DISTINCT sf_id, billing_month
    FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
    WHERE vendor = 'Acronis'
      AND sf_id ILIKE 'ACT-%'

    UNION

    SELECT DISTINCT sf_id, billing_month
    FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
    WHERE vendor = 'Acronis'
      AND sf_id ILIKE 'ACT-%'
),
acronis_sku_map AS (
    SELECT DISTINCT
        UPPER(TRIM(sku_match_key)) AS sku_match_key,
        UPPER(TRIM(cw_sku)) AS cw_sku
    FROM RECON_SKU_MAP
    WHERE vendor = 'Acronis'
      AND sku_match_key IS NOT NULL
      AND cw_sku IS NOT NULL
        AND UPPER(TRIM(cw_sku)) <> 'UNMATCHED'
),
acronis_sku_map_tokens AS (
    SELECT DISTINCT
        sm.sku_match_key,
        sm.cw_sku,
        UPPER(TRIM(tok.value)) AS cw_sku_token
    FROM acronis_sku_map sm,
         LATERAL SPLIT_TO_TABLE(REPLACE(sm.cw_sku, '/', '|'), '|') tok
    WHERE TRIM(tok.value) <> ''
),
manual_partner_map AS (
    SELECT
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS pn_norm,
        sf_id
    FROM RECON_VENDOR_PARTNER_MANUAL_MAP
    WHERE UPPER(TRIM(vendor)) = 'ACRONIS'
      AND partner_name IS NOT NULL
      AND sf_id ILIKE 'ACT-%'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(partner_name), '[^a-z0-9]+', ' '), '\\s+', ' '))
        ORDER BY COALESCE(updated_at, CURRENT_TIMESTAMP()) DESC, sf_id
    ) = 1
),
partner_map AS (
    WITH partner_name_candidates AS (
        SELECT
            pm.billing_month::DATE AS billing_month,
            TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(pm.partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS pn_norm,
                        pm.sf_id,
            IFF(b.sf_id IS NULL, 0, 1) AS has_billing_match,
            IFF(pm.cms_id IS NULL OR TRIM(pm.cms_id) IN ('', '-'), 0, 1) AS has_cms_id,
            0 AS alias_priority
        FROM RECON_PARTNER_MAP_MONTHLY pm
        LEFT JOIN billing_presence b
                    ON b.sf_id = pm.sf_id
         AND b.billing_month = pm.billing_month::DATE
        WHERE pm.sf_id ILIKE 'ACT-%' AND pm.partner_name IS NOT NULL

        UNION ALL

        SELECT
            pm.billing_month::DATE AS billing_month,
            TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(pm.parent_company), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS pn_norm,
                        pm.sf_id,
            IFF(b.sf_id IS NULL, 0, 1) AS has_billing_match,
            IFF(pm.cms_id IS NULL OR TRIM(pm.cms_id) IN ('', '-'), 0, 1) AS has_cms_id,
            1 AS alias_priority
        FROM RECON_PARTNER_MAP_MONTHLY pm
        LEFT JOIN billing_presence b
                    ON b.sf_id = pm.sf_id
         AND b.billing_month = pm.billing_month::DATE
        WHERE pm.sf_id ILIKE 'ACT-%' AND pm.parent_company IS NOT NULL
    ),
    unambiguous_parent_alias AS (
        SELECT billing_month, pn_norm
        FROM partner_name_candidates
        WHERE alias_priority = 1
        GROUP BY 1, 2
        HAVING COUNT(DISTINCT sf_id) = 1
    ),
    filtered_candidates AS (
        SELECT c.*
        FROM partner_name_candidates c
        WHERE c.alias_priority = 0
           OR EXISTS (
                SELECT 1
                FROM unambiguous_parent_alias u
                WHERE u.billing_month = c.billing_month
                  AND u.pn_norm = c.pn_norm
           )
    )
    SELECT
        billing_month,
        pn_norm,
        sf_id,
        has_billing_match,
        has_cms_id
    FROM filtered_candidates
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY billing_month, pn_norm
        ORDER BY has_billing_match DESC,
                 has_cms_id DESC,
                 alias_priority ASC,
                 sf_id
    ) = 1
),
combined_map AS (
    WITH cms_bridge AS (
        -- A CMS ID can be shared by distinct SF accounts (for example,
        -- ThrottleNet and Twin Pines). Only bridge one-to-one month keys;
        -- otherwise retain the account carried by the combined seed.
        SELECT
            billing_month::DATE AS billing_month,
            cms_id::VARCHAR AS cms_id,
            ANY_VALUE(sf_id) AS effective_sf_id
        FROM RECON_PARTNER_MAP_MONTHLY
        WHERE sf_id ILIKE 'ACT-%'
          AND cms_id IS NOT NULL
        GROUP BY 1, 2
                HAVING COUNT(DISTINCT sf_id) = 1
    )
    SELECT
        cm.BILLING_MONTH::DATE AS billing_month,
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(cm.TENANT_NAME), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS pn_norm,
        UPPER(TRIM(cm.VENDOR_SKU)) AS vendor_sku,
        -- Date-aware merge: only apply canonical for months on/after merge date
        ANY_VALUE(
            COALESCE(
                cb.effective_sf_id,
                CASE WHEN mr.old_sf_id IS NOT NULL
                          AND (mr.merge_effective_month IS NULL OR cm.BILLING_MONTH::DATE >= mr.merge_effective_month)
                     THEN mr.canonical_sf_id END,
                cm.SF_ID
            )
        ) AS sf_id,
        ANY_VALUE(cm.CMS_ID) AS cms_id,
        ANY_VALUE(cm.BILLING_TYPE) AS billing_type,
        ANY_VALUE(cm.CW_SKU) AS mapped_cw_sku,
        MAX(IFF(cm.CW_SKU IS NULL OR TRIM(cm.CW_SKU) = '', 0, 1)) AS has_cw_sku_mapping
    FROM ACRONIS_COMBINED_MAPPING_SEED cm
    LEFT JOIN merged_account_resolver mr ON mr.old_sf_id = cm.SF_ID
    LEFT JOIN cms_bridge cb
      ON cb.billing_month = cm.BILLING_MONTH::DATE
     AND cb.cms_id = cm.CMS_ID::VARCHAR
    WHERE cm.SF_ID ILIKE 'ACT-%' AND cm.TENANT_NAME IS NOT NULL AND cm.VENDOR_SKU IS NOT NULL
    GROUP BY 1, 2, 3
),

-- ---- Vendor side: shared vendor usage -> sf_id, sku_match_group = vendor SKU code ----
vendor_rows AS (
    WITH usage_norm AS (
        SELECT
            u.*,
            TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(u.VENDOR_PARTNER_NAME), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS pn_norm
        FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD u
        WHERE u.VENDOR = 'Acronis'
    ),
    usage_resolved AS (
        SELECT
            u.*,
            CASE
                WHEN cm.sf_id IS NOT NULL
                 AND NOT (
                     UPPER(COALESCE(cm.billing_type, '')) = 'MARKETPLACE'
                     AND COALESCE(cm.has_cw_sku_mapping, 0) = 0
                     AND COALESCE(p.sf_id, mpm.sf_id) IS NOT NULL
                 )
                    THEN cm.sf_id
            END AS combined_sf_id,
            -- The governed monthly map is date-aware and therefore outranks
            -- the undated manual fallback when both resolve the same alias.
            COALESCE(p.sf_id, mpm.sf_id) AS partner_map_sf_id
        FROM usage_norm u
        LEFT JOIN manual_partner_map mpm
            ON mpm.pn_norm = u.pn_norm
        LEFT JOIN combined_map cm
            ON cm.billing_month = u.BILLING_MONTH::DATE
            AND cm.pn_norm = u.pn_norm
            AND cm.vendor_sku = UPPER(TRIM(u.VENDOR_PRODUCT_SKU))
        LEFT JOIN partner_map p
            ON p.billing_month = u.BILLING_MONTH::DATE
           AND p.pn_norm = u.pn_norm
    )
    SELECT
        u.BILLING_MONTH::DATE AS billing_month,
        COALESCE(u.combined_sf_id, u.partner_map_sf_id, NULL) AS sf_id,
        CASE
            WHEN UPPER(TRIM(COALESCE(u.MODIFIER, ''))) = 'DISABLED' THEN
                'DISABLED:' || u.pn_norm
                || '|'
                || COALESCE(u.combined_sf_id, u.partner_map_sf_id, 'UNMAPPED')
            ELSE
                COALESCE(u.combined_sf_id, u.partner_map_sf_id, 'UNMAPPED:' || u.pn_norm)
        END AS partner_recon_key,
        u.VENDOR_PARTNER_NAME AS VENDOR_PARTNER_NAME,
        UPPER(TRIM(u.VENDOR_PRODUCT_SKU)) AS sku_match_group,
        IFF(UPPER(TRIM(COALESCE(u.MODIFIER, ''))) = 'DISABLED', 1, 0) AS is_disabled_modifier,
        COALESCE(u.QUANTITY, 0) AS quantity,
        COALESCE(u.AMOUNT, 0) AS amount
    FROM usage_resolved u
    WHERE COALESCE(u.QUANTITY, 0) > 0
      AND u.VENDOR_PRODUCT_SKU IS NOT NULL
),

vendor_agg_raw AS (
    SELECT
        sf_id, billing_month, sku_match_group,
        LISTAGG(DISTINCT VENDOR_PARTNER_NAME, ' | ') WITHIN GROUP (ORDER BY VENDOR_PARTNER_NAME) AS vendor_partner_name_raw,
        COUNT(DISTINCT VENDOR_PARTNER_NAME) AS partner_name_count,
        sku_match_group AS vendor_product,
        MAX(is_disabled_modifier) AS has_disabled_modifier,
        SUM(quantity) AS vendor_quantity,
        SUM(amount)   AS vendor_amount,
        COUNT(*)      AS vendor_row_count
    FROM vendor_rows
    GROUP BY partner_recon_key, sf_id, billing_month, sku_match_group
),

vendor_agg AS (
    SELECT
        v.sf_id,
        v.billing_month,
        v.sku_match_group,
        v.vendor_partner_name_raw AS vendor_partner_name,
        v.vendor_product,
        v.has_disabled_modifier,
        v.vendor_quantity,
        v.vendor_amount,
        v.vendor_row_count
    FROM vendor_agg_raw v
),

zuora_rollup_raw_sf AS (
    -- Parent rollup can collapse sibling subsidiaries into one parent sf_id.
    -- For vendor-level recon, recover the month+cms raw sf_id when available.
    -- Shared CMS IDs are deliberately excluded rather than ANY_VALUE-selected.
    SELECT
        billing_month::DATE AS billing_month,
        cms_id::VARCHAR AS cms_id,
        ANY_VALUE(raw_sf_id) AS raw_sf_id
    FROM RECON_PARTNER_MAP_MONTHLY
    WHERE raw_sf_id ILIKE 'ACT-%'
      AND cms_id IS NOT NULL
    GROUP BY 1, 2
        HAVING COUNT(DISTINCT raw_sf_id) = 1
),

zuora_union_rows AS (
    SELECT
        sf_id,
        sf_id_source,
        cms_id,
        subscription_sold_to_sf_id_raw AS sold_to_sf_id_raw,
        billing_month,
        product_sku,
        qty,
        unit_price_usd,
        charge_amount_usd,
        invoice_number,
        invoice_id,
        charge_name
    FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
    WHERE vendor = 'Acronis'
      AND sf_id ILIKE 'ACT-%'
      AND COALESCE(qty, 0) <> 0
),

zuora_source_rows AS (
    SELECT
        CASE
            WHEN z.sf_id_source ILIKE '%parent_rollup%'
                THEN COALESCE(
                    CASE WHEN z.sold_to_sf_id_raw ILIKE 'ACT-%' THEN z.sold_to_sf_id_raw END,
                    zr.raw_sf_id,
                    z.sf_id
                )
            ELSE z.sf_id
        END AS sf_id,
        z.billing_month::DATE AS billing_month,
        UPPER(TRIM(z.product_sku)) AS product_sku,
        COALESCE(z.qty, 0) AS qty,
        COALESCE(z.unit_price_usd, 0) AS unit_price_usd,
        COALESCE(z.charge_amount_usd, 0) AS charge_amount_usd,
        z.invoice_number,
        z.invoice_id,
        z.charge_name
    FROM zuora_union_rows z
    LEFT JOIN zuora_rollup_raw_sf zr
      ON zr.billing_month = z.billing_month::DATE
     AND zr.cms_id = z.cms_id
    WHERE z.sf_id ILIKE 'ACT-%'
      AND COALESCE(z.qty, 0) <> 0
),
zuora_mapped_rows AS (
    SELECT
        z.sf_id,
        z.billing_month,
        CASE
            WHEN z.product_sku = 'BB-ACRONIS-PER-GB-BUNDLE'
             AND UPPER(COALESCE(z.charge_name, '')) LIKE '%HOSTED STORAGE (PER GB)%'
                THEN 'SPBAMSENS'
            WHEN z.product_sku = 'BB-ACRONIS-PER-GB-BUNDLE'
             AND UPPER(COALESCE(z.charge_name, '')) LIKE '%LOCAL STORAGE (PER GB)%'
                THEN 'SP4BMSENS'

            WHEN z.product_sku = 'BB-ACRONIS-WORKLOAD-BUNDLE'
             AND UPPER(COALESCE(z.charge_name, '')) LIKE '%HOSTED STORAGE%PER WORKLOAD%'
                THEN 'SPDAMSENS'
            WHEN z.product_sku = 'BB-ACRONIS-WORKLOAD-BUNDLE'
             AND UPPER(COALESCE(z.charge_name, '')) LIKE '%GOOGLE WORKSPACE%'
                THEN 'SQ8AMSENS'
            WHEN z.product_sku = 'BB-ACRONIS-WORKLOAD-BUNDLE'
                 AND UPPER(COALESCE(z.charge_name, '')) LIKE '%MICROSOFT 365%'
                THEN 'SRJAMSENS'
            WHEN z.product_sku = 'BB-ACRONIS-WORKLOAD-BUNDLE'
             AND UPPER(COALESCE(z.charge_name, '')) LIKE '%CYBER FILES CLOUD%USER%'
                THEN 'SPIAMSENS'
            WHEN z.product_sku = 'BB-ACRONIS-WORKLOAD-BUNDLE'
             AND UPPER(COALESCE(z.charge_name, '')) LIKE '%WORKSTATION%'
                THEN 'SPGAMSENS'
            WHEN z.product_sku = 'BB-ACRONIS-WORKLOAD-BUNDLE'
             AND UPPER(COALESCE(z.charge_name, '')) LIKE '% - VM%'
                THEN 'SPFAMSENS'
            WHEN z.product_sku = 'BB-ACRONIS-WORKLOAD-BUNDLE'
             AND UPPER(COALESCE(z.charge_name, '')) LIKE '% - SERVER%'
                THEN 'SPEAMSENS'

            WHEN z.product_sku = 'BB-ACRONIS-ADV-PROTECTION-BUNDLE'
             AND UPPER(COALESCE(z.charge_name, '')) LIKE '%ADVANCED MANAGEMENT%'
                THEN 'SRHAMSENS'
            WHEN z.product_sku = 'BB-ACRONIS-ADV-PROTECTION-BUNDLE'
             AND UPPER(COALESCE(z.charge_name, '')) LIKE '%ADVANCED SECURITY%'
                THEN 'SRIAMSENS'

            -- Zuora NOC service lines do not appear in vendor usage with their
            -- own SKU code; map them to the corresponding workload families.
            WHEN z.product_sku = 'NOCSRVACRCYBPROTSERV'
             AND UPPER(COALESCE(z.charge_name, '')) LIKE '%SERVER%'
                THEN 'SPEAMSENS'
            WHEN z.product_sku = 'NOCSRVACRCYBPROTSERV'
             AND UPPER(COALESCE(z.charge_name, '')) LIKE '%DESKTOP%'
                THEN 'SPFAMSENS'

            ELSE COALESCE(sm.sku_match_key, z.product_sku)
        END AS sku_match_group,
        z.product_sku,
        z.qty,
        z.unit_price_usd,
        z.charge_amount_usd
    FROM zuora_source_rows z
    LEFT JOIN acronis_sku_map_tokens sm
      ON sm.cw_sku_token = z.product_sku
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY z.sf_id, z.billing_month, z.invoice_number, z.invoice_id, z.product_sku, z.charge_name, z.qty, z.charge_amount_usd
        ORDER BY IFF(sm.cw_sku = z.product_sku, 1, 0) DESC,
                 LENGTH(COALESCE(sm.cw_sku, z.product_sku)) ASC,
                 sm.sku_match_key
    ) = 1
),
zuora_agg AS (
    SELECT
        sf_id,
        billing_month,
        sku_match_group,
        ARRAY_AGG(DISTINCT product_sku) WITHIN GROUP (ORDER BY product_sku) AS zuora_skus,
        SUM(qty) AS zuora_quantity,
        IFF(SUM(qty) = 0, NULL, SUM(charge_amount_usd) / NULLIF(SUM(qty), 0)) AS zuora_unit_price,
        SUM(charge_amount_usd) AS zuora_amount,
        COUNT(*) AS zuora_row_count
    FROM zuora_mapped_rows
    GROUP BY 1, 2, 3
),
marketplace_source_rows AS (
    SELECT
        m.sf_id,
        m.billing_month::DATE AS billing_month,
        UPPER(TRIM(m.product_sku)) AS product_sku,
        COALESCE(m.qty, 0) AS qty,
        COALESCE(m.amount, 0) AS amount,
        m.transaction_source,
        m.marketplace_invoice_id
    FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD m
    WHERE m.vendor = 'Acronis'
      AND m.sf_id ILIKE 'ACT-%'
      AND COALESCE(m.qty, 0) <> 0
),
marketplace_mapped_rows AS (
    SELECT
        m.sf_id,
        m.billing_month,
        CASE
            WHEN m.product_sku = 'LEGACYSKUTASPBAMSEN' THEN 'SPBAMSENS'
            ELSE COALESCE(sm.sku_match_key, m.product_sku)
        END AS sku_match_group,
        m.product_sku,
        m.qty,
        m.amount,
        m.transaction_source,
        m.marketplace_invoice_id
    FROM marketplace_source_rows m
    LEFT JOIN acronis_sku_map_tokens sm
      ON sm.cw_sku_token = m.product_sku
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY
            m.sf_id,
            m.billing_month,
            m.product_sku,
            m.qty,
            m.amount,
            COALESCE(m.transaction_source, ''),
            COALESCE(m.marketplace_invoice_id, '')
        ORDER BY IFF(sm.cw_sku = m.product_sku, 1, 0) DESC,
                 LENGTH(COALESCE(sm.cw_sku, m.product_sku)) ASC,
                 sm.sku_match_key
    ) = 1
),
marketplace_agg AS (
    SELECT
        sf_id,
        billing_month,
        sku_match_group,
        ARRAY_AGG(DISTINCT product_sku) WITHIN GROUP (ORDER BY product_sku) AS marketplace_skus,
        SUM(qty) AS marketplace_quantity,
        SUM(amount) AS marketplace_amount
    FROM marketplace_mapped_rows
    GROUP BY 1, 2, 3
),
marketplace_prior_sf_month AS (
    SELECT
        sf_id,
        DATEADD(month, 1, billing_month) AS billing_month,
        SUM(marketplace_quantity) AS prior_month_marketplace_quantity,
        SUM(marketplace_amount) AS prior_month_marketplace_amount,
        COUNT(*) AS prior_month_marketplace_row_count,
        ARRAY_AGG(DISTINCT sku_match_group) WITHIN GROUP (ORDER BY sku_match_group) AS prior_month_marketplace_sku_groups
    FROM marketplace_agg
    GROUP BY 1, 2
),
marketplace_any_sf_month AS (
    SELECT
        sf_id,
        billing_month,
        SUM(marketplace_quantity) AS any_marketplace_quantity,
        SUM(marketplace_amount) AS any_marketplace_amount,
        COUNT(*) AS any_marketplace_row_count,
        ARRAY_AGG(DISTINCT sku_match_group) WITHIN GROUP (ORDER BY sku_match_group) AS any_marketplace_sku_groups
    FROM marketplace_agg
    GROUP BY 1, 2
),

-- Reverse lookup: sf_id -> partner_name for billing-only rows (no vendor side)
sf_id_to_partner AS (
    SELECT sf_id, ANY_VALUE(partner_name) AS partner_name
    FROM RECON_PARTNER_MAP
    WHERE sf_id ILIKE 'ACT-%' AND partner_name IS NOT NULL
    GROUP BY sf_id
),
sf_account_names AS (
    SELECT CWS_ACCOUNT_UNIQUE_IDENTIFIER_C AS sf_id, NAME AS account_name
    FROM ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT
    WHERE CWS_ACCOUNT_UNIQUE_IDENTIFIER_C ILIKE 'ACT-%'
),

joined AS (
    SELECT
        COALESCE(v.sf_id, z.sf_id, m.sf_id) AS sf_id,
        COALESCE(v.billing_month, z.billing_month, m.billing_month) AS billing_month,
        COALESCE(v.sku_match_group, z.sku_match_group, m.sku_match_group) AS sku_match_group,
        COALESCE(v.vendor_partner_name, pn.partner_name, sfn.account_name) AS vendor_partner_name,
        COALESCE(v.vendor_product, COALESCE(v.sku_match_group, z.sku_match_group, m.sku_match_group)) AS vendor_product,
        smv.cw_skus,
        z.zuora_skus,
        m.marketplace_skus,
        CASE
            WHEN z.zuora_quantity IS NOT NULL AND m.marketplace_quantity IS NOT NULL THEN 'ZUORA_AND_MARKETPLACE'
            WHEN z.zuora_quantity IS NOT NULL THEN 'ZUORA_ONLY'
            WHEN m.marketplace_quantity IS NOT NULL THEN 'MARKETPLACE_ONLY'
            ELSE 'NO_BILLING_SOURCE'
        END AS billing_source_mix,
        COALESCE(v.vendor_quantity, 0)::NUMBER AS vendor_quantity,
        CASE WHEN v.vendor_quantity > 0 THEN v.vendor_amount / v.vendor_quantity ELSE NULL END::NUMBER AS vendor_unit_price,
        COALESCE(v.vendor_amount, 0)::NUMBER AS vendor_amount,
        COALESCE(v.has_disabled_modifier, 0) AS has_disabled_modifier,
        z.zuora_quantity,
        z.zuora_unit_price,
        z.zuora_amount,
        m.marketplace_quantity,
        m.marketplace_amount,
        mp.prior_month_marketplace_quantity,
        mp.prior_month_marketplace_amount,
        mp.prior_month_marketplace_row_count,
        mp.prior_month_marketplace_sku_groups,
        ma.any_marketplace_quantity,
        ma.any_marketplace_amount,
        ma.any_marketplace_row_count,
        ma.any_marketplace_sku_groups,
        -- GREATEST (overlapping views), NOT sum -> avoids ~2x double-count.
        GREATEST(COALESCE(z.zuora_quantity, 0), COALESCE(m.marketplace_quantity, 0)) AS total_billing_quantity,
        GREATEST(COALESCE(z.zuora_amount, 0),   COALESCE(m.marketplace_amount, 0))   AS total_billing_amount,
        COALESCE(v.vendor_row_count, 0) AS vendor_row_count,
        CASE WHEN v.sf_id IS NOT NULL THEN 'PARTNER_NAME'
             WHEN pn.partner_name IS NOT NULL THEN 'SF_ID_REVERSE_LOOKUP'
             WHEN sfn.account_name IS NOT NULL THEN 'SALESFORCE_ACCOUNT'
             ELSE 'UNMAPPED' END AS partner_match_methods,
        'VENDOR_USAGE_VS_ZUORA_MARKETPLACE|VENDOR_SKU' AS sku_mapping_sources
    FROM vendor_agg v
    FULL OUTER JOIN zuora_agg z
        ON z.sf_id = v.sf_id AND z.billing_month = v.billing_month AND z.sku_match_group = v.sku_match_group
    FULL OUTER JOIN marketplace_agg m
        ON m.sf_id = COALESCE(v.sf_id, z.sf_id) AND m.billing_month = COALESCE(v.billing_month, z.billing_month)
       AND m.sku_match_group = COALESCE(v.sku_match_group, z.sku_match_group)
    LEFT JOIN marketplace_prior_sf_month mp
        ON mp.sf_id = COALESCE(v.sf_id, z.sf_id, m.sf_id)
       AND mp.billing_month = COALESCE(v.billing_month, z.billing_month, m.billing_month)
    LEFT JOIN marketplace_any_sf_month ma
        ON ma.sf_id = COALESCE(v.sf_id, z.sf_id, m.sf_id)
       AND ma.billing_month = COALESCE(v.billing_month, z.billing_month, m.billing_month)
    LEFT JOIN (SELECT sku_match_key AS sku_match_group, ARRAY_AGG(DISTINCT cw_sku) WITHIN GROUP (ORDER BY cw_sku) AS cw_skus
                             FROM (
                                     SELECT *
                                     FROM RECON_SKU_MAP
                                     WHERE VENDOR = 'Acronis'
                                         AND UPPER(TRIM(COALESCE(cw_sku, ''))) <> 'UNMATCHED'
                             ) GROUP BY 1) smv
        ON smv.sku_match_group = COALESCE(v.sku_match_group, z.sku_match_group, m.sku_match_group)
    LEFT JOIN sf_id_to_partner pn
        ON pn.sf_id = COALESCE(v.sf_id, z.sf_id, m.sf_id)
    LEFT JOIN sf_account_names sfn
        ON sfn.sf_id = COALESCE(v.sf_id, z.sf_id, m.sf_id)
),

other_sku_offsets AS (
    SELECT DISTINCT
        j1.sf_id,
        j1.billing_month,
        j1.sku_match_group
    FROM joined j1
    INNER JOIN joined j2
        ON j2.sf_id = j1.sf_id
       AND j2.billing_month = j1.billing_month
       AND j2.sku_match_group <> j1.sku_match_group
    WHERE j1.sf_id IS NOT NULL
      AND (
            (
                j1.vendor_quantity > 0
                AND j1.total_billing_quantity = 0
                AND j2.vendor_quantity = 0
                AND j2.total_billing_quantity > 0
                AND ABS(j1.vendor_quantity - j2.total_billing_quantity)
                    <= GREATEST(5, j1.vendor_quantity * 0.05)
            )
            OR (
                j1.vendor_quantity = 0
                AND j1.total_billing_quantity > 0
                AND j2.vendor_quantity > 0
                AND j2.total_billing_quantity = 0
                AND ABS(j2.vendor_quantity - j1.total_billing_quantity)
                    <= GREATEST(5, j2.vendor_quantity * 0.05)
            )
      )
),

joined_with_flags AS (
    SELECT
        j.*,
        (o.sf_id IS NOT NULL) AS same_account_other_sku_match_flag
    FROM joined j
    LEFT JOIN other_sku_offsets o
        ON o.sf_id = j.sf_id
       AND o.billing_month = j.billing_month
       AND o.sku_match_group = j.sku_match_group
),

scored AS (
    SELECT
        *,
        total_billing_amount / NULLIF(total_billing_quantity, 0) AS total_billing_unit_price,
        total_billing_quantity - vendor_quantity AS qty_delta,
        ABS(total_billing_quantity - vendor_quantity) AS abs_qty_delta,
        total_billing_amount - vendor_amount AS amount_delta,
        ABS(total_billing_amount - vendor_amount) AS abs_amount_delta,
        -- Acronis-specific duplicate billing: both CW billing views present
        -- AND materially diverge. Both-present without divergence is expected
        -- source overlap and should flow to the normal vendor-vs-billing flags.
        (zuora_quantity IS NOT NULL AND marketplace_quantity IS NOT NULL
         AND ABS(COALESCE(zuora_quantity,0) - COALESCE(marketplace_quantity,0)) > GREATEST(3, COALESCE(zuora_quantity,0) * 0.05)) AS duplicate_billing_flag,
        (
            vendor_quantity > 0
            AND total_billing_quantity = 0
            AND COALESCE(prior_month_marketplace_row_count, 0) > 0
        ) AS marketplace_timing_flag,
        CASE
            WHEN vendor_quantity > 0
             AND total_billing_quantity = 0
             AND COALESCE(prior_month_marketplace_row_count, 0) > 0
                THEN COALESCE(prior_month_marketplace_quantity, 0)
            ELSE 0
        END::FLOAT AS marketplace_timing_quantity,
        -- =====================================================================
        -- Expanded outcome_flag taxonomy (2026-08-12).
        -- Aligned to manual "Comments" vocabulary (JUL/JUN/MAY 2026 workbooks)
        -- and mirrors SentinelOne structural/differential flag families.
        --
        -- Precedence (top -> bottom):
        --   1. Preconditions (mapping / catalog / duplicate billing / no-activity)
        --   2. One-sided rows (vendor-only or billing-only) -> STRUCTURAL flags
        --   3. Two-sided rows -> tolerance / overage / material / drift bands
        --   4. Fallback -> REVIEW_EXCEPTION
        --
        -- Manual "Comments" -> outcome_flag mapping:
        --   Clear                              -> CLEAR
        --   MP clear                           -> MARKETPLACE_ONLY_CLEAR
        --   Overage                            -> OVERAGE_EXPECTED
        --   MP overage                         -> MARKETPLACE_OVERAGE
        --   Billed by CW not by vendor         -> STRUCTURAL_BILLING_ONLY
        --   (MP-only variant)                  -> MARKETPLACE_BILLING_NO_VENDOR
        --   Billed by Vendor not by CW /
        --     Not Billed by CW                 -> STRUCTURAL_VENDOR_ONLY_NO_CONTRACT
        --   No SKU Found                       -> VENDOR_PRODUCT_NO_CW_SKU
        --   Subscription Expired / terminated  -> STRUCTURAL_BILLING_ONLY
        --                                        (small-$ variant -> NEGLIGIBLE_DOLLAR_EXPOSURE)
        --   Zero Usage                         -> NO_ACTIVITY (both sides = 0 filtered upstream;
        --                                                     vendor row present w/ 0 usage passes here)
        -- Plus S1-parity differential bands:
        --   MATERIAL_UNDER_VENDOR   (vendor > billing >25%)
        --   MATERIAL_OVER_VENDOR    (billing > vendor >25%)
        --   BILLING_DIFFERENTIAL_UNDER (vendor > billing 5-25%)  -- covered by OVERAGE_EXPECTED band
        --   BILLING_DIFFERENTIAL_OVER  (billing > vendor 5-25%)
        --   DUPLICATE_BILLING (Zuora+Marketplace both present and divergent)
        -- =====================================================================
        CASE
            -- 1. Structural preconditions
            WHEN sf_id IS NULL THEN 'PARTNER_MAPPING_REQUIRED'
            WHEN has_disabled_modifier = 1 THEN 'DISABLED_PARTNER_SKU'
            WHEN cw_skus IS NULL AND vendor_quantity > 0 THEN 'VENDOR_PRODUCT_NO_CW_SKU'
            WHEN duplicate_billing_flag THEN 'DUPLICATE_BILLING'
            WHEN same_account_other_sku_match_flag THEN 'SKU_MISMATCH_BILLING_ON_OTHER_SKU'
            WHEN marketplace_timing_flag THEN 'MARKETPLACE_TIMING'

            -- 2. Both sides zero (safety; usually filtered upstream)
            WHEN vendor_quantity = 0 AND total_billing_quantity = 0 THEN 'NO_ACTIVITY'

            -- 3. Vendor-only rows (vendor usage, no billing)
            WHEN vendor_quantity > 0 AND total_billing_quantity = 0 THEN
                CASE
                    WHEN vendor_amount <= 100 THEN 'NEGLIGIBLE_DOLLAR_EXPOSURE'
                    ELSE 'STRUCTURAL_VENDOR_ONLY_NO_CONTRACT'
                END

            -- 4. Billing-only rows (billed by CW not by vendor)
            WHEN vendor_quantity = 0 AND total_billing_quantity > 0 THEN
                CASE
                    WHEN total_billing_amount <= 100 THEN 'NEGLIGIBLE_DOLLAR_EXPOSURE'
                    WHEN billing_source_mix = 'MARKETPLACE_ONLY' THEN 'MARKETPLACE_BILLING_NO_VENDOR'
                    ELSE 'STRUCTURAL_BILLING_ONLY'
                END

            -- 5. Two-sided CLEAR (within tolerance)
            WHEN ABS(total_billing_quantity - vendor_quantity) <= GREATEST(5, vendor_quantity * 0.02) THEN
                CASE WHEN billing_source_mix = 'MARKETPLACE_ONLY' THEN 'MARKETPLACE_ONLY_CLEAR' ELSE 'CLEAR' END
            WHEN ABS(total_billing_quantity - vendor_quantity) <= GREATEST(25, vendor_quantity * 0.05) THEN
                'MINOR_DRIFT'

            -- 6. Dollar noise gate (variance exists but negligible $ exposure)
            WHEN GREATEST(COALESCE(vendor_amount, 0), COALESCE(total_billing_amount, 0)) <= 100
                THEN 'NEGLIGIBLE_DOLLAR_EXPOSURE'

            -- 7. Vendor > billing (Overage pattern)
            WHEN vendor_quantity > total_billing_quantity THEN
                CASE
                    WHEN (vendor_quantity - total_billing_quantity) <= GREATEST(10, vendor_quantity * 0.25) THEN
                        CASE WHEN billing_source_mix = 'MARKETPLACE_ONLY' THEN 'MARKETPLACE_OVERAGE' ELSE 'OVERAGE_EXPECTED' END
                    ELSE 'MATERIAL_UNDER_VENDOR'
                END

            -- 8. Billing > vendor
            WHEN total_billing_quantity > vendor_quantity THEN
                CASE
                    WHEN (total_billing_quantity - vendor_quantity) <= GREATEST(10, vendor_quantity * 0.25)
                        THEN 'BILLING_DIFFERENTIAL_OVER'
                    ELSE 'MATERIAL_OVER_VENDOR'
                END

            -- 9. Fallback
            ELSE 'REVIEW_EXCEPTION'
        END AS outcome_flag
    FROM joined_with_flags
    WHERE COALESCE(vendor_quantity, 0) > 0 OR COALESCE(total_billing_quantity, 0) > 0
),

-- =============================================================================
-- API TELEMETRY (direct raw-TRT join, 2026-08-28 rewrite)
-- =============================================================================
-- Architecture parity with Proofpoint (see PROOFPOINT_RECON_DETAIL script).
-- Prior implementation used the stale THIRD_PARTY_RECON_SOURCE_TRT_PROD snapshot
-- for both the partner bridge (sf_id -> cms_id) and a fuzzy multi-column token
-- match. That is now retired in favor of a single canonical wiring:
--
--   TRT usage row is matched to a recon row when
--       partner_id::VARCHAR = RECON_PARTNER_MAP.cms_id
--   AND charge_sku          = RECON_SKU_MAP.TRT_MATCH_KEY
--
-- Acronis uses TRT.charge_sku (not product_sku) because vendor products bill
-- under an APP-BC-<SKU>-001 / APP-SA-<SKU>-001 code. TRT_MATCH_KEY on the SKU
-- map is populated as CW_SKU || '-001' for the CW_SKU rows matching that pattern.
--
-- Cycle window: (day 21 prev month, day 21 current month] -> Acronis invoice cycle.
--   api_quantity     = point-in-time seats on snapshot_date (day 21 current month)
--   avg_api_quantity = daily average across the full cycle
--
acronis_trt_keys AS (
    -- One row per (sku_match_key, charge_sku) that Acronis expects to see in TRT.
    -- UPPER(TRIM(...)) both sides so case / whitespace never causes a miss.
    SELECT DISTINCT
        UPPER(TRIM(trt_match_key)) AS charge_sku_key,
        UPPER(TRIM(sku_match_key)) AS sku_match_group_key
    FROM RECON_SKU_MAP
    WHERE vendor = 'Acronis'
      AND trt_match_key IS NOT NULL
      AND sku_match_key IS NOT NULL
),

acronis_api_partners AS (
    -- One (sf_id, billing_month, sku_match_group) row per scored recon row with
    -- CMS_ID resolved from the canonical RECON_PARTNER_MAP (not the stale TRT
    -- snapshot). Distinct sf_id -> cms_id map is fine because RECON_PARTNER_MAP
    -- is already de-duped upstream.
    SELECT DISTINCT
        s.sf_id,
        s.billing_month,
        s.sku_match_group,
        UPPER(TRIM(s.sku_match_group)) AS sku_match_group_key,
        p.cms_id
    FROM scored s
        JOIN (
                SELECT DISTINCT sf_id, cms_id
                FROM RECON_PARTNER_MAP
                WHERE sf_id IS NOT NULL
                    AND cms_id IS NOT NULL
                    AND TRIM(cms_id) <> ''
        ) p ON p.sf_id = s.sf_id
    WHERE s.sf_id IS NOT NULL
      AND s.sku_match_group IS NOT NULL
),

acronis_api_daily AS (
    -- Direct raw-TRT usage join:
    --   partner_id = cms_id  AND  UPPER(charge_sku) = UPPER(trt_match_key)
    -- Cycle window is (day 21 prev month, day 21 current month].
    SELECT
        pa.sf_id,
        pa.billing_month,
        pa.sku_match_group,
        DATEADD('day', 20, pa.billing_month)::DATE                       AS snapshot_date,
        u.on_date::DATE                                                  AS on_date,
        SUM(COALESCE(u.agent_cnt, 0))                                    AS day_quantity
    FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
    JOIN acronis_api_partners pa
      ON u.partner_id::VARCHAR = pa.cms_id
    JOIN acronis_trt_keys k
      ON k.charge_sku_key      = UPPER(TRIM(u.charge_sku))
     AND k.sku_match_group_key = pa.sku_match_group_key
        WHERE u.on_date >  DATEADD('day', 20, DATEADD('month', -1, pa.billing_month))::DATE
            AND u.on_date <= DATEADD('day', 20, pa.billing_month)::DATE
    GROUP BY 1, 2, 3, 4, 5
),

acronis_api_rollup AS (
    SELECT
        sf_id,
        billing_month,
        sku_match_group,
        MAX(IFF(on_date = snapshot_date, day_quantity, NULL)) AS api_quantity,
        AVG(day_quantity)                                     AS avg_api_quantity
    FROM acronis_api_daily
    GROUP BY 1, 2, 3
)

SELECT
    'Acronis' AS VENDOR,
    s.billing_month AS BILLING_MONTH,
    s.sf_id,
    s.vendor_partner_name,
    s.vendor_product AS VENDOR_PRODUCT,
    s.cw_skus,
    s.zuora_skus,
    s.marketplace_skus,
    s.billing_source_mix,
    api.api_quantity,
    api.avg_api_quantity,
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
    s.prior_month_marketplace_quantity,
    s.prior_month_marketplace_amount,
    s.prior_month_marketplace_row_count,
    s.prior_month_marketplace_sku_groups,
    s.any_marketplace_quantity,
    s.any_marketplace_amount,
    s.any_marketplace_row_count,
    s.any_marketplace_sku_groups,
    s.vendor_row_count AS vendor_source_row_count,
    s.partner_match_methods,
    s.sku_mapping_sources,
    cr.contract_cost_rate AS contract_cost_basis_quantity,
    ROUND(s.vendor_quantity * COALESCE(cr.contract_cost_rate, 0), 2)::NUMBER AS contract_cost_basis_amount,
    cr.contract_cost_rate,
    CASE WHEN cr.contract_cost_rate IS NOT NULL AND s.total_billing_quantity > 0
        THEN (s.total_billing_amount / s.total_billing_quantity) - cr.contract_cost_rate
        ELSE NULL END AS billing_vs_cost_delta_per_seat,
    CASE WHEN cr.contract_cost_rate IS NOT NULL AND s.total_billing_quantity > 0
        THEN ((s.total_billing_amount / s.total_billing_quantity) - cr.contract_cost_rate) * s.total_billing_quantity
        ELSE NULL END AS billing_vs_cost_dollar_impact,
    CASE WHEN cr.contract_cost_rate IS NOT NULL AND cr.contract_cost_rate > 0 AND s.total_billing_quantity > 0
        THEN ROUND(((s.total_billing_amount / s.total_billing_quantity) - cr.contract_cost_rate) / cr.contract_cost_rate * 100, 1)
        ELSE NULL END AS billing_vs_cost_pct,
    CASE
        WHEN cr.contract_cost_rate IS NULL THEN NULL
        WHEN s.total_billing_quantity = 0 THEN NULL
        WHEN (s.total_billing_amount / s.total_billing_quantity) > cr.contract_cost_rate * 1.05 THEN 'ABOVE_COST'
        WHEN (s.total_billing_amount / s.total_billing_quantity) >= cr.contract_cost_rate * 0.95 THEN 'AT_COST'
        ELSE 'BELOW_COST_DISCOUNT'
    END AS contract_price_flag,
    CASE WHEN cr.contract_cost_rate IS NOT NULL AND s.total_billing_quantity > 0
        AND (s.total_billing_amount / s.total_billing_quantity) < cr.contract_cost_rate * 0.80
        THEN TRUE ELSE FALSE END AS material_below_cost_flag,
    cr.source_doc AS contract_rate_source_docs,
    CURRENT_TIMESTAMP() AS recon_run_ts,
    s.outcome_flag,
    CASE s.outcome_flag
        WHEN 'CLEAR'                              THEN NULL
        WHEN 'MARKETPLACE_ONLY_CLEAR'             THEN 'Marketplace-only billing matches vendor within tolerance; no Zuora line expected.'
        WHEN 'OVERAGE_EXPECTED'                   THEN 'Vendor usage exceeds CW billing within expected overage band (<=25% of vendor qty). Typical for metered storage / workload SKUs against a fixed CW commit; overage qty usually onboarded to SF next cycle.'
        WHEN 'MARKETPLACE_OVERAGE'                THEN 'Marketplace-only overage within expected band; validate tier bracket in marketplace overlay.'
        WHEN 'MARKETPLACE_TIMING'                 THEN 'No current-month CW bill matched this vendor SKU, but prior-month Marketplace billing exists for the same account. Monitor as likely Marketplace billing delay before treating as missing bill.'
        WHEN 'STRUCTURAL_BILLING_ONLY'            THEN 'CW is billing material qty but vendor reports zero usage. Possible legacy subscription, vendor decommission, or subscription expired/terminated. Confirm active vendor subscription.'
        WHEN 'MARKETPLACE_BILLING_NO_VENDOR'      THEN 'Marketplace billing present but vendor reports zero usage; typical for marketplace-only fixed commits or trailing MP cycles.'
        WHEN 'STRUCTURAL_VENDOR_ONLY_NO_CONTRACT' THEN 'Vendor usage exists but no CW contract / billing found. Requires CW subscription creation or partner onboarding.'
        WHEN 'VENDOR_PRODUCT_NO_CW_SKU'           THEN 'Vendor SKU has no CW crosswalk in RECON_SKU_MAP (VENDOR=Acronis). Requires SKU catalog addition or product decision.'
        WHEN 'DUPLICATE_BILLING'                  THEN 'Zuora and Marketplace both bill the same partner/month/SKU and materially diverge. Treat as duplicate invoice evidence and reconcile to a single billing source.'
        WHEN 'SKU_MISMATCH_BILLING_ON_OTHER_SKU'  THEN 'Vendor usage and CW billing offset on the same account/month but on different SKU groups. Treat as a SKU mapping offset review first; rebook only if mapping is confirmed wrong.'
        WHEN 'MINOR_DRIFT'                        THEN 'Minor quantity drift within 2-5% (or <=25 units). Below operational action threshold.'
        WHEN 'MATERIAL_OVER_VENDOR'               THEN 'CW billing exceeds vendor usage by >25%. Review for stale subscription, SKU-tier mismatch, or over-billing.'
        WHEN 'MATERIAL_UNDER_VENDOR'              THEN 'Vendor usage exceeds CW billing by >25%. Review for missing overage capture or expired SKU on invoice.'
        WHEN 'BILLING_DIFFERENTIAL_OVER'          THEN 'CW billing exceeds vendor usage in the 5-25% band. Minor drift; validate seat count.'
        WHEN 'BILLING_DIFFERENTIAL_UNDER'         THEN 'Vendor usage exceeds CW billing in the 5-25% band. Minor drift; validate overage line.'
        WHEN 'PARTNER_MAPPING_REQUIRED'           THEN 'Vendor partner name has no CW SF ID mapping. Add partner map entry to ACRONIS_PARTNER_MAP_SEED or ACRONIS_COMBINED_MAPPING_SEED.'
        WHEN 'DISABLED_PARTNER_SKU'              THEN 'Vendor usage row is marked Disabled in source. Track separately from active billing gaps.'
        WHEN 'NEGLIGIBLE_DOLLAR_EXPOSURE'         THEN 'Variance exists but total dollar exposure is <=$100. No action required.'
        WHEN 'NO_ACTIVITY'                        THEN 'Row has zero on both vendor and billing sides; safety fallback.'
        WHEN 'REVIEW_EXCEPTION'                   THEN 'Pattern not matched by any rule; manual review required.'
        ELSE NULL
    END AS investigation_reason,
    CASE
        WHEN s.outcome_flag IN ('CLEAR', 'MARKETPLACE_ONLY_CLEAR', 'OVERAGE_EXPECTED',
                                'MARKETPLACE_OVERAGE', 'NEGLIGIBLE_DOLLAR_EXPOSURE',
                                'DISABLED_PARTNER_SKU',
                                'MINOR_DRIFT', 'NO_ACTIVITY', 'MARKETPLACE_TIMING') THEN FALSE
        ELSE TRUE
    END AS billing_action_required,
    NULL::NUMBER AS vendor_vs_contract_delta_per_seat,
    NULL::NUMBER AS vendor_vs_contract_pct,
    NULL::VARCHAR AS vendor_vs_contract_flag,
    NULL::NUMBER AS vendor_vs_contract_dollar_impact
FROM scored s
LEFT JOIN ACRONIS_CONTRACT_RATES cr
    ON cr.vendor_product = s.sku_match_group
    AND s.billing_month BETWEEN cr.valid_from AND cr.valid_to
    AND cr.currency = 'USD'
LEFT JOIN acronis_api_rollup api
    ON api.sf_id = s.sf_id
   AND api.billing_month = s.billing_month
   AND api.sku_match_group = s.sku_match_group
QUALIFY ROW_NUMBER() OVER (PARTITION BY s.sf_id, s.billing_month, s.sku_match_group ORDER BY cr.contract_cost_rate DESC NULLS LAST) = 1;

-- =============================================================================
-- SUMMARY
-- =============================================================================
CREATE OR REPLACE TABLE ACRONIS_RECON_SUMMARY AS
SELECT
    BILLING_MONTH,
    COUNT(*) AS total_rows,
    COUNT_IF(outcome_flag = 'CLEAR') AS strict_clear_rows,
    ROUND(COUNT_IF(outcome_flag = 'CLEAR') * 100.0 / NULLIF(COUNT(*), 0), 1) AS strict_clear_pct,
    COUNT_IF(billing_action_required = FALSE) AS operational_clear_rows,
    ROUND(COUNT_IF(billing_action_required = FALSE) * 100.0 / NULLIF(COUNT(*), 0), 1) AS operational_clear_pct,
    COUNT_IF(outcome_flag = 'CLEAR') AS perfect_match_rows, -- legacy app compatibility; strict clear
    ROUND(COUNT_IF(outcome_flag = 'CLEAR') * 100.0 / NULLIF(COUNT(*), 0), 1) AS perfect_match_pct,
    SUM(abs_qty_delta) AS abs_qty_variance,
    SUM(IFF(billing_action_required = FALSE, abs_qty_delta, 0)) AS operational_clear_abs_qty_variance,
    SUM(IFF(billing_action_required = TRUE, abs_qty_delta, 0)) AS actionable_abs_qty_variance,
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
    COUNT_IF(outcome_flag = 'PARTNER_MAPPING_REQUIRED') AS unmapped_rows,
    COUNT_IF(outcome_flag = 'NO_BILLING_NO_HISTORY') AS no_billing_rows,          -- legacy retained (=0 post-taxonomy expansion)
    COUNT_IF(outcome_flag = 'BILLING_OVER_VENDOR') AS billing_over_rows,          -- legacy retained (=0 post-taxonomy expansion)
    COUNT_IF(outcome_flag = 'VENDOR_OVER_BILLING') AS vendor_over_rows,           -- legacy retained (=0 post-taxonomy expansion)
    COUNT_IF(outcome_flag = 'STRUCTURAL_VENDOR_ONLY_NO_CONTRACT') AS structural_vendor_only_rows,
    COUNT_IF(outcome_flag = 'STRUCTURAL_BILLING_ONLY') AS structural_billing_only_rows,
    COUNT_IF(outcome_flag = 'MARKETPLACE_BILLING_NO_VENDOR') AS marketplace_billing_no_vendor_rows,
    COUNT_IF(outcome_flag = 'MARKETPLACE_TIMING') AS marketplace_timing_rows,
    COUNT_IF(outcome_flag = 'MARKETPLACE_ONLY_CLEAR') AS marketplace_only_clear_rows,
    COUNT_IF(outcome_flag = 'OVERAGE_EXPECTED') AS overage_expected_rows,
    COUNT_IF(outcome_flag = 'MARKETPLACE_OVERAGE') AS marketplace_overage_rows,
    COUNT_IF(outcome_flag = 'VENDOR_PRODUCT_NO_CW_SKU') AS vendor_product_no_cw_sku_rows,
    COUNT_IF(outcome_flag = 'SKU_MISMATCH_BILLING_ON_OTHER_SKU') AS sku_mismatch_billing_on_other_sku_rows,
    COUNT_IF(outcome_flag = 'MATERIAL_OVER_VENDOR') AS material_over_vendor_rows,
    COUNT_IF(outcome_flag = 'MATERIAL_UNDER_VENDOR') AS material_under_vendor_rows,
    COUNT_IF(outcome_flag = 'BILLING_DIFFERENTIAL_OVER') AS billing_differential_over_rows,
    COUNT_IF(outcome_flag = 'BILLING_DIFFERENTIAL_UNDER') AS billing_differential_under_rows,
    COUNT_IF(outcome_flag = 'MINOR_DRIFT') AS minor_drift_rows,
    COUNT_IF(outcome_flag = 'DISABLED_PARTNER_SKU') AS disabled_partner_sku_rows,
    COUNT_IF(outcome_flag = 'NEGLIGIBLE_DOLLAR_EXPOSURE') AS negligible_dollar_exposure_rows,
    COUNT_IF(outcome_flag = 'NO_ACTIVITY') AS no_activity_rows,
    COUNT_IF(outcome_flag = 'REVIEW_EXCEPTION') AS review_exception_rows,
    COUNT_IF(contract_price_flag = 'ABOVE_COST') AS contract_above_cost_rows,
    COUNT_IF(contract_price_flag = 'AT_COST') AS contract_at_cost_rows,
    COUNT_IF(contract_price_flag = 'BELOW_COST_DISCOUNT') AS contract_below_cost_rows,
    COUNT_IF(material_below_cost_flag = TRUE) AS contract_material_below_cost_rows,
    COUNT_IF(contract_price_flag IS NULL) AS contract_no_rate_rows,
    COALESCE(SUM(IFF(contract_price_flag = 'ABOVE_COST', billing_vs_cost_dollar_impact, 0)), 0) AS contract_above_cost_margin_dollars,
    COALESCE(SUM(IFF(contract_price_flag = 'BELOW_COST_DISCOUNT', billing_vs_cost_dollar_impact, 0)), 0) AS contract_below_cost_loss_dollars,
    COALESCE(SUM(IFF(material_below_cost_flag = TRUE, billing_vs_cost_dollar_impact, 0)), 0) AS contract_material_below_cost_loss_dollars
FROM ACRONIS_RECON_DETAIL
GROUP BY BILLING_MONTH
ORDER BY BILLING_MONTH;

-- =============================================================================
-- APP / AUDIT SUPPORT TABLES
-- =============================================================================

CREATE OR REPLACE TABLE ACRONIS_RAW_PARTNER_COVERAGE AS
WITH merged_account_resolver AS (
    SELECT old_sf_id, canonical_sf_id, merge_effective_month
    FROM RECON_ACCOUNT_MERGE_RESOLVER
),
partner_map AS (
    SELECT
        pm.billing_month::DATE AS billing_month,
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(pm.partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS pn_norm,
        pm.sf_id AS sf_id
    FROM RECON_PARTNER_MAP_MONTHLY pm
    WHERE pm.sf_id ILIKE 'ACT-%' AND pm.partner_name IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY pm.billing_month::DATE,
                     TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(pm.partner_name), '[^a-z0-9]+', ' '), '\\s+', ' '))
        ORDER BY IFF(pm.cms_id IS NULL OR TRIM(pm.cms_id) IN ('', '-'), 0, 1) DESC,
                 IFF(pm.zuora_name IS NULL OR TRIM(pm.zuora_name) IN ('', '-'), 0, 1) DESC,
                 pm.sf_id
    ) = 1
),
combined_map AS (
    SELECT
        BILLING_MONTH::DATE AS billing_month,
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(cm.TENANT_NAME), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS pn_norm,
        UPPER(TRIM(cm.VENDOR_SKU)) AS vendor_sku,
        ANY_VALUE(
            COALESCE(
                CASE WHEN mr.old_sf_id IS NOT NULL
                          AND (mr.merge_effective_month IS NULL OR cm.BILLING_MONTH::DATE >= mr.merge_effective_month)
                     THEN mr.canonical_sf_id END,
                cm.SF_ID
            )
        ) AS sf_id
    FROM ACRONIS_COMBINED_MAPPING_SEED cm
    LEFT JOIN merged_account_resolver mr ON mr.old_sf_id = cm.SF_ID
    WHERE cm.SF_ID ILIKE 'ACT-%' AND cm.TENANT_NAME IS NOT NULL AND cm.VENDOR_SKU IS NOT NULL
    GROUP BY 1, 2, 3
),
raw AS (
    SELECT
        u.BILLING_MONTH::DATE AS billing_month,
        UPPER(TRIM(u.VENDOR_PRODUCT_SKU)) AS vendor_sku,
        COALESCE(u.QUANTITY, 0) AS quantity,
        COALESCE(cm.sf_id, p.sf_id) AS sf_id
    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD u
    LEFT JOIN combined_map cm
        ON cm.billing_month = u.BILLING_MONTH::DATE
       AND cm.pn_norm = TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(u.VENDOR_PARTNER_NAME), '[^a-z0-9]+', ' '), '\\s+', ' '))
       AND cm.vendor_sku = UPPER(TRIM(u.VENDOR_PRODUCT_SKU))
    LEFT JOIN partner_map p
                ON p.billing_month = u.BILLING_MONTH::DATE
             AND p.pn_norm = TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(u.VENDOR_PARTNER_NAME), '[^a-z0-9]+', ' '), '\\s+', ' '))
    WHERE u.VENDOR = 'Acronis'
      AND COALESCE(u.QUANTITY, 0) > 0
      AND u.VENDOR_PRODUCT_SKU IS NOT NULL
)
SELECT
    billing_month,
    COUNT(*) AS raw_usage_rows,
    COUNT_IF(sf_id IS NOT NULL) AS mapped_usage_rows,
    COUNT_IF(sf_id IS NULL) AS unmapped_usage_rows,
    SUM(quantity) AS raw_usage_quantity,
    SUM(IFF(sf_id IS NOT NULL, quantity, 0)) AS mapped_usage_quantity,
    SUM(IFF(sf_id IS NULL, quantity, 0)) AS unmapped_usage_quantity,
    ROUND(COUNT_IF(sf_id IS NOT NULL) * 100.0 / NULLIF(COUNT(*), 0), 2) AS partner_row_coverage_pct,
    ROUND(SUM(IFF(sf_id IS NOT NULL, quantity, 0)) * 100.0 / NULLIF(SUM(quantity), 0), 2) AS partner_quantity_coverage_pct
FROM raw
GROUP BY billing_month
ORDER BY billing_month;

CREATE OR REPLACE TABLE ACRONIS_SOURCE_COVERAGE_AUDIT AS
WITH usage_qty AS (
    SELECT BILLING_MONTH::DATE AS billing_month, SUM(QUANTITY) AS vendor_usage_quantity
    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
    WHERE VENDOR = 'Acronis'
    GROUP BY 1
),
zuora_base AS (
        SELECT billing_month::DATE AS billing_month, SUM(qty) AS zuora_posted_billrun_quantity
        FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
        WHERE vendor = 'Acronis'
            AND billing_month >= '2026-01-01'
    GROUP BY 1
),
resolved AS (
    SELECT BILLING_MONTH, SUM(TOTAL_BILLING_QUANTITY) AS resolved_billing_quantity
    FROM ACRONIS_RECON_DETAIL
    GROUP BY 1
)
SELECT
    u.billing_month,
    u.vendor_usage_quantity,
    COALESCE(z.zuora_posted_billrun_quantity, 0) AS zuora_posted_billrun_quantity,
    COALESCE(r.resolved_billing_quantity, 0) AS resolved_billing_quantity,
    ROUND(COALESCE(r.resolved_billing_quantity, 0) * 100.0 / NULLIF(u.vendor_usage_quantity, 0), 2) AS resolved_billing_vs_vendor_pct,
    CASE
        WHEN COALESCE(r.resolved_billing_quantity, 0) < u.vendor_usage_quantity * 0.50 THEN 'INCOMPLETE_BILLING_SOURCE_COVERAGE'
        WHEN COALESCE(r.resolved_billing_quantity, 0) < u.vendor_usage_quantity * 0.85 THEN 'LOW_BILLING_SOURCE_COVERAGE'
        ELSE 'SOURCE_COVERAGE_OK'
    END AS source_coverage_flag
FROM usage_qty u
LEFT JOIN zuora_base z ON z.billing_month = u.billing_month
LEFT JOIN resolved r ON r.billing_month = u.billing_month
ORDER BY u.billing_month;

