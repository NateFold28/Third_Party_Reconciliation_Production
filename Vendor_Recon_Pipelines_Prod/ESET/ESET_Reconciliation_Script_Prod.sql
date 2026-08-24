-- =============================================================================
-- STEP 2: ESET FINAL RECONCILIATION  (Proofpoint-style, SKU-family grain)
-- =============================================================================
--   Vendor side = ESET_USAGE (MSP-summary rows; billed qty = Seats)
--   CW side     = ESET_BILLING_MATCHED (Zuora, USD)
--               + ESET_MARKETPLACE_BILLING_MATCHED (Marketplace, USD)
--   Grain       = (sf_id, billing_month, sku_match_group)
--   total_billing_quantity = zuora_quantity + marketplace_quantity
--   Reconciliation is on QUANTITY (seats). Vendor USD $ is derived from the
--   contract-rate overlay (the CSV carries no monetary column); it is a
--   secondary axis and never affects the CLEAR rate.
--
-- Output: ESET_RECON_DETAIL (46 cols, schema-identical to AUVIK_RECON_DETAIL)
--         ESET_RECON_SUMMARY (29 cols)
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

CREATE OR REPLACE TABLE ESET_RECON_DETAIL AS

WITH partner_map AS (
    SELECT
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS pn_norm,
        ANY_VALUE(sf_id) AS sf_id
    FROM RECON_PARTNER_MAP
    WHERE sf_id IS NOT NULL AND REGEXP_LIKE(sf_id, '^ACT-[0-9A-Z-]+$') AND partner_name IS NOT NULL
    GROUP BY 1
),

-- ---- Vendor side: ESET_USAGE -> group, mapped to sf_id ----
vendor_rows AS (
    SELECT
        u.BILLING_MONTH::DATE AS billing_month,
        p.sf_id,
        u.VENDOR_PARTNER_NAME,
        u.VENDOR_PRODUCT_SKU,
        CASE
            WHEN UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%PROTECT ENTRY ON-PREM%' THEN 'PROTECT_ENTRY_ONPREM'
            WHEN UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%PROTECT ENTRY%'      THEN 'PROTECT_ENTRY'
            WHEN UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%PROTECT ADVANCED%'   THEN 'PROTECT_ADVANCED'
            WHEN UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%PROTECT COMPLETE%'   THEN 'PROTECT_COMPLETE'
            WHEN UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%PROTECT ENTERPRISE%' THEN 'PROTECT_ENTERPRISE'
            WHEN UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%PROTECT MAIL%'
              OR UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%MAIL SECURITY%'      THEN 'MAIL_SECURITY'
            WHEN UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%INSPECT%'            THEN 'INSPECT_EDR'
            WHEN UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%SERVER SECURITY%'
              OR UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%FILE SECURITY%'      THEN 'SERVER_SECURITY'
            WHEN UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%ENDPOINT SECURITY%'  THEN 'ENDPOINT_SECURITY'
            WHEN UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%ENDPOINT ANTIVIRUS%' THEN 'ENDPOINT_ANTIVIRUS'
            WHEN UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%ENCRYPTION%'         THEN 'ENCRYPTION'
            WHEN UPPER(u.VENDOR_PRODUCT_SKU) LIKE '%SECURE AUTHENTICATION%' THEN 'SECURE_AUTH'
            ELSE 'OTHER'
        END AS sku_match_group,
        COALESCE(u.QUANTITY, 0) AS quantity
    FROM ESET_USAGE u
    LEFT JOIN partner_map p
        ON p.pn_norm = TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(u.VENDOR_PARTNER_NAME), '[^a-z0-9]+', ' '), '\\s+', ' '))
    WHERE COALESCE(u.QUANTITY, 0) > 0
),
vendor_agg AS (
    SELECT
        sf_id, billing_month, sku_match_group,
        LISTAGG(DISTINCT VENDOR_PARTNER_NAME, ' | ') WITHIN GROUP (ORDER BY VENDOR_PARTNER_NAME) AS vendor_partner_name,
        LISTAGG(DISTINCT VENDOR_PRODUCT_SKU, ' | ') WITHIN GROUP (ORDER BY VENDOR_PRODUCT_SKU) AS vendor_product,
        SUM(quantity)  AS vendor_quantity,
        COUNT(*)       AS vendor_row_count
    FROM vendor_rows
    WHERE sf_id IS NOT NULL
    GROUP BY 1, 2, 3
),

