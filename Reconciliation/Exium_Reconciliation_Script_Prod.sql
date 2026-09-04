-- =============================================================================
-- STEP 2: EXIUM FINAL RECONCILIATION
-- =============================================================================
-- Proofpoint-style reconciliation adapted for Exium:
--   Vendor side: shared vendor usage filtered to Exium, retaining raw QUANTITY and
--                OVERAGE_QUANTITY, while reconciling on billed quantity
--                recovered from AMOUNT / UNIT_PRICE when available.
--   Billing side: Zuora is the primary reconciliation source; Marketplace is
--                 retained as fallback when Zuora is absent and as overlap
--                 evidence when both sources exist.
--   Grain: sf_id + billing_month + Exium product family match group.
--          CW/CMS usage files are deduped only when the source rows are exact
--          mirrors; distinct entity rows remain in scope.
--   SKU map columns use Exium naming: EXIUM_PRODUCT, EXIUM_PRODUCT_GROUP.
--
-- Outputs:
--   - EXIUM_RECON_DETAIL
--   - EXIUM_RECON_SUMMARY
--   - EXIUM_RAW_PARTNER_COVERAGE
--   - EXIUM_OUTCOME_DISTRIBUTION
--   - EXIUM_RECON_ACCOUNT_MONTH_SUMMARY
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

