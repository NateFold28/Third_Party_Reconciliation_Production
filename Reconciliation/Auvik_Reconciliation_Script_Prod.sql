-- =============================================================================
-- STEP 2: AUVIK FINAL RECONCILIATION
-- =============================================================================
-- Proofpoint-style reconciliation adapted for Auvik:
--   Vendor side: normalized AUVIK_USAGE, retaining raw QUANTITY and
--                OVERAGE_QUANTITY, while reconciling on billed quantity
--                recovered from AMOUNT / UNIT_PRICE when available.
--   Billing side: Zuora is the primary reconciliation source; Marketplace is
--                 retained as fallback when Zuora is absent and as overlap
--                 evidence when both sources exist.
--   Grain: sf_id + billing_month + Auvik product family match group.
--          CW/CMS usage files are deduped only when the source rows are exact
--          mirrors; distinct entity rows remain in scope.
--   SKU map columns use Auvik naming: AUVIK_PRODUCT, AUVIK_PRODUCT_GROUP.
--
-- Outputs:
--   - AUVIK_RECON_DETAIL
--   - AUVIK_RECON_SUMMARY
--   - AUVIK_RAW_PARTNER_COVERAGE
--   - AUVIK_OUTCOME_DISTRIBUTION
--   - AUVIK_RECON_ACCOUNT_MONTH_SUMMARY
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