-- ESET_BILLING_MATCHED / ESET_MARKETPLACE_BILLING_MATCHED are already unique
-- per (sf_id, billing_month, sku_match_group); select directly (no flatten,
-- which would multiply quantities by the SKU-array length).
-- Only reconcile months for which a vendor usage file exists.
vendor_months AS (
    SELECT DISTINCT BILLING_MONTH::DATE AS billing_month FROM ESET_USAGE
),

zuora_agg2 AS (
    SELECT
        sf_id, billing_month, sku_match_group,
        cw_account_name,
        product_skus AS zuora_skus,
        zuora_quantity,
        zuora_unit_price,
        zuora_charge_amount AS zuora_amount,
        billing_row_count   AS zuora_row_count
    FROM ESET_BILLING_MATCHED
    WHERE billing_month IN (SELECT billing_month FROM vendor_months)
),

mp_agg2 AS (
    SELECT
        sf_id, billing_month, sku_match_group,
        cw_account_name,
        product_skus AS marketplace_skus,
        marketplace_quantity,
        marketplace_amount,
        marketplace_row_count
    FROM ESET_MARKETPLACE_BILLING_MATCHED
    WHERE billing_month IN (SELECT billing_month FROM vendor_months)
),

keys AS (
    SELECT sf_id, billing_month, sku_match_group FROM vendor_agg
    UNION SELECT sf_id, billing_month, sku_match_group FROM zuora_agg2
    UNION SELECT sf_id, billing_month, sku_match_group FROM mp_agg2
),

joined AS (
    SELECT
        k.sf_id,
        k.billing_month,
        k.sku_match_group,
        COALESCE(v.vendor_partner_name, z.cw_account_name, m.cw_account_name) AS vendor_partner_name,
        COALESCE(v.vendor_product, 'ESET ' || k.sku_match_group) AS vendor_product,
        z.zuora_skus AS cw_skus,
        z.zuora_skus,
        m.marketplace_skus,
        CASE
            WHEN z.zuora_quantity IS NOT NULL AND m.marketplace_quantity IS NOT NULL THEN 'ZUORA_AND_MARKETPLACE'
            WHEN z.zuora_quantity IS NOT NULL THEN 'ZUORA_ONLY'
            WHEN m.marketplace_quantity IS NOT NULL THEN 'MARKETPLACE_ONLY'
            ELSE 'NO_BILLING'
        END AS billing_source_mix,
        COALESCE(v.vendor_quantity, 0)::NUMBER AS vendor_quantity,
        z.zuora_quantity,
        z.zuora_unit_price,
        z.zuora_amount,
        m.marketplace_quantity,
        m.marketplace_amount,
        -- Zuora (BillRun) and Marketplace (CARR NetSuite Evergreen Usage) are
        -- OVERLAPPING views of the SAME CW billing -- NOT additive. Verified May
        -- 2026: of 113 (sf_id, group) keys present in both feeds, 104 (92%) have
        -- equal quantities. Summing them double-counts CW billing ~2x. The CW
        -- total per key is therefore the GREATER of the two feeds (the fuller
        -- view), with the dollar amount taken from that same feed.
        GREATEST(COALESCE(z.zuora_quantity, 0), COALESCE(m.marketplace_quantity, 0)) AS total_billing_quantity,
        CASE WHEN COALESCE(m.marketplace_quantity, 0) >= COALESCE(z.zuora_quantity, 0)
             THEN COALESCE(m.marketplace_amount, 0) ELSE COALESCE(z.zuora_amount, 0) END AS total_billing_amount,
        COALESCE(v.vendor_row_count, 0) AS vendor_row_count,
        CASE WHEN v.sf_id IS NOT NULL THEN 'PARTNER_NAME' ELSE 'UNMAPPED' END AS partner_match_methods,
        'VENDOR_USAGE_VS_ZUORA_PLUS_MARKETPLACE|SKU_GROUP' AS sku_mapping_sources
    FROM keys k
    LEFT JOIN vendor_agg v ON v.sf_id = k.sf_id AND v.billing_month = k.billing_month AND v.sku_match_group = k.sku_match_group
    LEFT JOIN zuora_agg2 z ON z.sf_id = k.sf_id AND z.billing_month = k.billing_month AND z.sku_match_group = k.sku_match_group
    LEFT JOIN mp_agg2  m ON m.sf_id = k.sf_id AND m.billing_month = k.billing_month AND m.sku_match_group = k.sku_match_group
),