CREATE OR REPLACE TABLE EXIUM_RECON_DETAIL AS
WITH partner_name_map AS (
    SELECT
        billing_month,
        partner_name,
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS partner_name_normalized,
        sf_id
    FROM RECON_PARTNER_MAP_MONTHLY
    WHERE sf_id IS NOT NULL
      AND REGEXP_LIKE(sf_id, '^ACT-[0-9A-Z-]+$')
      AND partner_name IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY billing_month, TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(partner_name), '[^a-z0-9]+', ' '), '\\s+', ' '))
        ORDER BY partner_name
    ) = 1
),
vendor_product_map AS (
    SELECT
        NULL::VARCHAR                               AS vendor_entity,
        UPPER(TRIM(VENDOR_SKU))                    AS vendor_product_key,
        COALESCE(SKU_MATCH_KEY, VENDOR_SKU)        AS sku_match_group,
        NULL::VARCHAR                              AS exium_product_family,
        COALESCE(MAPPING_NOTES, 'RECON_SKU_MAP') AS mapping_sources
    FROM (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Exium')
    WHERE VENDOR_SKU IS NOT NULL
      AND SKU_MATCH_KEY IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5
),
cw_sku_map AS (
    SELECT
        UPPER(TRIM(CW_SKU))                        AS cw_sku_key,
        COALESCE(SKU_MATCH_KEY, VENDOR_SKU)        AS sku_match_group,
        NULL::VARCHAR                              AS exium_product_family,
        COALESCE(MAPPING_NOTES, 'RECON_SKU_MAP') AS mapping_sources
    FROM (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Exium')
    WHERE CW_SKU IS NOT NULL
      AND SKU_MATCH_KEY IS NOT NULL
    GROUP BY 1, 2, 3, 4
),
contract_group_rates AS (
    SELECT
        exium_product_family,
        currency,
        MEDIAN(contract_cost_rate) AS contract_cost_rate,
        MIN(valid_from) AS valid_from,
        MAX(valid_to) AS valid_to,
        LISTAGG(DISTINCT source_doc, ' | ') WITHIN GROUP (ORDER BY source_doc) AS source_doc
    FROM EXIUM_CONTRACT_RATES
    WHERE contract_cost_rate > 0
    GROUP BY 1, 2
),
usage_deduped AS (
    SELECT
        BILLING_MONTH,
        VENDOR_PARTNER_NAME,
        VENDOR_PRODUCT_SKU AS VENDOR_SKU_OR_PRODUCT,
        MODIFIER AS VENDOR_ENTITY,
        QUANTITY,
        NULL::FLOAT AS OVERAGE_QUANTITY,
        UNIT_PRICE,
        AMOUNT,
        CURRENCY
    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
    WHERE VENDOR = 'Exium'
      AND COALESCE(quantity, 0) <> 0
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY
            billing_month,
            vendor_partner_name,
            vendor_sku_or_product,
            quantity,
            overage_quantity,
            unit_price,
            amount,
            currency
        ORDER BY
            CASE vendor_entity WHEN 'CW' THEN 0 WHEN 'CMS' THEN 1 ELSE 2 END,
            vendor_entity
    ) = 1
),
vendor_base AS (
    SELECT
        ROW_NUMBER() OVER (
            ORDER BY
                u.billing_month,
                u.vendor_entity,
                u.vendor_partner_name,
                u.vendor_sku_or_product,
                COALESCE(u.amount, 0)
        ) AS usage_row_id,
        u.billing_month::DATE AS billing_month,
        u.vendor_entity,
        u.vendor_partner_name,
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(u.vendor_partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS vendor_partner_name_normalized,
        u.vendor_sku_or_product AS exium_product,
        u.quantity AS vendor_raw_quantity,
        u.overage_quantity AS vendor_overage_quantity,
        CASE
            WHEN COALESCE(u.unit_price, 0) > 0 AND COALESCE(u.amount, 0) > 0
                THEN ROUND(u.amount / u.unit_price)
            WHEN COALESCE(u.amount, 0) = 0 THEN 0
            ELSE COALESCE(u.overage_quantity, u.quantity, 0)
        END AS vendor_billable_quantity,
        u.unit_price,
        u.amount,
        u.currency,
        pm.sf_id,
        COALESCE(
            pm.sf_id,
            'UNMAPPED:' || TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(u.vendor_partner_name), '[^a-z0-9]+', ' '), '\\s+', ' '))
        ) AS partner_recon_key,
        CASE WHEN pm.sf_id IS NOT NULL THEN 'PARTNER_NAME' ELSE 'UNMAPPED' END AS partner_match_method,
        COALESCE(
            vpm.sku_match_group,
            'EXIUM_' ||
                CASE
                    WHEN u.vendor_sku_or_product ILIKE '%asm%'
                      OR u.vendor_sku_or_product ILIKE '%saas management%'
                      OR u.vendor_sku_or_product ILIKE '%sam%' THEN 'ASM'
                    WHEN u.vendor_sku_or_product ILIKE '%performance%'
                      OR u.vendor_sku_or_product ILIKE '%addon%'
                      OR u.vendor_sku_or_product ILIKE '%add-on%'
                      OR u.vendor_sku_or_product ILIKE '%parm%'
                      OR u.vendor_sku_or_product ILIKE '%prm-%' THEN 'PERFORMANCE'
                    ELSE 'ESSENTIALS'
                END
        ) AS sku_match_group,
        COALESCE(
            vpm.exium_product_family,
            CASE
                WHEN u.vendor_sku_or_product ILIKE '%asm%'
                  OR u.vendor_sku_or_product ILIKE '%saas management%'
                  OR u.vendor_sku_or_product ILIKE '%sam%' THEN 'ASM'
                WHEN u.vendor_sku_or_product ILIKE '%performance%'
                  OR u.vendor_sku_or_product ILIKE '%addon%'
                  OR u.vendor_sku_or_product ILIKE '%add-on%'
                  OR u.vendor_sku_or_product ILIKE '%parm%'
                  OR u.vendor_sku_or_product ILIKE '%prm-%' THEN 'PERFORMANCE'
                ELSE 'ESSENTIALS'
            END
        ) AS exium_product_family,
        COALESCE(vpm.mapping_sources, 'DYNAMIC_VENDOR_PRODUCT_FALLBACK') AS sku_mapping_sources
    FROM usage_deduped u
    LEFT JOIN partner_name_map pm
        ON pm.billing_month = u.billing_month::DATE
       AND pm.partner_name_normalized = TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(u.vendor_partner_name), '[^a-z0-9]+', ' '), '\\s+', ' '))
    LEFT JOIN vendor_product_map vpm
        ON vpm.vendor_product_key = UPPER(TRIM(u.vendor_sku_or_product))
),
vendor_agg AS (
    SELECT
        sf_id,
        partner_recon_key,
        billing_month,
        sku_match_group,
        exium_product_family,
        LISTAGG(DISTINCT vendor_partner_name, ' | ') WITHIN GROUP (ORDER BY vendor_partner_name) AS vendor_partner_name,
        LISTAGG(DISTINCT exium_product, ' | ') WITHIN GROUP (ORDER BY exium_product) AS exium_product,
        LISTAGG(DISTINCT vendor_entity, ' | ') WITHIN GROUP (ORDER BY vendor_entity) AS vendor_entities,
        LISTAGG(DISTINCT currency, ' | ') WITHIN GROUP (ORDER BY currency) AS currencies,
        SUM(vendor_raw_quantity) AS vendor_raw_quantity,
        SUM(vendor_overage_quantity) AS vendor_overage_quantity,
        SUM(vendor_billable_quantity) AS vendor_quantity,
        AVG(NULLIF(unit_price, 0)) AS vendor_unit_price,
        SUM(amount) AS vendor_amount,
        COUNT(DISTINCT usage_row_id) AS vendor_row_count,
        LISTAGG(DISTINCT partner_match_method, ' | ') WITHIN GROUP (ORDER BY partner_match_method) AS partner_match_methods,
        LISTAGG(DISTINCT sku_mapping_sources, ' | ') WITHIN GROUP (ORDER BY sku_mapping_sources) AS sku_mapping_sources,
        LISTAGG(
            DISTINCT CONCAT(exium_product, ' qty=', vendor_billable_quantity::VARCHAR, ' amount=', ROUND(amount, 2)::VARCHAR),
            ' | '
        ) WITHIN GROUP (ORDER BY CONCAT(exium_product, ' qty=', vendor_billable_quantity::VARCHAR, ' amount=', ROUND(amount, 2)::VARCHAR)) AS vendor_product_breakdown
    FROM vendor_base
    GROUP BY 1, 2, 3, 4, 5
),
vendor_cw_skus AS (
    SELECT
        v.sf_id,
        v.billing_month,
        v.sku_match_group,
        ARRAY_AGG(DISTINCT m.cw_sku) WITHIN GROUP (ORDER BY m.cw_sku) AS cw_skus
    FROM vendor_agg v
    LEFT JOIN (
        SELECT CW_SKU AS cw_sku, SKU_MATCH_KEY AS sku_match_group,
               NULL::VARCHAR AS exium_product_family, TRUE AS is_active
        FROM RECON_SKU_MAP WHERE VENDOR = 'Exium' AND CW_SKU IS NOT NULL
    ) m
        ON m.sku_match_group = v.sku_match_group
       AND m.cw_sku IS NOT NULL
       AND m.is_active = TRUE
    GROUP BY 1, 2, 3
),
exium_zuora_rows AS (
        SELECT
                z.sf_id,
                z.billing_month::DATE AS billing_month,
                COALESCE(cw.sku_match_group, UPPER(TRIM(z.product_sku))) AS sku_match_group,
                cw.exium_product_family AS exium_product_family,
                UPPER(TRIM(z.product_sku)) AS product_sku,
                z.charge_name AS charge_names,
                NULL::VARCHAR AS billing_unit_types,
                1::NUMBER AS billing_qty_multiplier,
                COALESCE(z.qty, 0) AS zuora_native_quantity,
                COALESCE(z.qty, 0) AS zuora_quantity,
                COALESCE(z.unit_price_usd, 0) AS zuora_unit_price,
                COALESCE(z.charge_amount_usd, 0) AS zuora_charge_amount,
                1::NUMBER AS billing_row_count
        FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD z
        LEFT JOIN cw_sku_map cw
            ON cw.cw_sku_key = UPPER(TRIM(z.product_sku))
        WHERE z.vendor = 'Exium'
            AND z.sf_id ILIKE 'ACT-%'
            AND COALESCE(z.qty, 0) <> 0
),
exium_marketplace_rows AS (
        SELECT
                m.sf_id,
                m.billing_month::DATE AS billing_month,
                COALESCE(cw.sku_match_group, UPPER(TRIM(m.product_sku))) AS sku_match_group,
                cw.exium_product_family AS exium_product_family,
                UPPER(TRIM(m.product_sku)) AS product_sku,
                COALESCE(m.qty, 0) AS marketplace_quantity,
                COALESCE(m.amount, 0) AS marketplace_amount,
                1::NUMBER AS marketplace_row_count,
                m.transaction_source AS marketplace_transaction_sources
        FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD m
        LEFT JOIN cw_sku_map cw
            ON cw.cw_sku_key = UPPER(TRIM(m.product_sku))
        WHERE m.vendor = 'Exium'
            AND m.sf_id ILIKE 'ACT-%'
            AND COALESCE(m.qty, 0) <> 0
),
zuora_agg AS (
    SELECT
        b.sf_id,
        b.billing_month,
        REGEXP_REPLACE(b.sku_match_group, '^EXIUM_(CMS|CW)_', 'EXIUM_') AS sku_match_group,
        ANY_VALUE(b.exium_product_family) AS exium_product_family,
        ARRAY_AGG(DISTINCT b.product_sku) WITHIN GROUP (ORDER BY b.product_sku) AS zuora_skus,
        LISTAGG(DISTINCT b.charge_names, ' | ') WITHIN GROUP (ORDER BY b.charge_names) AS zuora_charge_names,
        LISTAGG(DISTINCT b.billing_unit_types, ' | ') WITHIN GROUP (ORDER BY b.billing_unit_types) AS zuora_billing_unit_types,
        MAX(b.billing_qty_multiplier) AS max_zuora_billing_qty_multiplier,
        SUM(b.zuora_native_quantity) AS zuora_native_quantity,
        SUM(b.zuora_quantity) AS zuora_quantity,
        AVG(NULLIF(b.zuora_unit_price, 0)) AS zuora_unit_price,
        SUM(b.zuora_charge_amount) AS zuora_amount,
        SUM(b.billing_row_count) AS zuora_row_count
    FROM exium_zuora_rows b
    GROUP BY 1, 2, 3
),
marketplace_agg AS (
    SELECT
        b.sf_id,
        b.billing_month,
        REGEXP_REPLACE(b.sku_match_group, '^EXIUM_(CMS|CW)_', 'EXIUM_') AS sku_match_group,
        ANY_VALUE(b.exium_product_family) AS exium_product_family,
        ARRAY_AGG(DISTINCT b.product_sku) WITHIN GROUP (ORDER BY b.product_sku) AS marketplace_skus,
        SUM(b.marketplace_quantity) AS marketplace_quantity,
        SUM(b.marketplace_amount) AS marketplace_amount,
        SUM(b.marketplace_row_count) AS marketplace_row_count,
        LISTAGG(DISTINCT b.marketplace_transaction_sources, ' | ')
            WITHIN GROUP (ORDER BY b.marketplace_transaction_sources) AS marketplace_transaction_sources
    FROM exium_marketplace_rows b
    GROUP BY 1, 2, 3
),
same_month_any_billing AS (
    SELECT sf_id, billing_month, SUM(zuora_quantity) AS any_zuora_quantity, SUM(zuora_amount) AS any_zuora_amount, COUNT(*) AS any_zuora_row_count
    FROM zuora_agg
    GROUP BY 1, 2
),
billing_history AS (
    SELECT
        sf_id,
        billing_month,
        sku_match_group,
        zuora_quantity AS billing_quantity,
        zuora_amount AS billing_amount,
        'ZUORA' AS billing_source
    FROM zuora_agg
    UNION ALL
    SELECT
        sf_id,
        billing_month,
        sku_match_group,
        marketplace_quantity AS billing_quantity,
        marketplace_amount AS billing_amount,
        'MARKETPLACE' AS billing_source
    FROM marketplace_agg
),
nearby_billing AS (
    SELECT
        v.sf_id,
        v.billing_month,
        v.sku_match_group,
        h.billing_month AS nearby_billing_month,
        DATEDIFF(month, v.billing_month, h.billing_month) AS nearby_billing_month_offset,
        h.billing_source AS nearby_billing_source,
        h.billing_quantity AS nearby_billing_quantity,
        h.billing_amount AS nearby_billing_amount,
        ABS(h.billing_quantity - v.vendor_quantity) AS nearby_abs_qty_delta
    FROM vendor_agg v
    INNER JOIN billing_history h
        ON h.sf_id = v.sf_id
       AND h.sku_match_group = v.sku_match_group
       AND h.billing_month <> v.billing_month
       AND ABS(DATEDIFF(month, v.billing_month, h.billing_month)) <= 3
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY v.sf_id, v.billing_month, v.sku_match_group
        ORDER BY ABS(DATEDIFF(month, v.billing_month, h.billing_month)), ABS(h.billing_quantity - v.vendor_quantity)
    ) = 1
),
sf_id_to_partner AS (
    SELECT
        sf_id,
        partner_name
    FROM RECON_PARTNER_MAP
    WHERE sf_id ILIKE 'ACT-%'
      AND partner_name IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY sf_id
        ORDER BY
            IFF(UPPER(partner_name) LIKE '%-INTERNAL', 1, 0),
            IFF(UPPER(partner_name) LIKE '%-CORPORATE', 1, 0),
            LENGTH(partner_name),
            UPPER(partner_name)
    ) = 1
),
sf_account_names AS (
    SELECT
        CWS_ACCOUNT_UNIQUE_IDENTIFIER_C AS sf_id,
        NAME AS account_name
    FROM ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT
    WHERE CWS_ACCOUNT_UNIQUE_IDENTIFIER_C ILIKE 'ACT-%'
      AND IS_DELETED = FALSE
),
joined AS (
    SELECT
        COALESCE(v.billing_month, z.billing_month, m.billing_month) AS billing_month,
        COALESCE(v.sf_id, z.sf_id, m.sf_id) AS sf_id,
        COALESCE(v.sku_match_group, z.sku_match_group, m.sku_match_group) AS sku_match_group,
        COALESCE(v.exium_product_family, z.exium_product_family, m.exium_product_family) AS exium_product_family,
        COALESCE(v.vendor_partner_name, sp.partner_name, sa.account_name) AS vendor_partner_name,
        v.exium_product,
        v.vendor_entities,
        v.currencies,
        vc.cw_skus,
        z.zuora_skus,
        z.zuora_charge_names,
        z.zuora_billing_unit_types,
        z.max_zuora_billing_qty_multiplier,
        z.zuora_native_quantity,
        m.marketplace_skus,
        m.marketplace_transaction_sources,
        COALESCE(v.vendor_raw_quantity, 0) AS vendor_raw_quantity,
        COALESCE(v.vendor_overage_quantity, 0) AS vendor_overage_quantity,
        COALESCE(v.vendor_quantity, 0) AS vendor_quantity,
        v.vendor_unit_price,
        COALESCE(v.vendor_amount, 0) AS vendor_amount,
        z.zuora_quantity,
        z.zuora_unit_price,
        z.zuora_amount,
        z.zuora_row_count,
        m.marketplace_quantity,
        m.marketplace_amount,
        m.marketplace_row_count,
        CASE
            WHEN z.zuora_quantity IS NOT NULL THEN COALESCE(z.zuora_quantity, 0)
            ELSE COALESCE(m.marketplace_quantity, 0)
        END AS total_billing_quantity,
        CASE
            WHEN z.zuora_quantity IS NOT NULL THEN COALESCE(z.zuora_amount, 0)
            ELSE COALESCE(m.marketplace_amount, 0)
        END AS total_billing_amount,
        COALESCE(v.vendor_row_count, 0) AS vendor_row_count,
        v.partner_match_methods,
        v.sku_mapping_sources,
        v.vendor_product_breakdown,
        a.any_zuora_quantity,
        a.any_zuora_amount,
        a.any_zuora_row_count,
        nb.nearby_billing_month,
        nb.nearby_billing_month_offset,
        nb.nearby_billing_source,
        nb.nearby_billing_quantity,
        nb.nearby_billing_amount,
        nb.nearby_abs_qty_delta
    FROM vendor_agg v
    FULL OUTER JOIN zuora_agg z
        ON z.sf_id = v.sf_id
       AND z.billing_month = v.billing_month
       AND z.sku_match_group = v.sku_match_group
    FULL OUTER JOIN marketplace_agg m
        ON m.sf_id = COALESCE(v.sf_id, z.sf_id)
       AND m.billing_month = COALESCE(v.billing_month, z.billing_month)
       AND m.sku_match_group = COALESCE(v.sku_match_group, z.sku_match_group)
    LEFT JOIN vendor_cw_skus vc
        ON vc.sf_id = v.sf_id
       AND vc.billing_month = v.billing_month
       AND vc.sku_match_group = v.sku_match_group
    LEFT JOIN same_month_any_billing a
        ON a.sf_id = COALESCE(v.sf_id, z.sf_id, m.sf_id)
       AND a.billing_month = COALESCE(v.billing_month, z.billing_month, m.billing_month)
    LEFT JOIN nearby_billing nb
        ON nb.sf_id = v.sf_id
       AND nb.billing_month = v.billing_month
       AND nb.sku_match_group = v.sku_match_group
    LEFT JOIN sf_id_to_partner sp
        ON sp.sf_id = COALESCE(v.sf_id, z.sf_id, m.sf_id)
    LEFT JOIN sf_account_names sa
        ON sa.sf_id = COALESCE(v.sf_id, z.sf_id, m.sf_id)
),
scored AS (
    SELECT
        *,
        total_billing_amount / NULLIF(total_billing_quantity, 0) AS total_billing_unit_price,
        total_billing_quantity - vendor_quantity AS qty_delta,
        ABS(total_billing_quantity - vendor_quantity) AS abs_qty_delta,
        total_billing_amount - vendor_amount AS amount_delta,
        ABS(total_billing_amount - vendor_amount) AS abs_amount_delta,
        zuora_quantity IS NOT NULL AND marketplace_quantity IS NOT NULL AS duplicate_billing_flag,
        FALSE AS marketplace_timing_flag,
        0::NUMBER AS marketplace_timing_quantity
    FROM joined
    WHERE COALESCE(vendor_quantity, 0) <> 0
       OR COALESCE(total_billing_quantity, 0) <> 0
),
detail_pre AS (
    SELECT
        s.*,
        cr.contract_cost_rate,
        cr.source_doc AS contract_rate_source_docs,
        CASE
            WHEN s.vendor_row_count > 0 AND s.sf_id IS NULL THEN 'PARTNER_MAPPING_REQUIRED'
            WHEN s.vendor_quantity <> 0 AND s.total_billing_quantity = 0
             AND COALESCE(s.any_zuora_row_count, 0) > 0 THEN 'SKU_MISMATCH_BILLING_ON_OTHER_SKU'
            WHEN s.vendor_quantity <> 0 AND s.total_billing_quantity = 0
             AND s.nearby_billing_month IS NOT NULL
             AND s.nearby_abs_qty_delta <= GREATEST(10, ABS(s.vendor_quantity) * 0.10) THEN 'BILLING_TIMING_ADJACENT_MONTH'
            WHEN s.vendor_quantity <> 0 AND s.total_billing_quantity = 0 THEN 'NO_BILLING_NO_HISTORY'
            WHEN s.vendor_quantity = 0 AND s.total_billing_quantity <> 0 THEN 'BILLING_OVER_VENDOR'
            WHEN ABS(s.total_billing_quantity - s.vendor_quantity) <= GREATEST(3, ABS(s.vendor_quantity) * 0.03) THEN 'CLEAR'
            WHEN GREATEST(ABS(COALESCE(s.vendor_amount, 0)), ABS(COALESCE(s.total_billing_amount, 0))) <= 300 THEN 'CLEAR'
            WHEN s.total_billing_amount >= s.vendor_amount AND s.total_billing_quantity >= s.vendor_quantity THEN 'CLEAR'
            WHEN s.total_billing_quantity > s.vendor_quantity THEN 'BILLING_OVER_VENDOR'
            WHEN s.total_billing_quantity < s.vendor_quantity THEN 'VENDOR_OVER_BILLING'
            ELSE 'REVIEW_EXCEPTION'
        END AS outcome_flag
    FROM scored s
    LEFT JOIN contract_group_rates cr
        ON cr.exium_product_family = s.exium_product_family
       AND cr.currency = 'USD'
       AND s.billing_month BETWEEN cr.valid_from AND cr.valid_to
),