CREATE OR REPLACE TABLE AUVIK_RECON_DETAIL AS
WITH partner_name_map AS (
    SELECT
        billing_month,
        partner_name,
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS partner_name_normalized,
        sf_id,
        'RECON_PARTNER_MAP' AS mapping_source
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
        REGEXP_SUBSTR(sku_match_key, 'AUVIK_(CMS|CW)_', 1, 1, 'e', 1) AS vendor_entity,
        UPPER(TRIM(vendor_product)) AS vendor_product_key,
        REGEXP_REPLACE(sku_match_key, '^AUVIK_(CMS|CW)_', 'AUVIK_') AS sku_match_group,
        REGEXP_REPLACE(sku_match_key, '^AUVIK_(CMS|CW)_', '') AS auvik_product_group,
        LISTAGG(DISTINCT mapping_notes, ' | ') WITHIN GROUP (ORDER BY mapping_notes) AS mapping_sources
    FROM (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Auvik')
    WHERE vendor_product IS NOT NULL
      AND sku_match_key IS NOT NULL
      AND REGEXP_SUBSTR(sku_match_key, 'AUVIK_(CMS|CW)_', 1, 1, 'e', 1) IS NOT NULL
    GROUP BY 1, 2, 3, 4
),
cw_sku_map AS (
    -- Preserve a de-duplicated CW SKU list only. Do not carry sku_match_group
    -- from this map because many Auvik CW SKUs map to numerous historical keys,
    -- which can fan out billing rows when joined directly.
    SELECT DISTINCT UPPER(TRIM(cw_sku)) AS cw_sku_key
    FROM (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Auvik')
    WHERE cw_sku IS NOT NULL
),
contract_group_rates AS (
    SELECT
        REGEXP_REPLACE(sku_match_key, '^AUVIK_(CMS|CW)_', '') AS auvik_product_group,
        currency,
        MEDIAN(contract_cost_rate) AS contract_cost_rate,
        MIN(valid_from) AS valid_from,
        MAX(valid_to) AS valid_to,
        LISTAGG(DISTINCT source_doc, ' | ') WITHIN GROUP (ORDER BY source_doc) AS source_doc
    FROM AUVIK_CONTRACT_RATES
    WHERE contract_cost_rate > 0
    GROUP BY 1, 2
),
usage_deduped AS (
    SELECT *
    FROM AUVIK_USAGE
    WHERE COALESCE(quantity, 0) <> 0
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY
            billing_month,
            vendor_partner_name,
            vendor_product_sku,
            quantity,
            unit_price,
            amount,
            currency
        ORDER BY
            CASE modifier WHEN 'CW' THEN 0 WHEN 'CMS' THEN 1 ELSE 2 END,
            modifier
    ) = 1
),
vendor_base AS (
    SELECT
        ROW_NUMBER() OVER (
            ORDER BY
                u.billing_month,
                u.modifier,
                u.vendor_partner_name,
                u.vendor_product_sku,
                COALESCE(u.amount, 0)
        ) AS usage_row_id,
        u.billing_month::DATE AS billing_month,
        u.modifier AS vendor_entity,
        u.vendor_partner_name,
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(u.vendor_partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS vendor_partner_name_normalized,
        u.vendor_product_sku AS auvik_product,
        u.quantity AS vendor_raw_quantity,
        NULL::NUMBER AS vendor_overage_quantity,
        CASE
            WHEN COALESCE(u.unit_price, 0) > 0 AND COALESCE(u.amount, 0) > 0
                THEN ROUND(u.amount / u.unit_price)
            WHEN COALESCE(u.amount, 0) = 0 THEN 0
            ELSE COALESCE(u.quantity, 0)
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
        REGEXP_REPLACE(COALESCE(
            vpm.sku_match_group,
            'AUVIK_' ||
                CASE
                    WHEN u.vendor_product_sku ILIKE '%asm%'
                      OR u.vendor_product_sku ILIKE '%saas management%'
                      OR u.vendor_product_sku ILIKE '%sam%' THEN 'ASM'
                    WHEN u.vendor_product_sku ILIKE '%performance%'
                      OR u.vendor_product_sku ILIKE '%addon%'
                      OR u.vendor_product_sku ILIKE '%add-on%'
                      OR u.vendor_product_sku ILIKE '%parm%'
                      OR u.vendor_product_sku ILIKE '%prm-%' THEN 'PERFORMANCE'
                    ELSE 'ESSENTIALS'
                END
        -- Strip entity prefix so vendor join key matches billing join key.
        -- zuora_agg and marketplace_agg both strip AUVIK_(CMS|CW)_ â†’ AUVIK_.
        -- Without this the FULL OUTER JOIN never matches and clear rate = 0%.
        ), '^AUVIK_(CMS|CW)_', 'AUVIK_') AS sku_match_group,
        COALESCE(
            vpm.auvik_product_group,
            CASE
                WHEN u.vendor_product_sku ILIKE '%asm%'
                  OR u.vendor_product_sku ILIKE '%saas management%'
                  OR u.vendor_product_sku ILIKE '%sam%' THEN 'ASM'
                WHEN u.vendor_product_sku ILIKE '%performance%'
                  OR u.vendor_product_sku ILIKE '%addon%'
                  OR u.vendor_product_sku ILIKE '%add-on%'
                  OR u.vendor_product_sku ILIKE '%parm%'
                  OR u.vendor_product_sku ILIKE '%prm-%' THEN 'PERFORMANCE'
                ELSE 'ESSENTIALS'
            END
        ) AS auvik_product_group,
        COALESCE(vpm.mapping_sources, 'DYNAMIC_VENDOR_PRODUCT_FALLBACK') AS sku_mapping_sources
    FROM usage_deduped u
    LEFT JOIN partner_name_map pm
        ON pm.billing_month = u.billing_month::DATE
       AND pm.partner_name_normalized = TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(u.vendor_partner_name), '[^a-z0-9]+', ' '), '\\s+', ' '))
    LEFT JOIN vendor_product_map vpm
        ON vpm.vendor_entity = u.modifier
       AND vpm.vendor_product_key = UPPER(TRIM(u.vendor_product_sku))
),
vendor_agg AS (
    SELECT
        sf_id,
        partner_recon_key,
        billing_month,
        sku_match_group,
        auvik_product_group,
        LISTAGG(DISTINCT vendor_partner_name, ' | ') WITHIN GROUP (ORDER BY vendor_partner_name) AS vendor_partner_name,
        LISTAGG(DISTINCT auvik_product, ' | ') WITHIN GROUP (ORDER BY auvik_product) AS auvik_product,
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
            DISTINCT CONCAT(auvik_product, ' qty=', vendor_billable_quantity::VARCHAR, ' amount=', ROUND(amount, 2)::VARCHAR),
            ' | '
        ) WITHIN GROUP (ORDER BY CONCAT(auvik_product, ' qty=', vendor_billable_quantity::VARCHAR, ' amount=', ROUND(amount, 2)::VARCHAR)) AS vendor_product_breakdown
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
    LEFT JOIN (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Auvik') m
        ON REGEXP_REPLACE(m.sku_match_key, '^AUVIK_(CMS|CW)_', 'AUVIK_') = v.sku_match_group
       AND m.cw_sku IS NOT NULL
    GROUP BY 1, 2, 3
),
auvik_zuora_rows AS (
        SELECT
                z.sf_id,
        z.billing_month::DATE AS billing_month,
                'AUVIK_' ||
                        CASE
                                WHEN UPPER(z.product_sku) LIKE '%ASM%'
                                    OR UPPER(z.product_sku) LIKE '%SAM%' THEN 'ASM'
                                WHEN UPPER(z.product_sku) LIKE '%PARM%'
                                    OR UPPER(z.product_sku) LIKE '%PERF%'
                                    OR UPPER(z.product_sku) LIKE '%ADDON%'
                                    OR UPPER(z.product_sku) LIKE '%ADD-ON%'
                                    OR UPPER(z.product_sku) LIKE '%AUVIKPERFORMANCADDON%' THEN 'PERFORMANCE'
                                ELSE 'ESSENTIALS'
                        END AS sku_match_group,
                CASE
                        WHEN UPPER(z.product_sku) LIKE '%ASM%'
                            OR UPPER(z.product_sku) LIKE '%SAM%' THEN 'ASM'
                        WHEN UPPER(z.product_sku) LIKE '%PARM%'
                            OR UPPER(z.product_sku) LIKE '%PERF%'
                            OR UPPER(z.product_sku) LIKE '%ADDON%'
                            OR UPPER(z.product_sku) LIKE '%ADD-ON%'
                            OR UPPER(z.product_sku) LIKE '%AUVIKPERFORMANCADDON%' THEN 'PERFORMANCE'
                        ELSE 'ESSENTIALS'
                END AS auvik_product_group,
                UPPER(TRIM(z.product_sku)) AS product_sku,
                z.charge_name AS charge_names,
                NULL::VARCHAR AS billing_unit_types,
                CASE
                        WHEN COALESCE(z.qty, 0) = 1
                         AND UPPER(COALESCE(z.charge_name, '')) LIKE '%PACKAGE MSP%'
                         AND REGEXP_SUBSTR(UPPER(TRIM(COALESCE(z.product_sku, ''))), 'M([[:digit:]]{2,5})', 1, 1, 'e', 1) IS NOT NULL
                            THEN TO_NUMBER(REGEXP_SUBSTR(UPPER(TRIM(z.product_sku)), 'M([[:digit:]]{2,5})', 1, 1, 'e', 1))
                        WHEN COALESCE(z.qty, 0) = 1
                         AND REGEXP_SUBSTR(UPPER(TRIM(COALESCE(z.product_sku, ''))), 'AVNMM([[:digit:]]{2,5})', 1, 1, 'e', 1) IS NOT NULL
                            THEN TO_NUMBER(REGEXP_SUBSTR(UPPER(TRIM(z.product_sku)), 'AVNMM([[:digit:]]{2,5})', 1, 1, 'e', 1))
                        ELSE 1::NUMBER
                END AS billing_qty_multiplier,
                COALESCE(z.qty, 0) AS zuora_native_quantity,
                COALESCE(z.qty, 0) *
                CASE
                        WHEN COALESCE(z.qty, 0) = 1
                         AND UPPER(COALESCE(z.charge_name, '')) LIKE '%PACKAGE MSP%'
                         AND REGEXP_SUBSTR(UPPER(TRIM(COALESCE(z.product_sku, ''))), 'M([[:digit:]]{2,5})', 1, 1, 'e', 1) IS NOT NULL
                            THEN TO_NUMBER(REGEXP_SUBSTR(UPPER(TRIM(z.product_sku)), 'M([[:digit:]]{2,5})', 1, 1, 'e', 1))
                        WHEN COALESCE(z.qty, 0) = 1
                         AND REGEXP_SUBSTR(UPPER(TRIM(COALESCE(z.product_sku, ''))), 'AVNMM([[:digit:]]{2,5})', 1, 1, 'e', 1) IS NOT NULL
                            THEN TO_NUMBER(REGEXP_SUBSTR(UPPER(TRIM(z.product_sku)), 'AVNMM([[:digit:]]{2,5})', 1, 1, 'e', 1))
                        ELSE 1::NUMBER
                END AS zuora_quantity,
                COALESCE(z.unit_price_usd, 0) AS zuora_unit_price,
                COALESCE(z.charge_amount_usd, 0) AS zuora_charge_amount,
                1::NUMBER AS billing_row_count
        FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD z
        LEFT JOIN cw_sku_map cw
            ON cw.cw_sku_key = UPPER(TRIM(z.product_sku))
        WHERE z.vendor = 'Auvik'
            AND z.sf_id ILIKE 'ACT-%'
            AND COALESCE(z.qty, 0) <> 0
),
auvik_marketplace_rows AS (
        SELECT
                m.sf_id,
                m.billing_month::DATE AS billing_month,
                'AUVIK_' ||
                        CASE
                                WHEN UPPER(m.product_sku) LIKE '%ASM%'
                                    OR UPPER(m.product_sku) LIKE '%SAM%' THEN 'ASM'
                                WHEN UPPER(m.product_sku) LIKE '%PARM%'
                                    OR UPPER(m.product_sku) LIKE '%PERF%'
                                    OR UPPER(m.product_sku) LIKE '%ADDON%'
                                    OR UPPER(m.product_sku) LIKE '%ADD-ON%'
                                    OR UPPER(m.product_sku) LIKE '%AUVIKPERFORMANCADDON%' THEN 'PERFORMANCE'
                                ELSE 'ESSENTIALS'
                        END AS sku_match_group,
                CASE
                        WHEN UPPER(m.product_sku) LIKE '%ASM%'
                            OR UPPER(m.product_sku) LIKE '%SAM%' THEN 'ASM'
                        WHEN UPPER(m.product_sku) LIKE '%PARM%'
                            OR UPPER(m.product_sku) LIKE '%PERF%'
                            OR UPPER(m.product_sku) LIKE '%ADDON%'
                            OR UPPER(m.product_sku) LIKE '%ADD-ON%'
                            OR UPPER(m.product_sku) LIKE '%AUVIKPERFORMANCADDON%' THEN 'PERFORMANCE'
                        ELSE 'ESSENTIALS'
                END AS auvik_product_group,
                UPPER(TRIM(m.product_sku)) AS product_sku,
                COALESCE(m.qty, 0) AS marketplace_quantity,
                COALESCE(m.amount, 0) AS marketplace_amount,
                1::NUMBER AS marketplace_row_count,
                m.transaction_source AS marketplace_transaction_sources
        FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD m
        INNER JOIN cw_sku_map cw
            ON cw.cw_sku_key = UPPER(TRIM(m.product_sku))
        WHERE m.vendor = 'Auvik'
            AND m.sf_id ILIKE 'ACT-%'
            AND COALESCE(m.qty, 0) <> 0
),
zuora_agg AS (
    SELECT
        b.sf_id,
        b.billing_month,
        REGEXP_REPLACE(b.sku_match_group, '^AUVIK_(CMS|CW)_', 'AUVIK_') AS sku_match_group,
        ANY_VALUE(b.auvik_product_group) AS auvik_product_group,
        ARRAY_AGG(DISTINCT b.product_sku) WITHIN GROUP (ORDER BY b.product_sku) AS zuora_skus,
        LISTAGG(DISTINCT b.charge_names, ' | ') WITHIN GROUP (ORDER BY b.charge_names) AS zuora_charge_names,
        LISTAGG(DISTINCT b.billing_unit_types, ' | ') WITHIN GROUP (ORDER BY b.billing_unit_types) AS zuora_billing_unit_types,
        MAX(b.billing_qty_multiplier) AS max_zuora_billing_qty_multiplier,
        SUM(b.zuora_native_quantity) AS zuora_native_quantity,
        SUM(b.zuora_quantity) AS zuora_quantity,
        AVG(NULLIF(b.zuora_unit_price, 0)) AS zuora_unit_price,
        SUM(b.zuora_charge_amount) AS zuora_amount,
        SUM(b.billing_row_count) AS zuora_row_count
    FROM auvik_zuora_rows b
    GROUP BY 1, 2, 3
),
marketplace_agg AS (
    SELECT
        b.sf_id,
        b.billing_month,
        REGEXP_REPLACE(b.sku_match_group, '^AUVIK_(CMS|CW)_', 'AUVIK_') AS sku_match_group,
        ANY_VALUE(b.auvik_product_group) AS auvik_product_group,
        ARRAY_AGG(DISTINCT b.product_sku) WITHIN GROUP (ORDER BY b.product_sku) AS marketplace_skus,
        SUM(b.marketplace_quantity) AS marketplace_quantity,
        SUM(b.marketplace_amount) AS marketplace_amount,
        SUM(b.marketplace_row_count) AS marketplace_row_count,
        LISTAGG(DISTINCT b.marketplace_transaction_sources, ' | ')
            WITHIN GROUP (ORDER BY b.marketplace_transaction_sources) AS marketplace_transaction_sources
    FROM auvik_marketplace_rows b
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
        COALESCE(v.auvik_product_group, z.auvik_product_group, m.auvik_product_group) AS auvik_product_group,
        COALESCE(v.vendor_partner_name, sp.partner_name, sa.account_name) AS vendor_partner_name,
        v.auvik_product,
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
        END AS base_outcome_flag
    FROM scored s
    LEFT JOIN contract_group_rates cr
        ON cr.auvik_product_group = s.auvik_product_group
       AND cr.currency = 'USD'
       AND s.billing_month BETWEEN cr.valid_from AND cr.valid_to
)
SELECT
    billing_month,
    sf_id,
    vendor_partner_name,
    auvik_product,
    auvik_product_group,
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
    base_outcome_flag,
    CASE
        WHEN base_outcome_flag = 'CLEAR' THEN 'LOW'
        WHEN abs_qty_delta <= GREATEST(10, ABS(vendor_quantity) * 0.05) THEN 'LOW'
        WHEN abs_qty_delta <= GREATEST(25, ABS(vendor_quantity) * 0.10) THEN 'MEDIUM'
        ELSE 'HIGH'
    END AS materiality_band,
    CASE
        WHEN billing_source_mix = 'ZUORA_PRIMARY_WITH_MARKETPLACE_OVERLAP' THEN 'MARKETPLACE_OVERLAP'
        WHEN billing_source_mix = 'MARKETPLACE_FALLBACK' THEN 'MARKETPLACE_PRIMARY'
        WHEN billing_source_mix = 'NO_BILLING_SOURCE' THEN 'NO_BILLING_SOURCE'
        ELSE 'NON_MARKETPLACE'
    END AS marketplace_classification,
    CASE
        WHEN COALESCE(vendor_overage_quantity, 0) > 0
         AND COALESCE(vendor_overage_quantity, 0) >= GREATEST(10, ABS(vendor_raw_quantity) * 0.20) THEN 'OVERAGE_HEAVY'
        WHEN COALESCE(vendor_overage_quantity, 0) > 0 THEN 'OVERAGE_PRESENT'
        ELSE 'NO_OVERAGE'
    END AS overage_classification,
    CONCAT(
        base_outcome_flag,
        '|MAT_',
        CASE
            WHEN base_outcome_flag = 'CLEAR' THEN 'LOW'
            WHEN abs_qty_delta <= GREATEST(10, ABS(vendor_quantity) * 0.05) THEN 'LOW'
            WHEN abs_qty_delta <= GREATEST(25, ABS(vendor_quantity) * 0.10) THEN 'MEDIUM'
            ELSE 'HIGH'
        END,
        '|SRC_',
        CASE
            WHEN billing_source_mix = 'ZUORA_PRIMARY_WITH_MARKETPLACE_OVERLAP' THEN 'MARKETPLACE_OVERLAP'
            WHEN billing_source_mix = 'MARKETPLACE_FALLBACK' THEN 'MARKETPLACE_PRIMARY'
            WHEN billing_source_mix = 'NO_BILLING_SOURCE' THEN 'NO_BILLING_SOURCE'
            ELSE 'NON_MARKETPLACE'
        END,
        '|OVR_',
        CASE
            WHEN COALESCE(vendor_overage_quantity, 0) > 0
             AND COALESCE(vendor_overage_quantity, 0) >= GREATEST(10, ABS(vendor_raw_quantity) * 0.20) THEN 'OVERAGE_HEAVY'
            WHEN COALESCE(vendor_overage_quantity, 0) > 0 THEN 'OVERAGE_PRESENT'
            ELSE 'NO_OVERAGE'
        END
    ) AS outcome_flag,
    CASE
        WHEN base_outcome_flag = 'PARTNER_MAPPING_REQUIRED' THEN 'Add or correct the Auvik partner mapping.'
        WHEN base_outcome_flag = 'DUPLICATE_BILLING' THEN 'Both Zuora and Marketplace billed this account/product/month; review duplicate billing exposure.'
        WHEN base_outcome_flag = 'SKU_MISMATCH_BILLING_ON_OTHER_SKU' THEN 'Vendor usage has no billing on this SKU group, but same account/month has Auvik billing on another SKU group; review SKU mapping or billing setup.'
        WHEN base_outcome_flag = 'BILLING_TIMING_ADJACENT_MONTH' THEN 'Vendor usage has no same-month billing, but nearby-month billing on the same product group is within timing tolerance.'
        WHEN base_outcome_flag = 'NO_BILLING_NO_HISTORY' THEN 'Vendor usage present with no matching CW billing for this account/product group.'
        WHEN base_outcome_flag = 'BILLING_OVER_VENDOR' THEN 'CW billing quantity exceeds vendor billable quantity.'
        WHEN base_outcome_flag = 'VENDOR_OVER_BILLING' THEN 'Vendor billable quantity exceeds CW billing quantity.'
        WHEN base_outcome_flag = 'CLEAR' THEN 'CLEAR under Proofpoint-style quantity/amount thresholds.'
        ELSE 'Review exception.'
    END AS investigation_reason,
    CASE WHEN base_outcome_flag IN ('PARTNER_MAPPING_REQUIRED', 'DUPLICATE_BILLING', 'SKU_MISMATCH_BILLING_ON_OTHER_SKU', 'BILLING_TIMING_ADJACENT_MONTH', 'NO_BILLING_NO_HISTORY', 'BILLING_OVER_VENDOR', 'VENDOR_OVER_BILLING') THEN TRUE ELSE FALSE END AS billing_action_required,
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
FROM detail_pre;