contract_rates AS (
    SELECT
        sku_match_group,
        currency,
        valid_from,
        valid_to,
        CASE
            WHEN COUNT(DISTINCT contract_cost_rate) = 1 THEN MAX(contract_cost_rate)
            ELSE NULL
        END AS contract_cost_rate,
        LISTAGG(DISTINCT source_doc, ' | ') WITHIN GROUP (ORDER BY source_doc) AS source_doc
    FROM ESET_CONTRACT_RATES
    WHERE currency = 'USD'
    GROUP BY 1, 2, 3, 4
),

with_rate AS (
    SELECT j.*, cr.contract_cost_rate, cr.source_doc AS contract_rate_source_docs
    FROM joined j
    LEFT JOIN contract_rates cr
        ON cr.sku_match_group = j.sku_match_group
       AND j.billing_month BETWEEN cr.valid_from AND cr.valid_to
),

scored AS (
    SELECT
        *,
        -- The ESET usage CSV carries NO monetary column, and the NetSuite ESET
        -- contract rates are unreliable (old / annual-vs-monthly grain-mixed,
        -- see GAPS_VS_PROOFPOINT.md), so we do NOT synthesize a vendor dollar
        -- amount here -- it would be misleading. Reconciliation is on QUANTITY;
        -- vendor $ stays NULL. The contract overlay columns below still expose
        -- billing-vs-cost as a separate (flagged) lane where a rate exists.
        NULL::NUMBER AS vendor_amount,
        NULL::NUMBER AS vendor_unit_price,
        total_billing_amount / NULLIF(total_billing_quantity, 0) AS total_billing_unit_price,
        total_billing_quantity - vendor_quantity      AS qty_delta,
        ABS(total_billing_quantity - vendor_quantity)  AS abs_qty_delta,
        CASE
            WHEN sf_id IS NULL THEN 'PARTNER_MAPPING_REQUIRED'
            WHEN vendor_quantity > 0 AND total_billing_quantity = 0 THEN 'NO_BILLING_NO_HISTORY'
            WHEN vendor_quantity = 0 AND total_billing_quantity > 0 THEN 'BILLING_OVER_VENDOR'
            WHEN ABS(total_billing_quantity - vendor_quantity) <= GREATEST(3, vendor_quantity * 0.03) THEN 'CLEAR'
            WHEN total_billing_quantity > vendor_quantity THEN 'BILLING_OVER_VENDOR'
            WHEN total_billing_quantity < vendor_quantity THEN 'VENDOR_OVER_BILLING'
            ELSE 'REVIEW_EXCEPTION'
        END AS base_outcome_flag
    FROM with_rate
)