-- =============================================================================
-- Exium API usage (direct-from-raw architecture, 2026-08-28)
-- ---------------------------------------------------------------------------
-- Reads API_QUANTITY / AVG_API_QUANTITY directly from the raw daily-usage
-- table (same pattern as Proofpoint/SentinelOne/Acronis):
--   ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
-- Join keys (governed):
--   * partner_id (usage) = cms_id from RECON_PARTNER_MAP (keyed by sf_id).
--   * product_sku (usage) = TRT_MATCH_KEY from RECON_SKU_MAP (Exium).
--     For Exium, TRT_MATCH_KEY = CW_SKU (verified 2026-08-28: all 5 Exium
--     CW_SKUs have 100% direct 1:1 coverage in raw TRT).
-- Cycle window: day-20 snapshot (billing_month M covers on_date rows in
--   (M-1 + 20 days, M + 20 days]).
-- Grain: (sf_id, billing_month, sku_match_group).
-- =============================================================================
exium_trt_keys AS (
    SELECT DISTINCT
        UPPER(TRIM(TRT_MATCH_KEY)) AS product_sku_key,
        COALESCE(SKU_MATCH_KEY, VENDOR_SKU) AS sku_match_group
    FROM RECON_SKU_MAP
    WHERE VENDOR = 'Exium'
      AND TRT_MATCH_KEY IS NOT NULL
      AND TRIM(TRT_MATCH_KEY) <> ''
      AND COALESCE(SKU_MATCH_KEY, VENDOR_SKU) IS NOT NULL
),

