-- =============================================================================
-- PROOFPOINT FINAL RECONCILIATION
-- =============================================================================
-- Purpose:
--   Build the production Proofpoint reconciliation directly from normalized
--   vendor usage, partner/SKU maps, Zuora billing, and Marketplace billing.
--
-- Required upstream objects:
--   - THIRD_PARTY_RECON_VENDOR_USAGE_PROD filtered to Proofpoint
--   - RECON_PARTNER_MAP
--   - (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Proofpoint')
--   - THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
--   - THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
--
-- Outputs:
--   - PROOFPOINT_RECON_DETAIL
--   - PROOFPOINT_RECON_SUMMARY
--   - PROOFPOINT_RAW_PARTNER_COVERAGE
--
-- Business logic:
--   qty_delta = Zuora quantity + Marketplace quantity - Vendor quantity
--   Positive qty_delta means CW billing is higher than vendor usage.
--   Negative qty_delta means vendor usage is higher than CW billing.
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

CREATE OR REPLACE TABLE PROOFPOINT_RECON_DETAIL AS

WITH partner_name_map AS (
    SELECT
        billing_month,
        partner_name,
        TRIM(
            REGEXP_REPLACE(
                REGEXP_REPLACE(LOWER(partner_name), '[^a-z0-9]+', ' '),
                '\\s+',
                ' '
            )
        ) AS partner_name_normalized,
        sf_id
    FROM RECON_PARTNER_MAP_MONTHLY
    WHERE sf_id IS NOT NULL
      AND REGEXP_LIKE(sf_id, '^ACT-[0-9A-Z-]+$')
      AND partner_name IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY billing_month, partner_name
        ORDER BY zuora_name DESC NULLS LAST
    ) = 1
),

partner_name_map_deduped AS (
    SELECT billing_month, partner_name_normalized, sf_id
    FROM partner_name_map
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY billing_month, partner_name_normalized
        ORDER BY partner_name
    ) = 1
),

manual_partner_map AS (
    SELECT
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS partner_name_normalized,
        sf_id
    FROM RECON_VENDOR_PARTNER_MANUAL_MAP
    WHERE UPPER(TRIM(vendor)) = 'PROOFPOINT'
      AND partner_name IS NOT NULL
      AND sf_id ILIKE 'ACT-%'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(partner_name), '[^a-z0-9]+', ' '), '\\s+', ' '))
        ORDER BY COALESCE(updated_at, CURRENT_TIMESTAMP()) DESC, sf_id
    ) = 1
),

cmit_parent_rollup AS (
    SELECT DISTINCT
        a_child.cws_account_unique_identifier_c AS child_sf_id,
        a_parent.cws_account_unique_identifier_c AS parent_sf_id
    FROM analytics.dbo_seed_files.seed__partner_parent_child_relationships p
    LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__account a_child
        ON a_child.id = p.sf_account_id
       AND a_child.is_deleted = FALSE
    LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__account a_parent
        ON a_parent.id = p.parent_id
       AND a_parent.is_deleted = FALSE
    WHERE p.parent_name ILIKE 'CMIT%'
      AND a_child.cws_account_unique_identifier_c IS NOT NULL
      AND a_parent.cws_account_unique_identifier_c IS NOT NULL
      AND a_child.cws_account_unique_identifier_c <> a_parent.cws_account_unique_identifier_c
),

