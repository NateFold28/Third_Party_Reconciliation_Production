 KeepIT final reconciliation.
-- Purpose: create canonical detail and summary outputs for app/reporting use.

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

CREATE OR REPLACE TABLE KEEPIT_VENDOR_USAGE_MASTER AS
WITH vendor_sku_map AS (
    SELECT
        vendor_product,
        ANY_VALUE(sku_match_key) AS sku_match_group,
        NULL::VARCHAR AS product_family,
        'THIRD_PARTY_RECON_SKU_MAP_PROD' AS sku_mapping_source,
        'OK' AS sku_review_flag
    FROM (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'KeepIT')
    WHERE vendor = 'KeepIT'
    GROUP BY 1
),
-- 2026-08-23 fix: KEEPIT_PARTNER_CMS_CROSSWALK_V5 has ~23 rows per
-- vendor_partner_name on average (keyed by vendor_partner_guid + cms_id).
-- Joining KEEPIT_USAGE to that raw table by name alone fanned out every
-- usage row by ~23x, inflating VENDOR_AMOUNT from ~$7M to ~$182M.
-- Dedupe to one row per name, choosing the highest-evidence mapping so
-- SF_ID and CMS_ID are deterministic.
partner_bridge AS (
    SELECT
        vendor_partner_guid,
        vendor_partner_name,
        cms_id,
        sf_id,
        sf_account_name,
        review_flag AS partner_review_flag,
        mapping_source AS partner_mapping_source
    FROM KEEPIT_PARTNER_CMS_CROSSWALK_V5
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY UPPER(TRIM(vendor_partner_name))
        ORDER BY COALESCE(evidence_row_count, 0) DESC,
                 COALESCE(account_match_count, 0) DESC,
                 sf_id NULLS LAST
    ) = 1
)
SELECT
    u.*,
    u.VENDOR_PRODUCT_SKU AS VENDOR_SKU_OR_PRODUCT,
    CASE WHEN UPPER(COALESCE(u.MODIFIER, '')) = 'TAKEOUT' THEN 'TAKEOUT' ELSE 'MAIN' END AS SOURCE_FAMILY,
    u.AMOUNT AS RECON_AMOUNT,
    NULL::VARCHAR AS SOURCE_FILE,
    NULL::VARCHAR AS VENDOR_SUBSCRIPTION_GUID,
    NULL::VARCHAR AS VENDOR_PARTNER_GUID,
    NULL::VARCHAR AS CMS_ID,
    COALESCE(vsm.sku_match_group, u.VENDOR_PRODUCT_SKU) AS SKU_MATCH_GROUP,
    vsm.product_family,
    COALESCE(vsm.sku_mapping_source, 'KEEPIT_USAGE_VENDOR_SKU_UNMAPPED') AS SKU_MAPPING_SOURCE,
    COALESCE(vsm.sku_review_flag, 'REVIEW_REQUIRED') AS SKU_REVIEW_FLAG,
    pb.cms_id AS RESOLVED_CMS_ID,
    pb.sf_id,
    pb.sf_account_name,
    pb.partner_review_flag,
    pb.partner_mapping_source
FROM KEEPIT_USAGE u
LEFT JOIN vendor_sku_map vsm
    ON UPPER(TRIM(vsm.vendor_product)) = UPPER(TRIM(u.VENDOR_PRODUCT_SKU))
LEFT JOIN partner_bridge pb
    ON UPPER(TRIM(pb.vendor_partner_name)) = UPPER(TRIM(u.VENDOR_PARTNER_NAME));