exium_api_partners AS (
    SELECT DISTINCT
        d.sf_id,
        d.billing_month,
        d.sku_match_group,
        pm.cms_id
    FROM detail_pre d
        JOIN RECON_PARTNER_MAP pm
      ON pm.sf_id = d.sf_id
    WHERE d.sf_id IS NOT NULL
      AND d.sku_match_group IS NOT NULL
      AND pm.cms_id IS NOT NULL
      AND TRIM(pm.cms_id) <> ''
),

exium_api_daily AS (
    SELECT
        pa.sf_id,
        pa.billing_month,
        pa.sku_match_group,
        DATEADD('day', 20, pa.billing_month)::DATE            AS snapshot_date,
        u.on_date::DATE                                        AS on_date,
        SUM(COALESCE(u.agent_cnt, 0))                          AS day_quantity
    FROM exium_api_partners pa
    JOIN exium_trt_keys k
      ON k.sku_match_group = pa.sku_match_group
    JOIN ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
      ON u.partner_id::VARCHAR = pa.cms_id
     AND UPPER(TRIM(u.product_sku)) = k.product_sku_key
     AND u.on_date::DATE >  DATEADD('day', 20, DATEADD('month', -1, pa.billing_month))::DATE
     AND u.on_date::DATE <= DATEADD('day', 20, pa.billing_month)::DATE
    GROUP BY 1, 2, 3, 4, 5
),