pp_skus AS (
    SELECT
        vendor_product,
        vendor_sku AS vendor_sku_invoices,
        cw_sku,
        trt_match_key,
        sku_match_key AS sku_match_group,
        'SIMPLIFIED_SKU_MAP' AS mapping_source
    FROM (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Proofpoint')
),

-- Time-variant per-seat contract cost rate lookup.
-- Populated in 00_reference_maps.sql from vendor invoices + rate cards.
pp_contract_rates AS (
    SELECT
        vendor_product,
        currency,
        contract_cost_rate,
        valid_from,
        valid_to,
        source_doc
    FROM PROOFPOINT_CONTRACT_RATES
),

proofpoint_loaded_billing_months AS (
    SELECT DISTINCT billing_month::DATE AS billing_month
    FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
    WHERE vendor = 'Proofpoint'
    UNION
    SELECT DISTINCT billing_month::DATE AS billing_month
    FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
    WHERE vendor = 'Proofpoint'
),

proofpoint_base AS (
    SELECT
        ROW_NUMBER() OVER (
            ORDER BY
                u.billing_month,
                u.vendor_partner_name,
                u.vendor_product_sku,
                COALESCE(u.quantity, 0),
                COALESCE(u.amount, 0),
                COALESCE(u.unit_price, 0)
        ) AS usage_row_id,
        u.billing_month,
        u.vendor_partner_name,
        TRIM(
            REGEXP_REPLACE(
                REGEXP_REPLACE(LOWER(u.vendor_partner_name), '[^a-z0-9]+', ' '),
                '\\s+',
                ' '
            )
        ) AS vendor_partner_name_normalized,
        u.vendor_product_sku AS vendor_product,
        NULL::VARCHAR AS cms_id,
        NULL::VARCHAR AS vendor_entity,
        u.currency,
        u.quantity,
        u.unit_price,
        u.amount,
        COALESCE(mpm.sf_id, pn.sf_id) AS partner_sf_id,
        -- Preserve the historical/raw identity before the effective merge
        -- month, then use the canonical identity from that month onward.
        COALESCE(
            cr.parent_sf_id,
            CASE
                WHEN sfr.merge_effective_month IS NOT NULL
                 AND u.billing_month::DATE < sfr.merge_effective_month
                    THEN COALESCE(mpm.sf_id, pn.sf_id)
                ELSE COALESCE(sfr.canonical_sf_id, mpm.sf_id, pn.sf_id)
            END
        ) AS sf_id,
        COALESCE(mpm.sf_id, pn.sf_id) AS raw_sf_id,
        CASE
            WHEN sfr.merge_effective_month IS NOT NULL
             AND u.billing_month::DATE < sfr.merge_effective_month
                THEN 'PRE_MERGE_SOURCE'
            ELSE sfr.canonical_source
        END AS sf_id_resolver_source,
        CASE
            WHEN cr.parent_sf_id IS NOT NULL AND mpm.sf_id IS NOT NULL THEN 'MANUAL_VENDOR_PARTNER_MAP|CMIT_PARENT_ROLLUP'
            WHEN cr.parent_sf_id IS NOT NULL AND pn.sf_id IS NOT NULL THEN 'PARTNER_NAME|CMIT_PARENT_ROLLUP'
            WHEN mpm.sf_id IS NOT NULL THEN 'MANUAL_VENDOR_PARTNER_MAP'
            WHEN pn.sf_id IS NOT NULL THEN 'PARTNER_NAME'
            ELSE 'UNMAPPED'
        END AS partner_match_method
    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD u
    LEFT JOIN manual_partner_map mpm
        ON mpm.partner_name_normalized = TRIM(
            REGEXP_REPLACE(
                REGEXP_REPLACE(LOWER(u.vendor_partner_name), '[^a-z0-9]+', ' '),
                '\\s+',
                ' '
            )
        )
    LEFT JOIN partner_name_map_deduped pn
        ON mpm.sf_id IS NULL
       AND pn.partner_name_normalized = TRIM(
            REGEXP_REPLACE(
                REGEXP_REPLACE(LOWER(u.vendor_partner_name), '[^a-z0-9]+', ' '),
                '\\s+',
                ' '
            )
        )
       AND pn.billing_month = u.billing_month::DATE
    LEFT JOIN RECON_ACCOUNT_MERGE_RESOLVER sfr
        ON sfr.old_sf_id = COALESCE(mpm.sf_id, pn.sf_id)
    LEFT JOIN cmit_parent_rollup cr
        ON cr.child_sf_id = CASE
            WHEN sfr.merge_effective_month IS NOT NULL
             AND u.billing_month::DATE < sfr.merge_effective_month
                THEN COALESCE(mpm.sf_id, pn.sf_id)
            ELSE COALESCE(sfr.canonical_sf_id, mpm.sf_id, pn.sf_id)
        END
    WHERE u.VENDOR = 'Proofpoint'
      AND COALESCE(u.quantity, 0) <> 0
      AND COALESCE(u.amount, 0) <> 0
            AND u.billing_month::DATE IN (SELECT billing_month FROM proofpoint_loaded_billing_months)
),

pp_product_to_group AS (
    SELECT DISTINCT vendor_product, sku_match_group
    FROM pp_skus
),

sku_candidates AS (
    SELECT
        b.*,
        pp.vendor_sku_invoices,
        pp.cw_sku,
        pg.sku_match_group,
        pp.mapping_source,
        rt.contract_cost_rate,
        rt.source_doc AS contract_rate_source_doc
    FROM proofpoint_base b
    LEFT JOIN pp_product_to_group pg
        ON pg.vendor_product = b.vendor_product
    LEFT JOIN pp_skus pp
        ON pp.sku_match_group = pg.sku_match_group
    LEFT JOIN pp_contract_rates rt
        ON rt.vendor_product = b.vendor_product
       AND rt.currency = b.currency
       AND b.billing_month BETWEEN rt.valid_from AND rt.valid_to
),

proofpoint_int AS (
    SELECT
        usage_row_id,
        billing_month,
        vendor_partner_name,
        vendor_product,
        cms_id,
        vendor_entity,
        currency,
        quantity,
        unit_price,
        amount,
        sf_id,
        partner_match_method,
        ANY_VALUE(contract_cost_rate) AS contract_cost_rate_row,
        ANY_VALUE(contract_rate_source_doc) AS contract_rate_source_doc,
        COALESCE(sku_match_group, vendor_product) AS sku_match_group,
        ARRAY_AGG(DISTINCT cw_sku) WITHIN GROUP (ORDER BY cw_sku) AS cw_skus,
        ARRAY_AGG(DISTINCT vendor_sku_invoices)
            WITHIN GROUP (ORDER BY vendor_sku_invoices) AS vendor_skus_invoices,
        LISTAGG(DISTINCT mapping_source, ' | ')
            WITHIN GROUP (ORDER BY mapping_source) AS sku_mapping_sources
    FROM sku_candidates
    GROUP BY ALL
),

-- =============================================================================
-- Proofpoint API usage (direct-from-raw architecture, 2026-08-28)
-- ---------------------------------------------------------------------------
-- Replaces the stale THIRD_PARTY_RECON_SOURCE_TRT_PROD snapshot / the
-- pipeline-level PROOFPOINT_API_BACKFILL_SQL step. API_QUANTITY and
-- AVG_API_QUANTITY are computed inline off the raw daily-usage table:
--   ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
-- Join keys (governed):
--   * partner_id (usage) = cms_id from RECON_PARTNER_MAP_MONTHLY.
--   * product_sku (usage) = TRT_MATCH_KEY from RECON_SKU_MAP (Proofpoint).
-- Cycle window:
--   Proofpoint API snapshot = day 20 of each month, so a billing_month M
--   accumulates on_date rows in (M-1 + 20 days, M + 20 days].
-- Grain: (sf_id, billing_month, sku_match_group) -- matches vendor_group_base
--        which is how vendor_agg rolls up to the recon detail row.
-- Metrics:
--   * api_quantity      = seats reported by TRT on the snapshot day (day 20).
--   * avg_api_quantity  = average day_quantity across the full cycle window.
-- =============================================================================
pp_trt_keys AS (
    -- All distinct TRT product_sku values Proofpoint maps to, plus the
    -- sku_match_group they roll up to. Distinct so the join fan-out matches
    -- the recon grain. Both sides normalized to UPPER(TRIM(...)) for the join.
    SELECT DISTINCT
        UPPER(TRIM(trt_match_key)) AS product_sku_key,
        UPPER(TRIM(sku_match_group)) AS sku_match_group_key
    FROM pp_skus
    WHERE trt_match_key IS NOT NULL
      AND TRIM(trt_match_key) <> ''
),

proofpoint_api_partners AS (
    -- Restrict to sf_ids that actually appear in the Proofpoint recon for a
    -- given billing_month, joined to their month-effective cms_id.
    SELECT DISTINCT
        i.sf_id,
        i.billing_month,
        i.sku_match_group,
        UPPER(TRIM(i.sku_match_group)) AS sku_match_group_key,
        pm.cms_id
    FROM proofpoint_int i
                JOIN RECON_PARTNER_MAP_MONTHLY_SF_UNIQUE pm
      ON pm.sf_id = i.sf_id
         AND pm.billing_month = i.billing_month
    WHERE pm.cms_id IS NOT NULL
      AND TRIM(pm.cms_id) <> ''
),

proofpoint_api_daily AS (
    -- One row per (sf_id, billing_month, sku_match_group, on_date). The
    -- window predicate binds each on_date to exactly one billing_month.
    SELECT
        pa.sf_id,
        pa.billing_month,
        pa.sku_match_group,
        DATEADD('day', 20, pa.billing_month)::DATE            AS snapshot_date,
        u.on_date::DATE                                        AS on_date,
        SUM(COALESCE(u.agent_cnt, 0))                          AS day_quantity
    FROM proofpoint_api_partners pa
    JOIN pp_trt_keys k
      ON k.sku_match_group_key = pa.sku_match_group_key
    JOIN ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
      ON u.partner_id::VARCHAR = pa.cms_id
     AND UPPER(TRIM(u.product_sku)) = k.product_sku_key
     AND u.on_date::DATE >  DATEADD('day', 20, DATEADD('month', -1, pa.billing_month))::DATE
     AND u.on_date::DATE <= DATEADD('day', 20, pa.billing_month)::DATE
    GROUP BY 1, 2, 3, 4, 5
),

proofpoint_api_usage AS (
    -- Roll up to recon grain. api_quantity = day-20 snapshot value (the
    -- vendor-invoice snapshot day). avg_api_quantity = mean across the
    -- window, used to smooth the mid-cycle add/drop noise.
    SELECT
        sf_id,
        billing_month,
        sku_match_group,
        MAX(IFF(on_date = snapshot_date, day_quantity, NULL))  AS api_quantity,
        AVG(day_quantity)                                       AS avg_api_quantity
    FROM proofpoint_api_daily
    GROUP BY 1, 2, 3
),

vendor_group_base AS (
    SELECT
        sf_id,
        billing_month,
        sku_match_group,
        LISTAGG(DISTINCT vendor_product, ' | ')
            WITHIN GROUP (ORDER BY vendor_product) AS vendor_product,
        SUM(quantity) AS vendor_quantity,
        AVG(NULLIF(unit_price, 0)) AS vendor_unit_price,
        SUM(amount) AS vendor_amount,
        COUNT(DISTINCT usage_row_id) AS vendor_row_count,
        -- Contract cost basis: extended cost at vendor volume using rate lookup.
        -- Only aggregates rows where a governed rate exists for (product,currency,month).
        SUM(IFF(contract_cost_rate_row IS NOT NULL, quantity, 0)) AS contract_cost_basis_quantity,
        SUM(IFF(contract_cost_rate_row IS NOT NULL, quantity * contract_cost_rate_row, 0))
            AS contract_cost_basis_amount,
        COUNT_IF(contract_cost_rate_row IS NULL) AS contract_rate_missing_row_count,
        LISTAGG(DISTINCT contract_rate_source_doc, ' | ')
            WITHIN GROUP (ORDER BY contract_rate_source_doc) AS contract_rate_source_docs,
        LISTAGG(DISTINCT vendor_partner_name, ' | ')
            WITHIN GROUP (ORDER BY vendor_partner_name) AS vendor_partner_name,
        LISTAGG(DISTINCT partner_match_method, ' | ')
            WITHIN GROUP (ORDER BY partner_match_method) AS partner_match_methods,
        LISTAGG(DISTINCT vendor_entity, ' | ')
            WITHIN GROUP (ORDER BY vendor_entity) AS vendor_entities,
        LISTAGG(DISTINCT currency, ' | ')
            WITHIN GROUP (ORDER BY currency) AS currencies,
        LISTAGG(DISTINCT sku_mapping_sources, ' | ')
            WITHIN GROUP (ORDER BY sku_mapping_sources) AS sku_mapping_sources,
        LISTAGG(
            DISTINCT CONCAT(
                vendor_product,
                ' qty=',
                quantity::VARCHAR,
                ' amount=',
                ROUND(amount, 2)::VARCHAR
            ),
            ' | '
        ) WITHIN GROUP (
            ORDER BY CONCAT(
                vendor_product,
                ' qty=',
                quantity::VARCHAR,
                ' amount=',
                ROUND(amount, 2)::VARCHAR
            )
        ) AS vendor_product_breakdown
    FROM proofpoint_int
    GROUP BY 1, 2, 3
),

vendor_group_cw_skus AS (
    SELECT
        p.sf_id,
        p.billing_month,
        p.sku_match_group,
        ARRAY_AGG(DISTINCT f.value::VARCHAR)
            WITHIN GROUP (ORDER BY f.value::VARCHAR) AS cw_skus
    FROM proofpoint_int p,
         LATERAL FLATTEN(input => p.cw_skus) f
    GROUP BY 1, 2, 3
),

vendor_group_invoice_skus AS (
    SELECT
        p.sf_id,
        p.billing_month,
        p.sku_match_group,
        ARRAY_AGG(DISTINCT f.value::VARCHAR)
            WITHIN GROUP (ORDER BY f.value::VARCHAR) AS vendor_skus_invoices
    FROM proofpoint_int p,
         LATERAL FLATTEN(input => p.vendor_skus_invoices) f
    GROUP BY 1, 2, 3
),

vendor_agg AS (
    SELECT
        b.sf_id,
        b.billing_month,
        b.sku_match_group,
        b.vendor_product,
        c.cw_skus,
        i.vendor_skus_invoices,
        b.vendor_quantity,
        b.vendor_unit_price,
        b.vendor_amount,
        b.vendor_row_count,
        b.vendor_partner_name,
        b.partner_match_methods,
        b.vendor_entities,
        b.currencies,
        b.sku_mapping_sources,
        b.vendor_product_breakdown,
        b.contract_cost_basis_quantity,
        b.contract_cost_basis_amount,
        b.contract_rate_missing_row_count,
        b.contract_rate_source_docs
    FROM vendor_group_base b
    LEFT JOIN vendor_group_cw_skus c
        ON c.sf_id = b.sf_id
       AND c.billing_month = b.billing_month
       AND c.sku_match_group = b.sku_match_group
    LEFT JOIN vendor_group_invoice_skus i
        ON i.sf_id = b.sf_id
       AND i.billing_month = b.billing_month
       AND i.sku_match_group = b.sku_match_group
),

proofpoint_cw_sku_tokens AS (
    SELECT DISTINCT
        UPPER(TRIM(tok.value)) AS cw_sku_token
    FROM (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Proofpoint') sm,
         LATERAL SPLIT_TO_TABLE(REPLACE(sm.cw_sku, '/', '|'), '|') tok
    WHERE sm.cw_sku IS NOT NULL
      AND TRIM(tok.value) <> ''
),

zuora_source AS (
    SELECT
        sf_id,
        UPPER(TRIM(product_sku)) AS product_sku,
        billing_month::DATE AS billing_month,
        COALESCE(qty, 0) AS zuora_quantity,
        COALESCE(unit_price_usd, 0) AS zuora_unit_price,
        COALESCE(charge_amount_usd, 0) AS zuora_charge_amount
    FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
    WHERE vendor = 'Proofpoint'
      AND sf_id ILIKE 'ACT-%'
            AND COALESCE(qty, 0) <> 0
),

marketplace_source AS (
    SELECT
        sf_id,
        UPPER(TRIM(product_sku)) AS product_sku,
        billing_month::DATE AS billing_month,
                transaction_source,
        COALESCE(qty, 0) AS marketplace_quantity,
        COALESCE(amount, 0) AS marketplace_amount
    FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
    WHERE vendor = 'Proofpoint'
      AND sf_id ILIKE 'ACT-%'
            AND (
                        COALESCE(qty, 0) <> 0
                 OR (transaction_source = 'Salesforce Contract' AND COALESCE(amount, 0) <> 0)
            )
            AND UPPER(TRIM(product_sku)) IN (SELECT cw_sku_token FROM proofpoint_cw_sku_tokens)
),

zuora_proofpoint AS (
    SELECT
        sf_id,
        product_sku,
        billing_month,
        SUM(zuora_quantity) AS zuora_quantity,
        AVG(zuora_unit_price) AS zuora_unit_price,
        SUM(zuora_charge_amount) AS zuora_amount
    FROM zuora_source
    GROUP BY 1, 2, 3
),

marketplace_billing AS (
    SELECT
        sf_id,
        product_sku,
        billing_month,
        SUM(marketplace_quantity) AS marketplace_quantity,
        SUM(marketplace_amount) AS marketplace_amount
    FROM marketplace_source
    GROUP BY 1, 2, 3
),

zuora_grouped AS (
    SELECT
        v.sf_id,
        v.billing_month,
        v.sku_match_group,
        ARRAY_AGG(DISTINCT z.product_sku) WITHIN GROUP (ORDER BY z.product_sku) AS zuora_skus,
        SUM(z.zuora_quantity) AS zuora_quantity,
        AVG(z.zuora_unit_price) AS zuora_unit_price,
        SUM(z.zuora_amount) AS zuora_amount
    FROM vendor_agg v
    LEFT JOIN zuora_proofpoint z
        ON z.sf_id = v.sf_id
       AND LAST_DAY(z.billing_month) = LAST_DAY(v.billing_month)
       AND ARRAY_CONTAINS(z.product_sku::VARIANT, v.cw_skus)
    GROUP BY 1, 2, 3
),

marketplace_grouped AS (
    SELECT
        v.sf_id,
        v.billing_month,
        v.sku_match_group,
        ARRAY_AGG(DISTINCT m.product_sku) WITHIN GROUP (ORDER BY m.product_sku) AS marketplace_skus,
        SUM(m.marketplace_quantity) AS marketplace_quantity,
        SUM(m.marketplace_amount) AS marketplace_amount
    FROM vendor_agg v
    LEFT JOIN marketplace_billing m
        ON m.sf_id = v.sf_id
       AND LAST_DAY(m.billing_month) = LAST_DAY(v.billing_month)
       AND ARRAY_CONTAINS(m.product_sku::VARIANT, v.cw_skus)
    GROUP BY 1, 2, 3
),

zuora_any_sf_month AS (
    SELECT
        sf_id,
        billing_month,
        SUM(zuora_quantity) AS any_zuora_quantity,
        SUM(zuora_charge_amount) AS any_zuora_amount,
        COUNT(*) AS any_zuora_row_count,
        ARRAY_AGG(DISTINCT product_sku) WITHIN GROUP (ORDER BY product_sku) AS any_zuora_skus
    FROM zuora_source
    GROUP BY 1, 2
),

marketplace_any_sf_month AS (
    SELECT
        sf_id,
        billing_month,
        SUM(marketplace_quantity) AS any_marketplace_quantity,
        SUM(marketplace_amount) AS any_marketplace_amount,
        COUNT(*) AS any_marketplace_row_count,
        ARRAY_AGG(DISTINCT product_sku) WITHIN GROUP (ORDER BY product_sku) AS any_marketplace_skus
    FROM marketplace_source
    GROUP BY 1, 2
),

marketplace_prior_sf_month AS (
    SELECT
        sf_id,
        DATEADD(month, 1, billing_month) AS billing_month,
        SUM(marketplace_quantity) AS prior_month_marketplace_quantity,
        SUM(marketplace_amount) AS prior_month_marketplace_amount,
        COUNT(*) AS prior_month_marketplace_row_count,
        ARRAY_AGG(DISTINCT product_sku) WITHIN GROUP (ORDER BY product_sku) AS prior_month_marketplace_skus
    FROM marketplace_source
    GROUP BY 1, 2
),

zuora_nearby_sf AS (
    SELECT
        current_month.sf_id,
        current_month.billing_month,
        COUNT(DISTINCT nearby.billing_month) AS nearby_zuora_month_count,
        SUM(nearby.zuora_quantity) AS nearby_zuora_quantity
    FROM (
        SELECT DISTINCT sf_id, billing_month
        FROM vendor_agg
        WHERE sf_id IS NOT NULL
    ) current_month
    LEFT JOIN zuora_source nearby
        ON nearby.sf_id = current_month.sf_id
       AND nearby.billing_month BETWEEN DATEADD(month, -2, current_month.billing_month)
                                   AND DATEADD(month, 2, current_month.billing_month)
       AND nearby.billing_month <> current_month.billing_month
    GROUP BY 1, 2
),

joined AS (
    SELECT
        v.billing_month,
        v.sf_id,
        v.sku_match_group,
        v.vendor_partner_name,
        v.vendor_product,
        v.cw_skus,
        v.vendor_skus_invoices,
        zg.zuora_skus,
        mg.marketplace_skus,
        v.vendor_quantity,
        v.vendor_unit_price,
        v.vendor_amount,
        zg.zuora_quantity,
        zg.zuora_unit_price,
        zg.zuora_amount,
        mg.marketplace_quantity,
        mg.marketplace_amount,
        za.any_zuora_quantity,
        za.any_zuora_amount,
        za.any_zuora_row_count,
        za.any_zuora_skus,
        ma.any_marketplace_quantity,
        ma.any_marketplace_amount,
        ma.any_marketplace_row_count,
        ma.any_marketplace_skus,
        mp.prior_month_marketplace_quantity,
        mp.prior_month_marketplace_amount,
        mp.prior_month_marketplace_row_count,
        mp.prior_month_marketplace_skus,
        zn.nearby_zuora_month_count,
        zn.nearby_zuora_quantity,
        v.vendor_row_count,
        v.partner_match_methods,
        v.sku_mapping_sources,
        v.vendor_product_breakdown,
        v.contract_cost_basis_quantity,
        v.contract_cost_basis_amount,
        v.contract_rate_missing_row_count,
        v.contract_rate_source_docs
    FROM vendor_agg v
    LEFT JOIN zuora_grouped zg
        ON zg.sf_id = v.sf_id
       AND zg.billing_month = v.billing_month
       AND zg.sku_match_group = v.sku_match_group
    LEFT JOIN marketplace_grouped mg
        ON mg.sf_id = v.sf_id
       AND mg.billing_month = v.billing_month
       AND mg.sku_match_group = v.sku_match_group
    LEFT JOIN zuora_any_sf_month za
        ON za.sf_id = v.sf_id
       AND LAST_DAY(za.billing_month) = LAST_DAY(v.billing_month)
    LEFT JOIN marketplace_any_sf_month ma
        ON ma.sf_id = v.sf_id
       AND LAST_DAY(ma.billing_month) = LAST_DAY(v.billing_month)
    LEFT JOIN marketplace_prior_sf_month mp
        ON mp.sf_id = v.sf_id
       AND LAST_DAY(mp.billing_month) = LAST_DAY(v.billing_month)
    LEFT JOIN zuora_nearby_sf zn
        ON zn.sf_id = v.sf_id
       AND LAST_DAY(zn.billing_month) = LAST_DAY(v.billing_month)
),

scored AS (
    SELECT
        billing_month::DATE AS billing_month,
        sf_id,
        sku_match_group,
        vendor_partner_name,
        vendor_product,
        cw_skus,
        vendor_skus_invoices,
        zuora_skus,
        marketplace_skus,
        CASE
            WHEN (COALESCE(zuora_quantity, 0) > 0 OR COALESCE(any_zuora_quantity, 0) > 0)
                 AND marketplace_quantity IS NOT NULL THEN 'ZUORA_AND_MARKETPLACE'
            WHEN (COALESCE(zuora_quantity, 0) > 0 OR COALESCE(any_zuora_quantity, 0) > 0) THEN 'ZUORA_ONLY'
            WHEN marketplace_quantity IS NOT NULL THEN 'MARKETPLACE_ONLY'
            ELSE 'NO_BILLING_SOURCE'
        END AS source_presence_flag,
        vendor_quantity,
        vendor_unit_price,
        vendor_amount,
        zuora_quantity,
        zuora_unit_price,
        zuora_amount,
        marketplace_quantity,
        marketplace_amount,
        CASE
            WHEN COALESCE(
                    IFF(COALESCE(zuora_quantity, 0) > 0, zuora_quantity,
                        IFF(COALESCE(any_zuora_quantity, 0) > 0, any_zuora_quantity, marketplace_quantity)
                    ),
                    0
                 ) = 0
             AND COALESCE(
                    IFF(COALESCE(zuora_amount, 0) > 0, zuora_amount,
                        IFF(COALESCE(any_zuora_amount, 0) > 0, any_zuora_amount, marketplace_amount)
                    ),
                    0
                 ) > 0
             AND UPPER(COALESCE(vendor_product, '')) IN ('BASIC OEM', 'ADVANCED OEM')
                THEN COALESCE(vendor_quantity, 0)
            ELSE COALESCE(
                    IFF(COALESCE(zuora_quantity, 0) > 0, zuora_quantity,
                        IFF(COALESCE(any_zuora_quantity, 0) > 0, any_zuora_quantity, marketplace_quantity)
                    ),
                    0
                 )
        END AS total_billing_quantity,
        COALESCE(
            IFF(COALESCE(zuora_amount, 0) > 0, zuora_amount,
                IFF(COALESCE(any_zuora_amount, 0) > 0, any_zuora_amount, marketplace_amount)
            ),
            0
        ) AS total_billing_amount,
        CASE
            WHEN COALESCE(
                    IFF(COALESCE(zuora_quantity, 0) > 0, zuora_quantity,
                        IFF(COALESCE(any_zuora_quantity, 0) > 0, any_zuora_quantity, marketplace_quantity)
                    ),
                    0
                 ) = 0
             AND COALESCE(
                    IFF(COALESCE(zuora_amount, 0) > 0, zuora_amount,
                        IFF(COALESCE(any_zuora_amount, 0) > 0, any_zuora_amount, marketplace_amount)
                    ),
                    0
                 ) > 0
             AND UPPER(COALESCE(vendor_product, '')) IN ('BASIC OEM', 'ADVANCED OEM')
                THEN 0
            ELSE COALESCE(
                    IFF(COALESCE(zuora_quantity, 0) > 0, zuora_quantity,
                        IFF(COALESCE(any_zuora_quantity, 0) > 0, any_zuora_quantity, marketplace_quantity)
                    ),
                    0
                 ) - COALESCE(vendor_quantity, 0)
        END AS qty_delta,
        COALESCE(
            IFF(COALESCE(zuora_amount, 0) > 0, zuora_amount,
                IFF(COALESCE(any_zuora_amount, 0) > 0, any_zuora_amount, marketplace_amount)
            ),
            0
        ) - COALESCE(vendor_amount, 0) AS amount_delta,
        vendor_row_count,
        partner_match_methods,
        sku_mapping_sources,
        vendor_product_breakdown,
        any_zuora_quantity,
        any_zuora_amount,
        any_zuora_row_count,
        any_zuora_skus,
        any_marketplace_quantity,
        any_marketplace_amount,
        any_marketplace_row_count,
        any_marketplace_skus,
        prior_month_marketplace_quantity,
        prior_month_marketplace_amount,
        prior_month_marketplace_row_count,
        prior_month_marketplace_skus,
        nearby_zuora_month_count,
        nearby_zuora_quantity,
        -- Contract-cost carry-through (rate lookup already applied at usage grain).
        contract_cost_basis_quantity,
        contract_cost_basis_amount,
        contract_rate_missing_row_count,
        contract_rate_source_docs
    FROM joined
),

matched_same_sku_history AS (
    SELECT
        a.billing_month,
        a.sf_id,
        a.sku_match_group,
        COUNT(DISTINCT b.billing_month) AS matched_history_month_count,
        COUNT(DISTINCT IFF(b.billing_month < a.billing_month, b.billing_month, NULL))
            AS prior_matched_history_month_count,
        COUNT(DISTINCT IFF(b.billing_month > a.billing_month, b.billing_month, NULL))
            AS later_matched_history_month_count,
        MAX(IFF(b.billing_month < a.billing_month, b.billing_month, NULL))
            AS last_prior_matched_month,
        MIN(IFF(b.billing_month > a.billing_month, b.billing_month, NULL))
            AS next_later_matched_month
    FROM scored a
    INNER JOIN scored b
        ON b.sf_id = a.sf_id
       AND b.sku_match_group = a.sku_match_group
       AND b.billing_month <> a.billing_month
       AND b.total_billing_quantity = b.vendor_quantity
    WHERE a.sf_id IS NOT NULL
    GROUP BY 1, 2, 3
),

sku_merge_candidates AS (
    SELECT
        a.billing_month,
        a.sf_id,
        a.vendor_product,
        b.vendor_product AS best_sku_merge_target_product,
        b.cw_skus AS best_sku_merge_target_cw_skus,
        ABS(a.qty_delta) + ABS(b.qty_delta) AS best_sku_merge_old_abs_qty,
        ABS(b.total_billing_quantity - (b.vendor_quantity + a.vendor_quantity))
            AS best_sku_merge_new_abs_qty,
        ABS(a.qty_delta) + ABS(b.qty_delta)
            - ABS(b.total_billing_quantity - (b.vendor_quantity + a.vendor_quantity))
            AS best_sku_merge_possible_improvement_qty
    FROM scored a
    JOIN scored b
        ON b.billing_month = a.billing_month
       AND b.sf_id = a.sf_id
       AND b.vendor_product <> a.vendor_product
    WHERE a.zuora_quantity IS NULL
      AND a.marketplace_quantity IS NULL
      AND b.total_billing_quantity > 0
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY a.billing_month, a.sf_id, a.vendor_product
        ORDER BY best_sku_merge_possible_improvement_qty DESC, best_sku_merge_new_abs_qty ASC
    ) = 1
),

scored_with_mapping_evidence AS (
    SELECT
        s.*,
        m.best_sku_merge_target_product,
        m.best_sku_merge_target_cw_skus,
        m.best_sku_merge_old_abs_qty,
        m.best_sku_merge_new_abs_qty,
        m.best_sku_merge_possible_improvement_qty,
        COALESCE(m.best_sku_merge_possible_improvement_qty, 0) > 0
            AS sku_mapping_candidate_flag,
        COALESCE(h.matched_history_month_count, 0) AS matched_history_month_count,
        COALESCE(h.prior_matched_history_month_count, 0) AS prior_matched_history_month_count,
        COALESCE(h.later_matched_history_month_count, 0) AS later_matched_history_month_count,
        h.last_prior_matched_month,
        h.next_later_matched_month,
        -- Inline TRT API rollup (see proofpoint_api_usage CTE above).
        -- Grain: (sf_id, billing_month, sku_match_group).
        au.api_quantity::FLOAT      AS api_quantity,
        au.avg_api_quantity::FLOAT  AS avg_api_quantity
    FROM scored s
    LEFT JOIN sku_merge_candidates m
        ON m.billing_month = s.billing_month
       AND m.sf_id = s.sf_id
       AND m.vendor_product = s.vendor_product
    LEFT JOIN matched_same_sku_history h
        ON h.billing_month = s.billing_month
       AND h.sf_id = s.sf_id
       AND h.sku_match_group = s.sku_match_group
    LEFT JOIN proofpoint_api_usage au
        ON au.sf_id          = s.sf_id
       AND au.billing_month  = s.billing_month
       AND au.sku_match_group = s.sku_match_group
),

-- =============================================================================
-- detail_pre: raw classification (outcome_flag, investigation_reason,
-- billing_action_required, contract-price overlay). The outer SELECT below
-- applies the 2026-07-31 quantity-noise gate and adds vendor unit-price vs
-- contract-rate audit columns.
-- =============================================================================
detail_pre AS (
SELECT
    billing_month,
    sf_id,
    vendor_partner_name,
    vendor_product AS vendor_product,
    cw_skus,
    zuora_skus,
    marketplace_skus,
    source_presence_flag AS billing_source_mix,
    api_quantity,
    avg_api_quantity,
    vendor_quantity,
    vendor_unit_price,
    vendor_amount,
    zuora_quantity,
    zuora_unit_price,
    zuora_amount,
    marketplace_quantity,
    marketplace_amount,
    total_billing_quantity,
    total_billing_amount / NULLIF(total_billing_quantity, 0) AS total_billing_unit_price,
    total_billing_amount,
    qty_delta,
    ABS(qty_delta) AS abs_qty_delta,
    amount_delta,
    ABS(amount_delta) AS abs_amount_delta,
    CASE
        WHEN sf_id IS NULL THEN 'PARTNER_MAPPING_REQUIRED'
        WHEN zuora_quantity IS NOT NULL AND marketplace_quantity IS NOT NULL THEN 'DUPLICATE_BILLING'
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL
         AND COALESCE(prior_month_marketplace_quantity, 0) > 0 THEN 'MARKETPLACE_TIMING'
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL
         AND matched_history_month_count > 0 THEN 'KNOWN_GOOD_MAPPING_MISSING_CURRENT_BILLING'
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL
         AND (
            COALESCE(any_zuora_row_count, 0) > 0
            OR COALESCE(any_marketplace_row_count, 0) > 0
         ) THEN 'SKU_MISMATCH_BILLING_ON_OTHER_SKU'
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL
         AND COALESCE(nearby_zuora_month_count, 0) > 0 THEN 'CONTRACT_TIMING_OR_INACTIVE'
        -- Vendor-only with no timing/history evidence: split by materiality.
        -- Aligns with S1's STRUCTURAL_VENDOR_ONLY_NO_CONTRACT so both pipelines
        -- surface material vendor-only rows the same way.
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL
         AND COALESCE(vendor_quantity, 0) >= 50 THEN 'STRUCTURAL_VENDOR_ONLY_NO_CONTRACT'
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL THEN 'NO_BILLING_NO_HISTORY'
        -- Amount-safe: CW billed >= vendor cost (existing preservation rule).
        WHEN COALESCE(total_billing_amount, 0) >= COALESCE(vendor_amount, 0) THEN 'CLEAR'
        -- Tiered granular differential (aligned with S1 thresholds):
        --   CLEAR:                      |Delta_qty| <= max(5, 2% * vendor_qty)
        --   MINOR_DRIFT:                |Delta_qty| <= max(25, 5% * vendor_qty)
        --   BILLING_DIFFERENTIAL_*:     5% - 25% of vendor_qty
        --   MATERIAL_*_VENDOR:          > 25% of vendor_qty
        WHEN ABS(qty_delta) <= GREATEST(5, 0.02 * COALESCE(vendor_quantity, 0)) THEN 'CLEAR'
        WHEN ABS(qty_delta) <= GREATEST(25, 0.05 * COALESCE(vendor_quantity, 0)) THEN 'MINOR_DRIFT'
        WHEN qty_delta > 0 AND ABS(qty_delta) <= 0.25 * COALESCE(vendor_quantity, 0) THEN 'BILLING_DIFFERENTIAL_OVER'
        WHEN qty_delta < 0 AND ABS(qty_delta) <= 0.25 * COALESCE(vendor_quantity, 0) THEN 'BILLING_DIFFERENTIAL_UNDER'
        WHEN qty_delta > 0 THEN 'MATERIAL_OVER_VENDOR'
        WHEN qty_delta < 0 THEN 'MATERIAL_UNDER_VENDOR'
        ELSE 'REVIEW_EXCEPTION'
    END AS outcome_flag,
    zuora_quantity IS NOT NULL
        AND marketplace_quantity IS NOT NULL AS duplicate_billing_flag,
    zuora_quantity IS NULL
        AND marketplace_quantity IS NULL
        AND COALESCE(prior_month_marketplace_quantity, 0) > 0 AS marketplace_timing_flag,
    CASE
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL
         AND COALESCE(prior_month_marketplace_quantity, 0) > 0
            THEN COALESCE(prior_month_marketplace_quantity, 0)
        ELSE 0
    END AS marketplace_timing_quantity,
    CASE
        WHEN sf_id IS NULL THEN 'Add or correct the partner mapping.'
        WHEN zuora_quantity IS NOT NULL AND marketplace_quantity IS NOT NULL THEN 'Both Zuora and Marketplace billed this account/product/month; review for duplicate billing.'
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL
         AND COALESCE(prior_month_marketplace_quantity, 0) > 0 THEN 'No current-month billing found, but prior-month Marketplace billing exists; monitor as likely Marketplace timing.'
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL
         AND matched_history_month_count > 0 THEN 'No current-month billing matched this vendor SKU group, but the same account/product has matched cleanly in another month; review missed billing, service dates, or subscription activity.'
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL
         AND (
            COALESCE(any_zuora_row_count, 0) > 0
            OR COALESCE(any_marketplace_row_count, 0) > 0
         ) THEN 'No billing matched this vendor SKU group, but the same account/month has Proofpoint billing on other SKU(s); review SKU mapping or billing setup.'
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL
         AND COALESCE(nearby_zuora_month_count, 0) > 0 THEN 'No current-month billing found, but nearby Zuora billing exists; review contract timing, inactive subscription, or service dates.'
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL
         AND COALESCE(vendor_quantity, 0) >= 50 THEN 'Vendor usage is material but no CW billing, prior-month, history, or nearby contract evidence exists; likely a contract/setup gap - route to Ops for coverage review.'
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL THEN 'No current billing, matched clean history, same-month other-SKU billing, Marketplace timing, or nearby Zuora timing evidence found; low materiality, investigate source coverage or new unbilled product.'
        WHEN COALESCE(total_billing_amount, 0) >= COALESCE(vendor_amount, 0) THEN 'CLEAR: CW billed amount meets or exceeds vendor amount.'
        WHEN ABS(qty_delta) <= GREATEST(5, 0.02 * COALESCE(vendor_quantity, 0)) THEN 'CLEAR: billing quantity within 2% (or 5 units) of vendor usage.'
        WHEN ABS(qty_delta) <= GREATEST(25, 0.05 * COALESCE(vendor_quantity, 0)) THEN 'MINOR_DRIFT: billing quantity within 5% (or 25 units); typically noise or timing.'
        WHEN qty_delta > 0 AND ABS(qty_delta) <= 0.25 * COALESCE(vendor_quantity, 0) THEN 'Billing quantity exceeds vendor usage by 5-25%; review overbilling or stale billing.'
        WHEN qty_delta < 0 AND ABS(qty_delta) <= 0.25 * COALESCE(vendor_quantity, 0) THEN 'Vendor usage exceeds billing quantity by 5-25%; review underbilling.'
        WHEN qty_delta > 0 THEN 'MATERIAL overbilling: billing quantity exceeds vendor usage by more than 25%.'
        WHEN qty_delta < 0 THEN 'MATERIAL underbilling: vendor usage exceeds billing quantity by more than 25%.'
        ELSE 'Review exception.'
    END AS investigation_reason,
    CASE
        WHEN zuora_quantity IS NULL
         AND marketplace_quantity IS NULL
         AND COALESCE(prior_month_marketplace_quantity, 0) = 0
         AND matched_history_month_count > 0
            THEN TRUE
        WHEN COALESCE(total_billing_amount, 0) < COALESCE(vendor_amount, 0)
         AND NOT (
            zuora_quantity IS NULL
            AND marketplace_quantity IS NULL
            AND (
                COALESCE(prior_month_marketplace_quantity, 0) > 0
                OR (
                    COALESCE(any_zuora_row_count, 0) > 0
                    AND sku_mapping_candidate_flag
                )
                OR COALESCE(any_marketplace_row_count, 0) > 0
                OR (
                    COALESCE(any_zuora_row_count, 0) = 0
                    AND COALESCE(any_marketplace_row_count, 0) = 0
                    AND COALESCE(nearby_zuora_month_count, 0) > 0
                )
            )
         )
            THEN TRUE
        ELSE FALSE
    END AS billing_action_required,
    vendor_row_count AS vendor_source_row_count,
    partner_match_methods,
    sku_mapping_sources,
    -- ------------------------------------------------------------------
    -- Contract Price Overlay
    -- ------------------------------------------------------------------
    -- contract_cost_rate is the vendor-quantity-weighted average per-seat
    -- cost, derived by joining PROOFPOINT_CONTRACT_RATES to each usage row
    -- on (vendor_product, currency, billing_month) inside sku_candidates.
    -- It is NULL when the recon row's product/currency/month is unmapped
    -- in the rate lookup, or when only partial rate coverage exists (e.g. a
    -- multi-product sku_match_group where one product has no governed rate).
    contract_cost_basis_quantity,
    contract_cost_basis_amount,
    CASE
        WHEN COALESCE(contract_cost_basis_quantity, 0) = 0 THEN NULL
        WHEN COALESCE(contract_rate_missing_row_count, 0) > 0 THEN NULL
        ELSE contract_cost_basis_amount / NULLIF(contract_cost_basis_quantity, 0)
    END AS contract_cost_rate,
    CASE
        WHEN COALESCE(contract_cost_basis_quantity, 0) = 0 THEN NULL
        WHEN COALESCE(contract_rate_missing_row_count, 0) > 0 THEN NULL
        WHEN COALESCE(total_billing_quantity, 0) = 0 THEN NULL
        ELSE (total_billing_amount / NULLIF(total_billing_quantity, 0))
             - (contract_cost_basis_amount / NULLIF(contract_cost_basis_quantity, 0))
    END AS billing_vs_cost_delta_per_seat,
    CASE
        WHEN COALESCE(contract_cost_basis_quantity, 0) = 0 THEN NULL
        WHEN COALESCE(contract_rate_missing_row_count, 0) > 0 THEN NULL
        WHEN COALESCE(total_billing_quantity, 0) = 0 THEN NULL
        ELSE (
            (total_billing_amount / NULLIF(total_billing_quantity, 0))
            - (contract_cost_basis_amount / NULLIF(contract_cost_basis_quantity, 0))
        ) * total_billing_quantity
    END AS billing_vs_cost_dollar_impact,
    CASE
        WHEN COALESCE(contract_cost_basis_quantity, 0) = 0 THEN NULL
        WHEN COALESCE(contract_rate_missing_row_count, 0) > 0 THEN NULL
        WHEN COALESCE(total_billing_quantity, 0) = 0 THEN NULL
        ELSE (
            (total_billing_amount / NULLIF(total_billing_quantity, 0))
            - (contract_cost_basis_amount / NULLIF(contract_cost_basis_quantity, 0))
        ) / NULLIF(contract_cost_basis_amount / NULLIF(contract_cost_basis_quantity, 0), 0)
    END AS billing_vs_cost_pct,
    CASE
        WHEN sf_id IS NULL THEN 'PARTNER_MAPPING_REQUIRED'
        WHEN COALESCE(total_billing_quantity, 0) = 0 THEN 'NO_BILLING_PRICE'
        WHEN COALESCE(contract_cost_basis_quantity, 0) = 0
          OR COALESCE(contract_rate_missing_row_count, 0) > 0 THEN 'NO_CONTRACT_RATE'
        WHEN ABS(
                (total_billing_amount / NULLIF(total_billing_quantity, 0))
                - (contract_cost_basis_amount / NULLIF(contract_cost_basis_quantity, 0))
             ) < 0.02 THEN 'AT_COST'
        WHEN (total_billing_amount / NULLIF(total_billing_quantity, 0))
             > (contract_cost_basis_amount / NULLIF(contract_cost_basis_quantity, 0))
            THEN 'ABOVE_COST'
        ELSE 'BELOW_COST_DISCOUNT'
    END AS contract_price_flag,
    -- Materiality gate for BELOW_COST rows: filters out sub-penny rate noise
    -- from tiered/rounded billing so the leadership queue focuses on real losses.
    CASE
        WHEN COALESCE(contract_cost_basis_quantity, 0) = 0
          OR COALESCE(contract_rate_missing_row_count, 0) > 0 THEN FALSE
        WHEN (total_billing_amount / NULLIF(total_billing_quantity, 0))
             >= (contract_cost_basis_amount / NULLIF(contract_cost_basis_quantity, 0))
            THEN FALSE
        WHEN ABS(
                (total_billing_amount / NULLIF(total_billing_quantity, 0))
                - (contract_cost_basis_amount / NULLIF(contract_cost_basis_quantity, 0))
             ) < 0.10
         AND ABS(
                (
                    (total_billing_amount / NULLIF(total_billing_quantity, 0))
                    - (contract_cost_basis_amount / NULLIF(contract_cost_basis_quantity, 0))
                ) * total_billing_quantity
             ) < 50 THEN FALSE
        ELSE TRUE
    END AS material_below_cost_flag,
    contract_rate_source_docs,
    CURRENT_TIMESTAMP() AS recon_run_ts
FROM scored_with_mapping_evidence
)

-- =============================================================================
-- Final SELECT: apply 2026-07-31 quantity-noise gate + vendor rate audit.
--
-- Business rule (leadership request 2026-07-31):
--   Quantity variance should NOT be flagged unless BOTH:
--     (a) exposure amount (GREATEST of vendor $ vs CW billed $) > $300, AND
--     (b) quantity variance is >= 3% of vendor seats.
--   Applies to every real-differential / vendor-only lane:
--     KNOWN_GOOD_MAPPING_MISSING_CURRENT_BILLING, NO_BILLING_NO_HISTORY,
--     CONTRACT_TIMING_OR_INACTIVE, STRUCTURAL_VENDOR_ONLY_NO_CONTRACT,
--     BILLING_DIFFERENTIAL_OVER/UNDER, MATERIAL_OVER/UNDER_VENDOR.
--   SKU_MISMATCH is a routing exception, not a quantity variance, so it is
--   NOT gated. MINOR_DRIFT is already sub-threshold and is not re-labeled.
--
-- Vendor rate audit (new):
--   Compares vendor_unit_price (what Proofpoint invoiced CW per seat) against
--   the governed contract_cost_rate. Buckets each row as OVER_CONTRACT /
--   UNDER_CONTRACT / EVEN / NO_CONTRACT_RATE / NO_VENDOR_PRICE.
--   EVEN band: within +/- $0.01/seat AND +/- 1% of contract rate.
--
-- Combined-system column:
--   'Proofpoint' is emitted as VENDOR (position 1) so the shared
--   THIRD_PARTY_RECON_OUTPUT_PROD mart can UNION ALL every vendor with the
--   discriminator baked in.
-- =============================================================================
SELECT
    'Proofpoint'::VARCHAR AS vendor,
    * EXCLUDE (outcome_flag, investigation_reason, billing_action_required),
    CASE
        WHEN outcome_flag IN (
            'KNOWN_GOOD_MAPPING_MISSING_CURRENT_BILLING',
            'NO_BILLING_NO_HISTORY',
            'CONTRACT_TIMING_OR_INACTIVE',
            'STRUCTURAL_VENDOR_ONLY_NO_CONTRACT',
            'BILLING_DIFFERENTIAL_OVER',
            'BILLING_DIFFERENTIAL_UNDER',
            'MATERIAL_OVER_VENDOR',
            'MATERIAL_UNDER_VENDOR'
        )
         AND NOT (
            GREATEST(COALESCE(vendor_amount, 0), COALESCE(total_billing_amount, 0)) > 300
            AND ABS(qty_delta)
                / NULLIF(GREATEST(COALESCE(vendor_quantity, 0), 1), 0) >= 0.03
         )
            THEN 'CLEAR'
        ELSE outcome_flag
    END AS outcome_flag,
    CASE
        WHEN outcome_flag IN (
            'KNOWN_GOOD_MAPPING_MISSING_CURRENT_BILLING',
            'NO_BILLING_NO_HISTORY',
            'CONTRACT_TIMING_OR_INACTIVE',
            'STRUCTURAL_VENDOR_ONLY_NO_CONTRACT',
            'BILLING_DIFFERENTIAL_OVER',
            'BILLING_DIFFERENTIAL_UNDER',
            'MATERIAL_OVER_VENDOR',
            'MATERIAL_UNDER_VENDOR'
        )
         AND NOT (
            GREATEST(COALESCE(vendor_amount, 0), COALESCE(total_billing_amount, 0)) > 300
            AND ABS(qty_delta)
                / NULLIF(GREATEST(COALESCE(vendor_quantity, 0), 1), 0) >= 0.03
         )
            THEN 'CLEAR: quantity variance below noise floor ($300 dollar exposure and 3% seat variance threshold; business rule 2026-07-31).'
        ELSE investigation_reason
    END AS investigation_reason,
    billing_action_required
        AND (
            outcome_flag NOT IN (
                'KNOWN_GOOD_MAPPING_MISSING_CURRENT_BILLING',
                'NO_BILLING_NO_HISTORY',
                'CONTRACT_TIMING_OR_INACTIVE',
                'STRUCTURAL_VENDOR_ONLY_NO_CONTRACT',
                'BILLING_DIFFERENTIAL_OVER',
                'BILLING_DIFFERENTIAL_UNDER',
                'MATERIAL_OVER_VENDOR',
                'MATERIAL_UNDER_VENDOR'
            )
            OR (
                GREATEST(COALESCE(vendor_amount, 0), COALESCE(total_billing_amount, 0)) > 300
                AND ABS(qty_delta)
                    / NULLIF(GREATEST(COALESCE(vendor_quantity, 0), 1), 0) >= 0.03
            )
        )
        AS billing_action_required,
    -- Vendor unit price vs governed contract rate ----------------------------
    CASE
        WHEN contract_cost_rate IS NULL THEN NULL
        WHEN vendor_unit_price IS NULL THEN NULL
        ELSE vendor_unit_price - contract_cost_rate
    END AS vendor_vs_contract_delta_per_seat,
    CASE
        WHEN contract_cost_rate IS NULL OR contract_cost_rate = 0 THEN NULL
        WHEN vendor_unit_price IS NULL THEN NULL
        ELSE (vendor_unit_price - contract_cost_rate) / NULLIF(contract_cost_rate, 0)
    END AS vendor_vs_contract_pct,
    CASE
        WHEN contract_cost_rate IS NULL THEN 'NO_CONTRACT_RATE'
        WHEN vendor_unit_price IS NULL THEN 'NO_VENDOR_PRICE'
        WHEN ABS(vendor_unit_price - contract_cost_rate) <= 0.01
         AND ABS((vendor_unit_price - contract_cost_rate) / NULLIF(contract_cost_rate, 0)) <= 0.01
            THEN 'EVEN'
        WHEN vendor_unit_price > contract_cost_rate THEN 'OVER_CONTRACT'
        ELSE 'UNDER_CONTRACT'
    END AS vendor_vs_contract_flag,
    CASE
        WHEN contract_cost_rate IS NULL OR vendor_unit_price IS NULL THEN NULL
        ELSE (vendor_unit_price - contract_cost_rate) * COALESCE(vendor_quantity, 0)
    END AS vendor_vs_contract_dollar_impact
FROM detail_pre;

CREATE OR REPLACE TABLE PROOFPOINT_RECON_SUMMARY AS
SELECT
    billing_month,
    COUNT(*) AS total_rows,
    COUNT_IF(outcome_flag = 'CLEAR') AS perfect_match_rows,
    ROUND(COUNT_IF(outcome_flag = 'CLEAR') * 100.0 / COUNT(*), 1) AS perfect_match_pct,
    SUM(ABS(qty_delta)) AS abs_qty_variance,
    SUM(vendor_quantity) AS total_vendor_seats,
    SUM(COALESCE(zuora_quantity, 0)) AS total_zuora_seats,
    SUM(COALESCE(marketplace_quantity, 0)) AS total_marketplace_seats,
    SUM(total_billing_quantity) AS total_billing_seats,
    ROUND(SUM(vendor_amount), 2) AS total_vendor_amount,
    ROUND(SUM(total_billing_amount), 2) AS total_billing_amount,
    COUNT_IF(duplicate_billing_flag) AS duplicate_billing_rows,
    SUM(IFF(duplicate_billing_flag, COALESCE(vendor_quantity, 0), 0)) AS duplicate_billing_vendor_seats,
    SUM(IFF(duplicate_billing_flag, COALESCE(zuora_quantity, 0), 0)) AS duplicate_billing_zuora_seats,
    SUM(IFF(duplicate_billing_flag, COALESCE(marketplace_quantity, 0), 0)) AS duplicate_billing_marketplace_seats,
    SUM(
        IFF(
            duplicate_billing_flag,
            ABS(total_billing_quantity - COALESCE(vendor_quantity, 0))
                - ABS(COALESCE(zuora_quantity, marketplace_quantity, 0) - COALESCE(vendor_quantity, 0)),
            0
        )
    ) AS duplicate_billing_abs_qty_variance_impact,
    ROUND(
        SUM(
            IFF(
                duplicate_billing_flag,
                ABS(total_billing_amount - COALESCE(vendor_amount, 0))
                    - ABS(COALESCE(zuora_amount, marketplace_amount, 0) - COALESCE(vendor_amount, 0)),
                0
            )
        ),
        2
    ) AS duplicate_billing_abs_amount_variance_impact,
    COUNT_IF(outcome_flag = 'PARTNER_MAPPING_REQUIRED') AS unmapped_rows,
    COUNT_IF(billing_source_mix = 'NO_BILLING_SOURCE') AS no_billing_rows,
    COUNT_IF(qty_delta > 0) AS billing_over_rows,
    COUNT_IF(qty_delta < 0) AS vendor_over_rows,
    -- Contract Price Overlay rollups
    COUNT_IF(contract_price_flag = 'ABOVE_COST') AS contract_above_cost_rows,
    COUNT_IF(contract_price_flag = 'AT_COST') AS contract_at_cost_rows,
    COUNT_IF(contract_price_flag = 'BELOW_COST_DISCOUNT') AS contract_below_cost_rows,
    COUNT_IF(contract_price_flag = 'BELOW_COST_DISCOUNT' AND material_below_cost_flag) AS contract_material_below_cost_rows,
    COUNT_IF(contract_price_flag = 'NO_CONTRACT_RATE') AS contract_no_rate_rows,
    ROUND(
        SUM(IFF(contract_price_flag = 'ABOVE_COST', billing_vs_cost_dollar_impact, 0)),
        2
    ) AS contract_above_cost_margin_dollars,
    ROUND(
        SUM(IFF(contract_price_flag = 'BELOW_COST_DISCOUNT', billing_vs_cost_dollar_impact, 0)),
        2
    ) AS contract_below_cost_loss_dollars,
    ROUND(
        SUM(IFF(contract_price_flag = 'BELOW_COST_DISCOUNT' AND material_below_cost_flag,
                billing_vs_cost_dollar_impact, 0)),
        2
    ) AS contract_material_below_cost_loss_dollars
FROM PROOFPOINT_RECON_DETAIL
GROUP BY 1
ORDER BY 1;

CREATE OR REPLACE TABLE PROOFPOINT_RAW_PARTNER_COVERAGE AS
SELECT
    billing_month,
    SUM(vendor_source_row_count) AS raw_rows_after_scope,
    SUM(IFF(sf_id IS NOT NULL, vendor_source_row_count, 0)) AS mapped_rows,
    ROUND(
        SUM(IFF(sf_id IS NOT NULL, vendor_source_row_count, 0)) / NULLIF(SUM(vendor_source_row_count), 0),
        4
    ) AS row_mapped_rate,
    SUM(COALESCE(vendor_quantity, 0)) AS vendor_seats_after_scope,
    SUM(IFF(sf_id IS NOT NULL, COALESCE(vendor_quantity, 0), 0)) AS mapped_vendor_seats,
    ROUND(
        SUM(IFF(sf_id IS NOT NULL, COALESCE(vendor_quantity, 0), 0))
        / NULLIF(SUM(COALESCE(vendor_quantity, 0)), 0),
        4
    ) AS seat_mapped_rate,
    ROUND(SUM(COALESCE(vendor_amount, 0)), 2) AS vendor_amount_after_scope,
    ROUND(SUM(IFF(sf_id IS NOT NULL, COALESCE(vendor_amount, 0), 0)), 2) AS mapped_vendor_amount,
    ROUND(
        SUM(IFF(sf_id IS NOT NULL, COALESCE(vendor_amount, 0), 0))
        / NULLIF(SUM(COALESCE(vendor_amount, 0)), 0),
        4
    ) AS amount_mapped_rate
FROM PROOFPOINT_RECON_DETAIL
GROUP BY 1
ORDER BY 1;

