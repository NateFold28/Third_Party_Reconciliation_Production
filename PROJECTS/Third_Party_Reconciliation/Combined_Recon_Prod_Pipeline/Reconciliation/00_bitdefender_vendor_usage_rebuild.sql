-- =============================================================================
-- 00_bitdefender_vendor_usage_rebuild.sql
-- Native replacement for the deprecated Excel-based Bitdefender ingestion
-- (Ingestion/_archive/Bitdefender_Vendor_Usage_Ingestion_Prod.py).
--
-- Populates THIRD_PARTY_RECON_VENDOR_USAGE_PROD Bitdefender rows directly from
-- ANALYTICS.DBO.PRODUCT_MANAGEMENT__ROYALTIES using the same inclusion rules
-- as the manual team's Bitdefender_Royalty_Report_Builder.sql:
--
--   Layer 1: current-month Bitdefender Contract + Usage rows
--   Layer 2: prior-month Bitdefender Marketplace rows (one-month billing lag,
--            assigned to the current report month)
--   Layer 3: current-month CW MDR-Bitdefender bundle rows split into two output
--            rows (ATS_EDR + GRAVITYZONE), priced at RECON_SKU_MAP contract rates
--
-- Idempotent: DELETE Bitdefender rows then INSERT.  Run range is Jan 2026 -> CURRENT_MONTH.
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

DELETE FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD WHERE VENDOR = 'Bitdefender';