CREATE OR REPLACE TABLE KEEPIT_RECON_DETAIL AS
WITH family_presence AS (
    SELECT
        BILLING_MONTH::DATE AS billing_month,
        COUNT_IF(SOURCE_FAMILY = 'PROMO') AS promo_row_count
    FROM KEEPIT_VENDOR_USAGE_MASTER
    GROUP BY 1
),
vendor_base AS (
    SELECT
        m.*,
        CASE
            WHEN m.SOURCE_FAMILY = 'TAKEOUT' AND COALESCE(fp.promo_row_count, 0) = 0 THEN 'PROMO'
            ELSE m.SOURCE_FAMILY
        END AS RECON_SOURCE_FAMILY
    FROM KEEPIT_VENDOR_USAGE_MASTER m
    LEFT JOIN family_presence fp
        ON fp.billing_month = m.BILLING_MONTH::DATE
),
vendor_agg AS (
    SELECT
        sf_id,
        RESOLVED_CMS_ID AS cms_id,
        BILLING_MONTH::DATE AS billing_month,
        RECON_SOURCE_FAMILY AS source_family,
        SKU_MATCH_GROUP AS sku_match_group,
        ARRAY_AGG(DISTINCT RESOLVED_CMS_ID) WITHIN GROUP (ORDER BY RESOLVED_CMS_ID) AS cms_ids,
        LISTAGG(DISTINCT VENDOR_PARTNER_NAME, ' | ') WITHIN GROUP (ORDER BY VENDOR_PARTNER_NAME) AS vendor_partner_name,
        ARRAY_AGG(DISTINCT SOURCE_FILE) WITHIN GROUP (ORDER BY SOURCE_FILE) AS vendor_source_files,
        ARRAY_AGG(DISTINCT SOURCE_FAMILY) WITHIN GROUP (ORDER BY SOURCE_FAMILY) AS vendor_source_families,
        COUNT(*) AS vendor_source_row_count,
        COUNT(DISTINCT VENDOR_PARTNER_GUID) AS vendor_partner_guid_count,
        COUNT_IF(sf_id IS NULL) AS vendor_unmapped_partner_rows,
        SUM(COALESCE(QUANTITY, 0)) AS vendor_quantity,
        SUM(COALESCE(RECON_AMOUNT, AMOUNT, 0)) AS vendor_amount
    FROM vendor_base
    WHERE SKU_MATCH_GROUP IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5
),
vendor_weights AS (
    SELECT
        sf_id,
        billing_month,
        source_family,
        sku_match_group,
        SUM(COALESCE(vendor_quantity, 0)) AS vendor_group_quantity,
        COUNT(*) AS vendor_row_count
    FROM vendor_agg
    GROUP BY 1, 2, 3, 4
),
joined_vendor AS (
    SELECT
        v.sf_id,
        v.cms_id,
        v.billing_month,
        v.source_family,
        v.sku_match_group,
        v.cms_ids,
        v.vendor_partner_name,
        v.vendor_source_files,
        v.vendor_source_families,
        sm.cw_skus,
        z.zuora_skus,
        z.zuora_charge_names,
        c.carr_skus,
        CASE
            WHEN z.sf_id IS NOT NULL AND c.sf_id IS NOT NULL THEN 'ZUORA_WITH_CARR_SECONDARY_CHECK'
            WHEN z.sf_id IS NOT NULL THEN 'ZUORA_ONLY'
            WHEN c.sf_id IS NOT NULL THEN 'CARR_SECONDARY_CHECK_ONLY'
            ELSE 'NO_BILLING_SOURCE'
        END AS billing_source_mix,
        v.vendor_quantity,
        v.vendor_amount,
        z.zuora_quantity,
        z.zuora_unit_price,
        z.zuora_amount,
        z.zuora_row_count,
        z.zuora_review_row_count,
        c.carr_quantity,
        c.carr_amount,
        c.carr_row_count,
        NULL::NUMBER AS support_quantity,
        NULL::NUMBER AS support_row_count,
        CASE
            WHEN COALESCE(w.vendor_group_quantity, 0) > 0 THEN COALESCE(z.zuora_quantity, 0) * COALESCE(v.vendor_quantity, 0) / NULLIF(w.vendor_group_quantity, 0)
            ELSE 0
        END AS total_billing_quantity,
        CASE
            WHEN COALESCE(w.vendor_group_quantity, 0) > 0 THEN COALESCE(z.zuora_amount, 0) * COALESCE(v.vendor_quantity, 0) / NULLIF(w.vendor_group_quantity, 0)
            ELSE 0
        END AS total_billing_amount,
        v.vendor_source_row_count,
        v.vendor_partner_guid_count,
        v.vendor_unmapped_partner_rows
    FROM vendor_agg v
    LEFT JOIN vendor_weights w
        ON w.sf_id = v.sf_id
       AND w.billing_month = v.billing_month
       AND w.source_family = v.source_family
       AND w.sku_match_group = v.sku_match_group
    LEFT JOIN KEEPIT_ZUORA_RESOLVED z
        ON z.sf_id = v.sf_id
       AND z.billing_month = v.billing_month
       AND z.source_family = v.source_family
       AND z.sku_match_group = v.sku_match_group
    LEFT JOIN KEEPIT_CARR_RESOLVED c
        ON c.sf_id = v.sf_id
       AND c.billing_month = v.billing_month
       AND c.source_family = v.source_family
       AND c.sku_match_group = v.sku_match_group
    LEFT JOIN (
        SELECT
            sku_match_key AS sku_match_group,
            ARRAY_AGG(DISTINCT cw_sku) WITHIN GROUP (ORDER BY cw_sku) AS cw_skus
        FROM (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'KeepIT')
        WHERE vendor = 'KeepIT'
          AND cw_sku IS NOT NULL
        GROUP BY 1
    ) sm
        ON sm.sku_match_group = v.sku_match_group
),
zuora_only AS (
    SELECT
        z.sf_id,
        NULL::VARCHAR AS cms_id,
        z.billing_month,
        z.source_family,
        z.sku_match_group,
        ARRAY_CONSTRUCT() AS cms_ids,
        NULL::VARCHAR AS vendor_partner_name,
        NULL AS vendor_source_files,
        NULL AS vendor_source_families,
        sm.cw_skus,
        z.zuora_skus,
        z.zuora_charge_names,
        c.carr_skus,
        CASE
            WHEN c.sf_id IS NOT NULL THEN 'ZUORA_WITH_CARR_SECONDARY_CHECK'
            ELSE 'ZUORA_ONLY'
        END AS billing_source_mix,
        0::NUMBER AS vendor_quantity,
        0::NUMBER AS vendor_amount,
        z.zuora_quantity,
        z.zuora_unit_price,
        z.zuora_amount,
        z.zuora_row_count,
        z.zuora_review_row_count,
        c.carr_quantity,
        c.carr_amount,
        c.carr_row_count,
        NULL::NUMBER AS support_quantity,
        NULL::NUMBER AS support_row_count,
        COALESCE(z.zuora_quantity, 0) AS total_billing_quantity,
        COALESCE(z.zuora_amount, 0) AS total_billing_amount,
        0::NUMBER AS vendor_source_row_count,
        0::NUMBER AS vendor_partner_guid_count,
        0::NUMBER AS vendor_unmapped_partner_rows
    FROM KEEPIT_ZUORA_RESOLVED z
    LEFT JOIN KEEPIT_CARR_RESOLVED c
        ON c.sf_id = z.sf_id
       AND c.billing_month = z.billing_month
       AND c.source_family = z.source_family
       AND c.sku_match_group = z.sku_match_group
    LEFT JOIN vendor_weights w
        ON w.sf_id = z.sf_id
       AND w.billing_month = z.billing_month
       AND w.source_family = z.source_family
       AND w.sku_match_group = z.sku_match_group
    LEFT JOIN (
        SELECT
            sku_match_key AS sku_match_group,
            ARRAY_AGG(DISTINCT cw_sku) WITHIN GROUP (ORDER BY cw_sku) AS cw_skus
        FROM (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'KeepIT')
        WHERE vendor = 'KeepIT'
          AND cw_sku IS NOT NULL
        GROUP BY 1
    ) sm
        ON sm.sku_match_group = z.sku_match_group
    WHERE w.sf_id IS NULL
),
carr_secondary_only AS (
    SELECT
        c.sf_id,
        NULL::VARCHAR AS cms_id,
        c.billing_month,
        c.source_family,
        c.sku_match_group,
        ARRAY_CONSTRUCT() AS cms_ids,
        NULL::VARCHAR AS vendor_partner_name,
        NULL AS vendor_source_files,
        NULL AS vendor_source_families,
        sm.cw_skus,
        NULL AS zuora_skus,
        NULL AS zuora_charge_names,
        c.carr_skus,
        'CARR_SECONDARY_CHECK_ONLY' AS billing_source_mix,
        0::NUMBER AS vendor_quantity,
        0::NUMBER AS vendor_amount,
        NULL::NUMBER AS zuora_quantity,
        NULL::NUMBER AS zuora_unit_price,
        NULL::NUMBER AS zuora_amount,
        NULL::NUMBER AS zuora_row_count,
        NULL::NUMBER AS zuora_review_row_count,
        c.carr_quantity,
        c.carr_amount,
        c.carr_row_count,
        NULL::NUMBER AS support_quantity,
        NULL::NUMBER AS support_row_count,
        0::NUMBER AS total_billing_quantity,
        0::NUMBER AS total_billing_amount,
        0::NUMBER AS vendor_source_row_count,
        0::NUMBER AS vendor_partner_guid_count,
        0::NUMBER AS vendor_unmapped_partner_rows
    FROM KEEPIT_CARR_RESOLVED c
    LEFT JOIN KEEPIT_ZUORA_RESOLVED z
        ON z.sf_id = c.sf_id
       AND z.billing_month = c.billing_month
       AND z.source_family = c.source_family
       AND z.sku_match_group = c.sku_match_group
    LEFT JOIN vendor_weights w
        ON w.sf_id = c.sf_id
       AND w.billing_month = c.billing_month
       AND w.source_family = c.source_family
       AND w.sku_match_group = c.sku_match_group
    LEFT JOIN (
        SELECT
            sku_match_key AS sku_match_group,
            ARRAY_AGG(DISTINCT cw_sku) WITHIN GROUP (ORDER BY cw_sku) AS cw_skus
        FROM (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'KeepIT')
        WHERE vendor = 'KeepIT'
          AND cw_sku IS NOT NULL
        GROUP BY 1
    ) sm
        ON sm.sku_match_group = c.sku_match_group
    WHERE z.sf_id IS NULL
      AND w.sf_id IS NULL
),
-- Reverse lookup: sf_id -> partner_name for billing-only rows
sf_id_to_partner AS (
    SELECT vendor_partner_guid, sf_id, vendor_partner_name
    FROM KEEPIT_PARTNER_CMS_CROSSWALK_V5
    WHERE sf_id IS NOT NULL AND sf_id ILIKE 'ACT-%'
    QUALIFY ROW_NUMBER() OVER (PARTITION BY sf_id ORDER BY evidence_row_count DESC) = 1
),
sf_account_names AS (
    SELECT CWS_ACCOUNT_UNIQUE_IDENTIFIER_C AS sf_id, NAME AS account_name
    FROM ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT
    WHERE CWS_ACCOUNT_UNIQUE_IDENTIFIER_C ILIKE 'ACT-%' AND IS_DELETED = FALSE
),