exium_api_rollup AS (
    SELECT
        sf_id,
        billing_month,
        sku_match_group,
        MAX(IFF(on_date = snapshot_date, day_quantity, NULL)) AS api_quantity,
        AVG(day_quantity)                                     AS avg_api_quantity
    FROM exium_api_daily
    GROUP BY 1, 2, 3
),

detail_pre_with_api AS (
    SELECT
        d.*,
        a.api_quantity,
        a.avg_api_quantity
    FROM detail_pre d
    LEFT JOIN exium_api_rollup a
      ON a.sf_id = d.sf_id
     AND a.billing_month = d.billing_month
     AND a.sku_match_group = d.sku_match_group
)
SELECT
    billing_month,
    sf_id,
    vendor_partner_name,
    exium_product,
    exium_product_family,
    sku_match_group,
    vendor_entities,
    currencies,
    cw_skus,
    zuora_skus,
    zuora_charge_names,
    zuora_billing_unit_types,
    max_zuora_billing_qty_multiplier,
    marketplace_skus,
    marketplace_transaction_sources,
    CASE
        WHEN zuora_quantity IS NOT NULL AND marketplace_quantity IS NOT NULL THEN 'ZUORA_PRIMARY_WITH_MARKETPLACE_OVERLAP'
        WHEN zuora_quantity IS NOT NULL THEN 'ZUORA_PRIMARY'
        WHEN marketplace_quantity IS NOT NULL THEN 'MARKETPLACE_FALLBACK'
        ELSE 'NO_BILLING_SOURCE'
    END AS billing_source_mix,
    api_quantity,
    avg_api_quantity,
    vendor_raw_quantity,
    vendor_overage_quantity,
    vendor_quantity,
    vendor_unit_price,
    vendor_amount,
    zuora_native_quantity,
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
    nearby_billing_month,
    nearby_billing_month_offset,
    nearby_billing_source,
    nearby_billing_quantity,
    nearby_billing_amount,
    nearby_abs_qty_delta,
    vendor_row_count AS vendor_source_row_count,
    partner_match_methods,
    sku_mapping_sources,
    vendor_product_breakdown,
    vendor_quantity AS contract_cost_basis_quantity,
    ROUND(vendor_quantity * COALESCE(contract_cost_rate, 0), 2) AS contract_cost_basis_amount,
    contract_cost_rate,
    CASE WHEN contract_cost_rate IS NOT NULL AND total_billing_quantity <> 0
        THEN total_billing_unit_price - contract_cost_rate ELSE NULL END AS billing_vs_cost_delta_per_seat,
    CASE WHEN contract_cost_rate IS NOT NULL AND total_billing_quantity <> 0
        THEN (total_billing_unit_price - contract_cost_rate) * total_billing_quantity ELSE NULL END AS billing_vs_cost_dollar_impact,
    CASE WHEN contract_cost_rate IS NOT NULL AND contract_cost_rate <> 0 AND total_billing_quantity <> 0
        THEN (total_billing_unit_price - contract_cost_rate) / contract_cost_rate ELSE NULL END AS billing_vs_cost_pct,
    CASE
        WHEN contract_cost_rate IS NULL THEN 'NO_CONTRACT_RATE'
        WHEN total_billing_quantity = 0 THEN 'NO_BILLING_PRICE'
        WHEN total_billing_unit_price > contract_cost_rate * 1.05 THEN 'ABOVE_COST'
        WHEN total_billing_unit_price >= contract_cost_rate * 0.95 THEN 'AT_COST'
        ELSE 'BELOW_COST_DISCOUNT'
    END AS contract_price_flag,
    CASE
        WHEN contract_cost_rate IS NULL OR total_billing_quantity = 0 THEN FALSE
        WHEN total_billing_unit_price < contract_cost_rate * 0.80 THEN TRUE
        ELSE FALSE
    END AS material_below_cost_flag,
    contract_rate_source_docs,
    CURRENT_TIMESTAMP() AS recon_run_ts,
    outcome_flag,
    CASE
        WHEN outcome_flag = 'PARTNER_MAPPING_REQUIRED' THEN 'Add or correct the Exium partner mapping.'
        WHEN outcome_flag = 'DUPLICATE_BILLING' THEN 'Both Zuora and Marketplace billed this account/product/month; review duplicate billing exposure.'
        WHEN outcome_flag = 'SKU_MISMATCH_BILLING_ON_OTHER_SKU' THEN 'Vendor usage has no billing on this SKU group, but same account/month has Exium billing on another SKU group; review SKU mapping or billing setup.'
        WHEN outcome_flag = 'BILLING_TIMING_ADJACENT_MONTH' THEN 'Vendor usage has no same-month billing, but nearby-month billing on the same product group is within timing tolerance.'
        WHEN outcome_flag = 'NO_BILLING_NO_HISTORY' THEN 'Vendor usage present with no matching CW billing for this account/product group.'
        WHEN outcome_flag = 'BILLING_OVER_VENDOR' THEN 'CW billing quantity exceeds vendor billable quantity.'
        WHEN outcome_flag = 'VENDOR_OVER_BILLING' THEN 'Vendor billable quantity exceeds CW billing quantity.'
        WHEN outcome_flag = 'CLEAR' THEN 'CLEAR under Proofpoint-style quantity/amount thresholds.'
        ELSE 'Review exception.'
    END AS investigation_reason,
    CASE WHEN outcome_flag IN ('PARTNER_MAPPING_REQUIRED', 'DUPLICATE_BILLING', 'SKU_MISMATCH_BILLING_ON_OTHER_SKU', 'BILLING_TIMING_ADJACENT_MONTH', 'NO_BILLING_NO_HISTORY', 'BILLING_OVER_VENDOR', 'VENDOR_OVER_BILLING') THEN TRUE ELSE FALSE END AS billing_action_required,
    CASE WHEN contract_cost_rate IS NULL THEN NULL ELSE vendor_unit_price - contract_cost_rate END AS vendor_vs_contract_delta_per_seat,
    CASE WHEN contract_cost_rate IS NULL OR contract_cost_rate = 0 THEN NULL ELSE (vendor_unit_price - contract_cost_rate) / contract_cost_rate END AS vendor_vs_contract_pct,
    CASE
        WHEN contract_cost_rate IS NULL THEN 'NO_CONTRACT_RATE'
        WHEN vendor_unit_price IS NULL THEN 'NO_VENDOR_PRICE'
        WHEN ABS(vendor_unit_price - contract_cost_rate) <= 0.01 THEN 'EVEN'
        WHEN vendor_unit_price > contract_cost_rate THEN 'OVER_CONTRACT'
        ELSE 'UNDER_CONTRACT'
    END AS vendor_vs_contract_flag,
    CASE WHEN contract_cost_rate IS NULL OR vendor_unit_price IS NULL THEN NULL ELSE (vendor_unit_price - contract_cost_rate) * vendor_quantity END AS vendor_vs_contract_dollar_impact