INSERT INTO THIRD_PARTY_RECON_VENDOR_USAGE_PROD (
    BILLING_MONTH, VENDOR, VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU, MODIFIER,
    QUANTITY, UNIT_PRICE, AMOUNT, CURRENCY
)
WITH month_universe AS (
    -- All months that appear as either a Bitdefender-royalty billing_month OR a
    -- prior-month Marketplace source month.
    SELECT DISTINCT DATE_TRUNC('MONTH', BILLING_MONTH)::DATE AS report_month
    FROM ANALYTICS.DBO.PRODUCT_MANAGEMENT__ROYALTIES
    WHERE VENDOR = 'Bitdefender'
      AND BILLING_MONTH >= '2026-01-01'
      AND BILLING_MONTH <= DATE_TRUNC('MONTH', CURRENT_DATE())
),
mdr_bundle_rates AS (
    -- Bundle component rates from the canonical SKU map (contract_cost_rate).
    -- One MDR-bundle seat -> one ATS_EDR seat + one GRAVITYZONE seat.
    SELECT
        MAX(CASE WHEN sku_match_key = 'ATS_EDR'     THEN contract_cost_rate END) AS ats_edr_rate,
        MAX(CASE WHEN sku_match_key = 'GRAVITYZONE' THEN contract_cost_rate END) AS gz_rate
    FROM RECON_SKU_MAP
    WHERE vendor = 'Bitdefender'
),
-- Layer 1: current-month Contract + Usage
layer_1 AS (
    SELECT
        DATE_TRUNC('MONTH', r.BILLING_MONTH)::DATE AS billing_month,
        r.COMPANY_NAME                             AS vendor_partner_name,
        r.PRODUCT_DESCRIPTION                      AS vendor_product_sku,
        NULLIF(CONCAT_WS(' | ',
            NULLIF(r.THIRD_PARTY_TYPE, ''),
            NULLIF(r.CHARGE_OR_CREDIT, ''),
            NULLIF(r.INVOICE_NUMBER, '')
        ), '') AS modifier,
        r.QTY                                       AS quantity,
        CASE WHEN COALESCE(r.QTY, 0) <> 0 THEN r.AMOUNT / r.QTY END AS unit_price,
        r.AMOUNT                                    AS amount
    FROM ANALYTICS.DBO.PRODUCT_MANAGEMENT__ROYALTIES r
    JOIN month_universe m
      ON m.report_month = DATE_TRUNC('MONTH', r.BILLING_MONTH)::DATE
    WHERE r.VENDOR = 'Bitdefender'
      AND r.THIRD_PARTY_TYPE IN ('Contract', 'Usage')
      AND COALESCE(r.QTY, 0) <> 0
),
-- Layer 2: prior-month Marketplace (billing lag; assigned to CURRENT report month)
layer_2 AS (
    SELECT
        m.report_month AS billing_month,
        r.COMPANY_NAME AS vendor_partner_name,
        r.PRODUCT_DESCRIPTION AS vendor_product_sku,
        NULLIF(CONCAT_WS(' | ',
            NULLIF(r.THIRD_PARTY_TYPE, ''),
            NULLIF(r.CHARGE_OR_CREDIT, ''),
            NULLIF(r.INVOICE_NUMBER, '')
        ), '') AS modifier,
        r.QTY AS quantity,
        CASE WHEN COALESCE(r.QTY, 0) <> 0 THEN r.AMOUNT / r.QTY END AS unit_price,
        r.AMOUNT AS amount
    FROM ANALYTICS.DBO.PRODUCT_MANAGEMENT__ROYALTIES r
    JOIN month_universe m
      ON m.report_month = DATEADD(MONTH, 1, DATE_TRUNC('MONTH', r.BILLING_MONTH)::DATE)
    WHERE r.VENDOR = 'Bitdefender'
      AND r.THIRD_PARTY_TYPE = 'Marketplace'
      AND COALESCE(r.QTY, 0) <> 0
),
-- Layer 3a: current-month CW MDR bundle -> ATS_EDR component
layer_3a AS (
    SELECT
        DATE_TRUNC('MONTH', r.BILLING_MONTH)::DATE AS billing_month,
        r.COMPANY_NAME AS vendor_partner_name,
        'Bitdefender ATS & EDR (MDR bundle split)'::VARCHAR AS vendor_product_sku,
        NULLIF(CONCAT_WS(' | ',
            'MDR_BUNDLE_SPLIT',
            NULLIF(r.CHARGE_OR_CREDIT, ''),
            NULLIF(r.INVOICE_NUMBER, '')
        ), '') AS modifier,
        r.QTY AS quantity,
        b.ats_edr_rate AS unit_price,
        r.QTY * b.ats_edr_rate AS amount
    FROM ANALYTICS.DBO.PRODUCT_MANAGEMENT__ROYALTIES r
    JOIN month_universe m
      ON m.report_month = DATE_TRUNC('MONTH', r.BILLING_MONTH)::DATE
    CROSS JOIN mdr_bundle_rates b
    WHERE r.VENDOR = 'ConnectWise'
      AND r.PRODUCT_DESCRIPTION ILIKE '%MDR%Bitdefender%'
      AND COALESCE(r.QTY, 0) <> 0
      AND b.ats_edr_rate IS NOT NULL
),
-- Layer 3b: current-month CW MDR bundle -> GRAVITYZONE component
layer_3b AS (
    SELECT
        DATE_TRUNC('MONTH', r.BILLING_MONTH)::DATE AS billing_month,
        r.COMPANY_NAME AS vendor_partner_name,
        'Bitdefender Cloud Sec GravityZone (MDR bundle split)'::VARCHAR AS vendor_product_sku,
        NULLIF(CONCAT_WS(' | ',
            'MDR_BUNDLE_SPLIT',
            NULLIF(r.CHARGE_OR_CREDIT, ''),
            NULLIF(r.INVOICE_NUMBER, '')
        ), '') AS modifier,
        r.QTY AS quantity,
        b.gz_rate AS unit_price,
        r.QTY * b.gz_rate AS amount
    FROM ANALYTICS.DBO.PRODUCT_MANAGEMENT__ROYALTIES r
    JOIN month_universe m
      ON m.report_month = DATE_TRUNC('MONTH', r.BILLING_MONTH)::DATE
    CROSS JOIN mdr_bundle_rates b
    WHERE r.VENDOR = 'ConnectWise'
      AND r.PRODUCT_DESCRIPTION ILIKE '%MDR%Bitdefender%'
      AND COALESCE(r.QTY, 0) <> 0
      AND b.gz_rate IS NOT NULL
),
combined AS (
    SELECT * FROM layer_1
    UNION ALL SELECT * FROM layer_2
    UNION ALL SELECT * FROM layer_3a
    UNION ALL SELECT * FROM layer_3b
)
SELECT
    billing_month,
    'Bitdefender'::VARCHAR                            AS vendor,
    vendor_partner_name,
    vendor_product_sku,
    modifier,
    SUM(quantity)::NUMBER(18,4)                        AS quantity,
    CASE
        WHEN COUNT(DISTINCT unit_price) = 1 THEN MAX(unit_price)::NUMBER(18,6)
        WHEN SUM(quantity) > 0 THEN (SUM(amount) / SUM(quantity))::NUMBER(18,6)
        ELSE NULL::NUMBER(18,6)
    END                                                AS unit_price,
    SUM(amount)::NUMBER(18,4)                          AS amount,
    'USD'::VARCHAR                                     AS currency
FROM combined
GROUP BY billing_month, vendor_partner_name, vendor_product_sku, modifier;