joined AS (
    SELECT j.*, COALESCE(j.vendor_partner_name, pn.vendor_partner_name, sfn.account_name) AS resolved_partner_name
    FROM (
        SELECT * FROM joined_vendor
        UNION ALL
        SELECT * FROM zuora_only
        UNION ALL
        SELECT * FROM carr_secondary_only
    ) j
    LEFT JOIN sf_id_to_partner pn ON pn.sf_id = j.sf_id
    LEFT JOIN sf_account_names sfn ON sfn.sf_id = j.sf_id
),
scored AS (
    SELECT
        *,
        total_billing_amount / NULLIF(total_billing_quantity, 0) AS total_billing_unit_price,
        vendor_amount / NULLIF(vendor_quantity, 0) AS vendor_unit_price,
        total_billing_quantity - vendor_quantity AS qty_delta,
        ABS(total_billing_quantity - vendor_quantity) AS abs_qty_delta,
        total_billing_amount - vendor_amount AS amount_delta,
        ABS(total_billing_amount - vendor_amount) AS abs_amount_delta,
        (zuora_quantity IS NOT NULL AND carr_quantity IS NOT NULL
         AND ABS(COALESCE(zuora_quantity, 0) - COALESCE(carr_quantity, 0)) > GREATEST(3, COALESCE(zuora_quantity, 0) * 0.05)) AS duplicate_billing_flag,
        CASE
            -- 0. Takeout file with no direct billing lane
            WHEN source_family = 'TAKEOUT' AND total_billing_quantity = 0 THEN 'TAKEOUT_SUPPORT_FILE_NO_DIRECT_BILLING'

            -- 1. Structural preconditions
            WHEN vendor_quantity > 0 AND sf_id IS NULL THEN 'PARTNER_MAPPING_REQUIRED'
            WHEN vendor_quantity = 0 AND total_billing_quantity = 0 AND COALESCE(carr_quantity, 0) > 0 THEN 'CARR_SECONDARY_CHECK_ONLY'

            -- 2. CW-only SKUs (no vendor counterpart) - non-penalizing
            WHEN sku_match_group ILIKE 'KEEPIT_CW_ONLY_%' AND vendor_quantity = 0 THEN 'CW_ONLY_ADDON_NO_VENDOR'

            -- 3. Zero-dollar included tracking rows - non-penalizing
            WHEN vendor_quantity = 0 AND total_billing_quantity > 0 AND COALESCE(total_billing_amount, 0) = 0 THEN 'CW_INCLUDED_ZERO_DOLLAR'
            WHEN vendor_quantity > 0 AND total_billing_quantity > 0 AND COALESCE(total_billing_amount, 0) = 0
                 AND total_billing_quantity < vendor_quantity THEN 'CW_INCLUDED_ZERO_DOLLAR'

            -- 4. One-sided rows with material exposure
            WHEN vendor_quantity > 0 AND total_billing_quantity = 0 THEN 'STRUCTURAL_VENDOR_ONLY'
            WHEN vendor_quantity = 0 AND total_billing_quantity > 0 THEN 'STRUCTURAL_BILLING_ONLY'

            -- 5. Two-sided CLEAR (within tolerance)
            WHEN ABS(total_billing_quantity - vendor_quantity) <= GREATEST(3, vendor_quantity * 0.05) THEN 'CLEAR'

            -- 6. Dollar noise gate
            WHEN GREATEST(COALESCE(vendor_amount, 0), COALESCE(total_billing_amount, 0)) <= 100 THEN 'NEGLIGIBLE_DOLLAR_EXPOSURE'

            -- 7. Duplicate billing flag (CARR diverges from Zuora) - informational, after CLEAR
            WHEN zuora_quantity IS NOT NULL AND carr_quantity IS NOT NULL
                 AND ABS(COALESCE(zuora_quantity, 0) - COALESCE(carr_quantity, 0)) > GREATEST(3, COALESCE(zuora_quantity, 0) * 0.05)
                THEN 'DUPLICATE_BILLING'

            -- 8. Vendor > billing (overage pattern)
            WHEN vendor_quantity > total_billing_quantity THEN
                CASE
                    WHEN (vendor_quantity - total_billing_quantity) <= GREATEST(10, vendor_quantity * 0.25) THEN 'OVERAGE_EXPECTED'
                    ELSE 'MATERIAL_UNDER_VENDOR'
                END

            -- 9. Billing > vendor
            WHEN total_billing_quantity > vendor_quantity THEN
                CASE
                    WHEN (total_billing_quantity - vendor_quantity) <= GREATEST(10, vendor_quantity * 0.25) THEN 'BILLING_DIFFERENTIAL_OVER'
                    ELSE 'MATERIAL_OVER_VENDOR'
                END

            -- 10. Fallback
            ELSE 'REVIEW_EXCEPTION'
        END AS outcome_flag
    FROM joined
    WHERE COALESCE(vendor_quantity, 0) > 0
       OR COALESCE(total_billing_quantity, 0) > 0
)
SELECT
    'KeepIT' AS VENDOR,
    billing_month AS BILLING_MONTH,
    source_family AS SOURCE_FAMILY,
    sf_id,
    cms_id,
    cms_ids,
    resolved_partner_name AS vendor_partner_name,
    vendor_source_families,
    sku_match_group AS VENDOR_PRODUCT,
    cw_skus,
    zuora_skus,
    zuora_charge_names,
    carr_skus,
    billing_source_mix,
    vendor_quantity,
    vendor_unit_price,
    vendor_amount,
    zuora_quantity,
    zuora_unit_price,
    zuora_amount,
    carr_quantity AS marketplace_quantity,
    carr_amount AS marketplace_amount,
    total_billing_quantity,
    total_billing_unit_price,
    total_billing_amount,
    qty_delta,
    abs_qty_delta,
    amount_delta,
    abs_amount_delta,
    duplicate_billing_flag,
    FALSE AS marketplace_timing_flag,
    0::FLOAT AS marketplace_timing_quantity,
    vendor_source_row_count,
    vendor_partner_guid_count,
    vendor_unmapped_partner_rows,
    CASE
        WHEN sf_id IS NOT NULL AND vendor_quantity > 0 THEN 'MANUAL_ACCOUNT2_CMS_CROSSWALK_TO_SALESFORCE'
        WHEN vendor_quantity > 0 THEN 'PARTNER_MAPPING_REQUIRED'
        WHEN resolved_partner_name IS NOT NULL THEN 'SF_ID_REVERSE_LOOKUP'
        ELSE 'BILLING_ONLY'
    END AS partner_match_methods,
    'KEEPIT_SKU_MAP|SOURCE_FAMILY|' || source_family AS sku_mapping_sources,
    NULL::NUMBER AS contract_cost_basis_quantity,
    NULL::NUMBER AS contract_cost_basis_amount,
    NULL::NUMBER AS contract_cost_rate,
    NULL::NUMBER AS billing_vs_cost_delta_per_seat,
    NULL::NUMBER AS billing_vs_cost_dollar_impact,
    NULL::NUMBER AS billing_vs_cost_pct,
    NULL::VARCHAR AS contract_price_flag,
    FALSE AS material_below_cost_flag,
    NULL::VARCHAR AS contract_rate_source_docs,
    CURRENT_TIMESTAMP() AS recon_run_ts,
    outcome_flag,
    CASE
        WHEN outcome_flag = 'PARTNER_MAPPING_REQUIRED' THEN 'Vendor usage has KeepIT partner GUID but no resolved ACT account from governed CMS crosswalk.'
        WHEN outcome_flag = 'STRUCTURAL_VENDOR_ONLY' THEN 'Vendor summary usage exists for this account/source family/SKU with no matching posted Zuora usage. Requires CW subscription creation.'
        WHEN outcome_flag = 'STRUCTURAL_BILLING_ONLY' THEN 'Posted Zuora usage exists with no matching KeepIT summary usage at the account/source family/SKU grain.'
        WHEN outcome_flag = 'CARR_SECONDARY_CHECK_ONLY' THEN 'CARR/marketplace has KeepIT-like billing evidence, but posted Zuora has no comparable usage; retained as secondary evidence only.'
        WHEN outcome_flag = 'TAKEOUT_SUPPORT_FILE_NO_DIRECT_BILLING' THEN 'Takeout summary usage is retained in master usage, but no posted Zuora source family is mapped yet.'
        WHEN outcome_flag = 'CW_INCLUDED_ZERO_DOLLAR' THEN 'CW tracks this workload at $0 (included in bundle). Vendor does not report usage for this account. No action required.'
        WHEN outcome_flag = 'CW_ONLY_ADDON_NO_VENDOR' THEN 'CW-only add-on SKU (e.g., Unlimited Retention) with no KeepIT vendor counterpart. No reconciliation possible.'
        WHEN outcome_flag = 'NEGLIGIBLE_DOLLAR_EXPOSURE' THEN 'Variance exists but total dollar exposure is <= $100. No action required.'
        WHEN outcome_flag = 'OVERAGE_EXPECTED' THEN 'Vendor usage exceeds CW billing within expected overage band (<=25%). Typical growth pattern.'
        WHEN outcome_flag = 'MATERIAL_UNDER_VENDOR' THEN 'Vendor usage exceeds CW billing by >25%. Review for missing billing or overage capture.'
        WHEN outcome_flag = 'MATERIAL_OVER_VENDOR' THEN 'CW billing exceeds vendor usage by >25%. Review for stale subscription or over-billing.'
        WHEN outcome_flag = 'BILLING_DIFFERENTIAL_OVER' THEN 'CW billing exceeds vendor usage in the 5-25% band. Minor drift; validate seat count.'
        WHEN outcome_flag = 'DUPLICATE_BILLING' THEN 'Zuora and CARR both billing material qty that materially diverge; likely double-billing.'
        ELSE NULL
    END AS investigation_reason,
    CASE
        WHEN outcome_flag IN ('CLEAR', 'CW_INCLUDED_ZERO_DOLLAR', 'CW_ONLY_ADDON_NO_VENDOR',
                              'NEGLIGIBLE_DOLLAR_EXPOSURE', 'OVERAGE_EXPECTED',
                              'CARR_SECONDARY_CHECK_ONLY', 'TAKEOUT_SUPPORT_FILE_NO_DIRECT_BILLING') THEN FALSE
        ELSE TRUE
    END AS billing_action_required,
    NULL::NUMBER AS vendor_vs_contract_delta_per_seat,
    NULL::NUMBER AS vendor_vs_contract_pct,
    NULL::VARCHAR AS vendor_vs_contract_flag,
    NULL::NUMBER AS vendor_vs_contract_dollar_impact