FROM detail_pre_with_api;

CREATE OR REPLACE TABLE EXIUM_RECON_SUMMARY AS
SELECT
    billing_month,
    COUNT(*) AS total_rows,
    COUNT_IF(outcome_flag = 'CLEAR') AS perfect_match_rows,
    ROUND(COUNT_IF(outcome_flag = 'CLEAR') * 100.0 / NULLIF(COUNT(*), 0), 1) AS perfect_match_pct,
    SUM(abs_qty_delta) AS abs_qty_variance,
    SUM(vendor_quantity) AS total_vendor_billable_quantity,
    SUM(vendor_raw_quantity) AS total_vendor_raw_quantity,
    SUM(COALESCE(zuora_quantity, 0)) AS total_zuora_quantity,
    SUM(COALESCE(marketplace_quantity, 0)) AS total_marketplace_quantity,
    SUM(total_billing_quantity) AS total_billing_quantity,
    ROUND(SUM(vendor_amount), 2) AS total_vendor_amount,
    ROUND(SUM(total_billing_amount), 2) AS total_billing_amount,
    COUNT_IF(duplicate_billing_flag) AS duplicate_billing_rows,
    SUM(IFF(duplicate_billing_flag, COALESCE(vendor_quantity, 0), 0)) AS duplicate_billing_vendor_quantity,
    SUM(IFF(duplicate_billing_flag, COALESCE(zuora_quantity, 0), 0)) AS duplicate_billing_zuora_quantity,
    SUM(IFF(duplicate_billing_flag, COALESCE(marketplace_quantity, 0), 0)) AS duplicate_billing_marketplace_quantity,
    SUM(IFF(duplicate_billing_flag, abs_qty_delta, 0)) AS duplicate_billing_abs_qty_variance_impact,
    ROUND(SUM(IFF(duplicate_billing_flag, abs_amount_delta, 0)), 2) AS duplicate_billing_abs_amount_variance_impact,
    COUNT_IF(outcome_flag = 'PARTNER_MAPPING_REQUIRED') AS unmapped_rows,
    COUNT_IF(outcome_flag = 'NO_BILLING_NO_HISTORY') AS no_billing_rows,
    COUNT_IF(outcome_flag = 'BILLING_TIMING_ADJACENT_MONTH') AS billing_timing_rows,
    COUNT_IF(outcome_flag = 'SKU_MISMATCH_BILLING_ON_OTHER_SKU') AS sku_mismatch_rows,
    COUNT_IF(outcome_flag = 'BILLING_OVER_VENDOR') AS billing_over_rows,
    COUNT_IF(outcome_flag = 'VENDOR_OVER_BILLING') AS vendor_over_rows,
    COUNT_IF(contract_price_flag = 'ABOVE_COST') AS contract_above_cost_rows,
    COUNT_IF(contract_price_flag = 'AT_COST') AS contract_at_cost_rows,
    COUNT_IF(contract_price_flag = 'BELOW_COST_DISCOUNT') AS contract_below_cost_rows,
    COUNT_IF(material_below_cost_flag) AS contract_material_below_cost_rows,
    COUNT_IF(contract_price_flag = 'NO_CONTRACT_RATE') AS contract_no_rate_rows,
    ROUND(SUM(IFF(contract_price_flag = 'ABOVE_COST', billing_vs_cost_dollar_impact, 0)), 2) AS contract_above_cost_margin_dollars,
    ROUND(SUM(IFF(contract_price_flag = 'BELOW_COST_DISCOUNT', billing_vs_cost_dollar_impact, 0)), 2) AS contract_below_cost_loss_dollars,
    ROUND(SUM(IFF(material_below_cost_flag, billing_vs_cost_dollar_impact, 0)), 2) AS contract_material_below_cost_loss_dollars