CREATE OR REPLACE TABLE AUVIK_RECON_SUMMARY AS
SELECT
    billing_month,
    COUNT(*) AS total_rows,
    COUNT_IF(base_outcome_flag = 'CLEAR') AS perfect_match_rows,
    ROUND(COUNT_IF(base_outcome_flag = 'CLEAR') * 100.0 / NULLIF(COUNT(*), 0), 1) AS perfect_match_pct,
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
    COUNT_IF(base_outcome_flag = 'PARTNER_MAPPING_REQUIRED') AS unmapped_rows,
    COUNT_IF(base_outcome_flag = 'NO_BILLING_NO_HISTORY') AS no_billing_rows,
    COUNT_IF(base_outcome_flag = 'BILLING_TIMING_ADJACENT_MONTH') AS billing_timing_rows,
    COUNT_IF(base_outcome_flag = 'SKU_MISMATCH_BILLING_ON_OTHER_SKU') AS sku_mismatch_rows,
    COUNT_IF(base_outcome_flag = 'BILLING_OVER_VENDOR') AS billing_over_rows,
    COUNT_IF(base_outcome_flag = 'VENDOR_OVER_BILLING') AS vendor_over_rows,
    COUNT_IF(contract_price_flag = 'ABOVE_COST') AS contract_above_cost_rows,
    COUNT_IF(contract_price_flag = 'AT_COST') AS contract_at_cost_rows,
    COUNT_IF(contract_price_flag = 'BELOW_COST_DISCOUNT') AS contract_below_cost_rows,
    COUNT_IF(material_below_cost_flag) AS contract_material_below_cost_rows,
    COUNT_IF(contract_price_flag = 'NO_CONTRACT_RATE') AS contract_no_rate_rows,
    ROUND(SUM(IFF(contract_price_flag = 'ABOVE_COST', billing_vs_cost_dollar_impact, 0)), 2) AS contract_above_cost_margin_dollars,
    ROUND(SUM(IFF(contract_price_flag = 'BELOW_COST_DISCOUNT', billing_vs_cost_dollar_impact, 0)), 2) AS contract_below_cost_loss_dollars,
    ROUND(SUM(IFF(material_below_cost_flag, billing_vs_cost_dollar_impact, 0)), 2) AS contract_material_below_cost_loss_dollars
FROM AUVIK_RECON_DETAIL
GROUP BY 1
ORDER BY 1;

CREATE OR REPLACE TABLE AUVIK_RAW_PARTNER_COVERAGE AS
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
FROM AUVIK_RECON_DETAIL
WHERE vendor_source_row_count > 0
GROUP BY 1
ORDER BY 1;

CREATE OR REPLACE TABLE AUVIK_OUTCOME_DISTRIBUTION AS
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
FROM AUVIK_RECON_DETAIL
GROUP BY 1, 2
ORDER BY 1, row_count DESC;

CREATE OR REPLACE TABLE AUVIK_RECON_ACCOUNT_MONTH_SUMMARY AS
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
    FROM AUVIK_RECON_DETAIL
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