FROM scored;

CREATE OR REPLACE TABLE KEEPIT_RECON_SUMMARY AS
SELECT
    BILLING_MONTH,
    SOURCE_FAMILY,
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
    COUNT_IF(duplicate_billing_flag = TRUE) AS duplicate_billing_rows,
    SUM(IFF(duplicate_billing_flag, vendor_quantity, 0))::NUMBER AS duplicate_billing_vendor_seats,
    SUM(IFF(duplicate_billing_flag, zuora_quantity, 0)) AS duplicate_billing_zuora_seats,
    SUM(IFF(duplicate_billing_flag, marketplace_quantity, 0)) AS duplicate_billing_marketplace_seats,
    SUM(IFF(duplicate_billing_flag, abs_qty_delta, 0)) AS duplicate_billing_abs_qty_variance_impact,
    SUM(IFF(duplicate_billing_flag, abs_amount_delta, 0)) AS duplicate_billing_abs_amount_variance_impact,
    COUNT_IF(outcome_flag = 'PARTNER_MAPPING_REQUIRED') AS unmapped_rows,
    COUNT_IF(outcome_flag = 'STRUCTURAL_VENDOR_ONLY') AS no_billing_rows,
    COUNT_IF(outcome_flag = 'STRUCTURAL_BILLING_ONLY') AS billing_only_rows,
    COUNT_IF(outcome_flag = 'MATERIAL_OVER_VENDOR') AS billing_over_rows,
    COUNT_IF(outcome_flag IN ('MATERIAL_UNDER_VENDOR', 'OVERAGE_EXPECTED')) AS vendor_over_rows,
    COUNT_IF(outcome_flag = 'TAKEOUT_SUPPORT_FILE_NO_DIRECT_BILLING') AS takeout_support_rows,
    COUNT_IF(outcome_flag = 'CW_INCLUDED_ZERO_DOLLAR') AS cw_included_zero_dollar_rows,
    COUNT_IF(outcome_flag = 'CW_ONLY_ADDON_NO_VENDOR') AS cw_only_addon_rows,
    COUNT_IF(outcome_flag = 'NEGLIGIBLE_DOLLAR_EXPOSURE') AS negligible_dollar_exposure_rows,
    COUNT_IF(outcome_flag = 'OVERAGE_EXPECTED') AS overage_expected_rows,
    COUNT_IF(outcome_flag = 'BILLING_DIFFERENTIAL_OVER') AS billing_differential_over_rows,
    -- Actionable clear % excludes non-reconcilable rows
    ROUND(COUNT_IF(outcome_flag = 'CLEAR') * 100.0 / 
        NULLIF(COUNT(*) - COUNT_IF(outcome_flag IN ('CW_INCLUDED_ZERO_DOLLAR', 'CW_ONLY_ADDON_NO_VENDOR', 'TAKEOUT_SUPPORT_FILE_NO_DIRECT_BILLING')), 0), 1) AS actionable_clear_pct,
    COUNT_IF(contract_price_flag = 'ABOVE_COST') AS contract_above_cost_rows,
    COUNT_IF(contract_price_flag = 'AT_COST') AS contract_at_cost_rows,
    COUNT_IF(contract_price_flag = 'BELOW_COST') AS contract_below_cost_rows,
    COUNT_IF(material_below_cost_flag = TRUE) AS contract_material_below_cost_rows,
    COUNT_IF(contract_price_flag IS NULL) AS contract_no_rate_rows,
    COALESCE(SUM(IFF(contract_price_flag = 'ABOVE_COST', billing_vs_cost_dollar_impact, 0)), 0) AS contract_above_cost_margin_dollars,
    COALESCE(SUM(IFF(contract_price_flag = 'BELOW_COST', billing_vs_cost_dollar_impact, 0)), 0) AS contract_below_cost_loss_dollars,
    COALESCE(SUM(IFF(material_below_cost_flag = TRUE, billing_vs_cost_dollar_impact, 0)), 0) AS contract_material_below_cost_loss_dollars
FROM KEEPIT_RECON_DETAIL
GROUP BY 1, 2
ORDER BY 1, 2;