FROM EXIUM_RECON_DETAIL
GROUP BY 1
ORDER BY 1;

CREATE OR REPLACE TABLE EXIUM_RAW_PARTNER_COVERAGE AS
SELECT
    billing_month,
    SUM(vendor_source_row_count) AS raw_rows_after_scope,
    SUM(IFF(sf_id IS NOT NULL, vendor_source_row_count, 0)) AS mapped_rows,
    ROUND(SUM(IFF(sf_id IS NOT NULL, vendor_source_row_count, 0)) / NULLIF(SUM(vendor_source_row_count), 0), 4) AS row_mapped_rate,
    SUM(vendor_quantity) AS vendor_quantity_after_scope,
    SUM(IFF(sf_id IS NOT NULL, vendor_quantity, 0)) AS mapped_vendor_quantity,
    ROUND(SUM(IFF(sf_id IS NOT NULL, vendor_quantity, 0)) / NULLIF(SUM(vendor_quantity), 0), 4) AS quantity_mapped_rate,
    ROUND(SUM(vendor_amount), 2) AS vendor_amount_after_scope,
    ROUND(SUM(IFF(sf_id IS NOT NULL, vendor_amount, 0)), 2) AS mapped_vendor_amount,
    ROUND(SUM(IFF(sf_id IS NOT NULL, vendor_amount, 0)) / NULLIF(SUM(vendor_amount), 0), 4) AS amount_mapped_rate