SELECT
    s.billing_month AS BILLING_MONTH,
    s.sf_id,
    s.vendor_partner_name,
    s.vendor_product,
    s.sku_match_group,
    s.cw_skus,
    s.zuora_skus,
    s.marketplace_skus,
    s.billing_source_mix,
    s.vendor_quantity,
    s.vendor_unit_price,
    ROUND(s.vendor_amount, 2)::NUMBER AS vendor_amount,
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
    s.total_billing_amount - s.vendor_amount           AS amount_delta,
    ABS(s.total_billing_amount - s.vendor_amount)       AS abs_amount_delta,
    FALSE AS duplicate_billing_flag,
    FALSE AS marketplace_timing_flag,
    0::FLOAT AS marketplace_timing_quantity,
    s.vendor_row_count AS vendor_source_row_count,
    s.partner_match_methods,
    s.sku_mapping_sources,
    s.contract_cost_rate AS contract_cost_basis_quantity,
    ROUND(s.vendor_quantity * COALESCE(s.contract_cost_rate, 0), 2)::NUMBER AS contract_cost_basis_amount,
    s.contract_cost_rate,
    CASE WHEN s.contract_cost_rate IS NOT NULL AND s.total_billing_quantity > 0
        THEN (s.total_billing_amount / s.total_billing_quantity) - s.contract_cost_rate ELSE NULL END AS billing_vs_cost_delta_per_seat,
    CASE WHEN s.contract_cost_rate IS NOT NULL AND s.total_billing_quantity > 0
        THEN ((s.total_billing_amount / s.total_billing_quantity) - s.contract_cost_rate) * s.total_billing_quantity ELSE NULL END AS billing_vs_cost_dollar_impact,
    CASE WHEN s.contract_cost_rate IS NOT NULL AND s.contract_cost_rate > 0 AND s.total_billing_quantity > 0
        THEN ROUND(((s.total_billing_amount / s.total_billing_quantity) - s.contract_cost_rate) / s.contract_cost_rate * 100, 1) ELSE NULL END AS billing_vs_cost_pct,
    CASE
        WHEN s.contract_cost_rate IS NULL THEN NULL
        WHEN s.total_billing_quantity = 0 THEN NULL
        WHEN (s.total_billing_amount / s.total_billing_quantity) > s.contract_cost_rate * 1.05 THEN 'ABOVE_COST'
        WHEN (s.total_billing_amount / s.total_billing_quantity) >= s.contract_cost_rate * 0.95 THEN 'AT_COST'
        ELSE 'BELOW_COST'
    END AS contract_price_flag,
    CASE WHEN s.contract_cost_rate IS NOT NULL AND s.total_billing_quantity > 0
        AND (s.total_billing_amount / s.total_billing_quantity) < s.contract_cost_rate * 0.80 THEN TRUE ELSE FALSE END AS material_below_cost_flag,
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
        WHEN s.base_outcome_flag = 'NO_BILLING_NO_HISTORY' THEN 'Vendor usage present with no matching CW (Zuora or Marketplace) billing for this account/product group.'
        WHEN s.base_outcome_flag = 'VENDOR_OVER_BILLING'   THEN 'Vendor usage qty exceeds CW billing qty (Zuora + Marketplace) for this product group.'
        WHEN s.base_outcome_flag = 'BILLING_OVER_VENDOR'   THEN 'CW billing qty (Zuora + Marketplace) exceeds vendor usage qty for this product group.'
        ELSE NULL
    END AS investigation_reason,
    CASE WHEN s.base_outcome_flag IN ('NO_BILLING_NO_HISTORY', 'VENDOR_OVER_BILLING', 'BILLING_OVER_VENDOR') THEN TRUE ELSE FALSE END AS billing_action_required,
    NULL::NUMBER  AS vendor_vs_contract_delta_per_seat,
    NULL::NUMBER  AS vendor_vs_contract_pct,
    NULL::VARCHAR AS vendor_vs_contract_flag,
    NULL::NUMBER  AS vendor_vs_contract_dollar_impact
FROM scored s;

-- =============================================================================
-- SUMMARY (29-column schema)
-- =============================================================================
CREATE OR REPLACE TABLE ESET_RECON_SUMMARY AS
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
    COALESCE(SUM(IFF(contract_price_flag = 'ABOVE_COST', billing_vs_cost_dollar_impact, 0)), 0) AS contract_above_cost_margin_dollars,
    COALESCE(SUM(IFF(contract_price_flag = 'BELOW_COST', billing_vs_cost_dollar_impact, 0)), 0) AS contract_below_cost_loss_dollars,
    COALESCE(SUM(IFF(material_below_cost_flag = TRUE, billing_vs_cost_dollar_impact, 0)), 0) AS contract_material_below_cost_loss_dollars
FROM ESET_RECON_DETAIL
GROUP BY BILLING_MONTH
ORDER BY BILLING_MONTH;