FROM EXIUM_RECON_DETAIL
WHERE vendor_source_row_count > 0
GROUP BY 1
ORDER BY 1;

CREATE OR REPLACE TABLE EXIUM_OUTCOME_DISTRIBUTION AS
SELECT
    billing_month,
    outcome_flag,
    COUNT(*) AS row_count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY billing_month), 1) AS row_pct,
    SUM(vendor_quantity) AS vendor_quantity,
    SUM(total_billing_quantity) AS billing_quantity,
    SUM(abs_qty_delta) AS abs_qty_delta,
    ROUND(SUM(vendor_amount), 2) AS vendor_amount,
    ROUND(SUM(total_billing_amount), 2) AS billing_amount,
    ROUND(SUM(abs_amount_delta), 2) AS abs_amount_delta
FROM EXIUM_RECON_DETAIL
GROUP BY 1, 2
ORDER BY 1, row_count DESC;

CREATE OR REPLACE TABLE EXIUM_RECON_ACCOUNT_MONTH_SUMMARY AS
WITH account_month AS (
    SELECT
        billing_month,
        sf_id,
        LISTAGG(DISTINCT vendor_partner_name, ' | ') WITHIN GROUP (ORDER BY vendor_partner_name) AS vendor_partner_name,
        LISTAGG(DISTINCT sku_match_group, ' | ') WITHIN GROUP (ORDER BY sku_match_group) AS sku_match_groups,
        LISTAGG(DISTINCT billing_source_mix, ' | ') WITHIN GROUP (ORDER BY billing_source_mix) AS billing_source_mix,
        SUM(vendor_source_row_count) AS vendor_source_row_count,
        SUM(vendor_quantity) AS vendor_quantity,
        SUM(total_billing_quantity) AS total_billing_quantity,
        SUM(vendor_amount) AS vendor_amount,
        SUM(total_billing_amount) AS total_billing_amount,
        COUNT_IF(outcome_flag = 'SKU_MISMATCH_BILLING_ON_OTHER_SKU') AS product_mix_exception_rows,
        COUNT_IF(duplicate_billing_flag) AS marketplace_overlap_rows
    FROM EXIUM_RECON_DETAIL
    GROUP BY 1, 2
),
scored AS (
    SELECT
        *,
        total_billing_quantity - vendor_quantity AS qty_delta,
        ABS(total_billing_quantity - vendor_quantity) AS abs_qty_delta,
        total_billing_amount - vendor_amount AS amount_delta,
        ABS(total_billing_amount - vendor_amount) AS abs_amount_delta,
        CASE
            WHEN vendor_source_row_count > 0 AND sf_id IS NULL THEN 'PARTNER_MAPPING_REQUIRED'
            WHEN vendor_quantity <> 0 AND total_billing_quantity = 0 THEN 'NO_BILLING_NO_HISTORY'
            WHEN vendor_quantity = 0 AND total_billing_quantity <> 0 THEN 'BILLING_OVER_VENDOR'
            WHEN ABS(total_billing_quantity - vendor_quantity) <= GREATEST(3, ABS(vendor_quantity) * 0.03) THEN 'CLEAR'
            WHEN GREATEST(ABS(COALESCE(vendor_amount, 0)), ABS(COALESCE(total_billing_amount, 0))) <= 300 THEN 'CLEAR'
            WHEN total_billing_amount >= vendor_amount AND total_billing_quantity >= vendor_quantity THEN 'CLEAR'
            WHEN total_billing_quantity > vendor_quantity THEN 'BILLING_OVER_VENDOR'
            WHEN total_billing_quantity < vendor_quantity THEN 'VENDOR_OVER_BILLING'
            ELSE 'REVIEW_EXCEPTION'
        END AS account_month_outcome_flag
    FROM account_month
)
SELECT *
FROM scored
ORDER BY billing_month, sf_id;

