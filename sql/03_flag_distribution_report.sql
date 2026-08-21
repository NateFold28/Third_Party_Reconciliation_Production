-- =============================================================================
-- FLAG DISTRIBUTION REPORT -- ALL VENDORS, ALL MONTHS
-- =============================================================================
-- Reads from the unified THIRD_PARTY_RECON_DETAIL_PROD table to show how
-- outcome flags are distributed per vendor per billing month.
-- Run AFTER all vendor <Vendor>_Reconciliation_Script_Prod.sql pipelines have been
-- executed so THIRD_PARTY_RECON_DETAIL_PROD is current.
--
-- Outputs:
--   1. FLAG_DISTRIBUTION_BY_VENDOR_MONTH  -- cross-vendor flag pivot
--   2. FLAG_DISTRIBUTION_SUMMARY          -- high-level clear-rate comparison
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

-- ---------------------------------------------------------------------------
-- STEP 1: Read unified recon table (no UNION needed)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TEMPORARY TABLE RECON_ALL_VENDORS_PROD AS
SELECT VENDOR, BILLING_MONTH, SF_ID, SKU_MATCH_GROUP, VENDOR_QUANTITY,
       TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, QTY_DELTA, ABS_QTY_DELTA,
       AMOUNT_DELTA, ABS_AMOUNT_DELTA, OUTCOME_FLAG
FROM THIRD_PARTY_RECON_DETAIL_PROD;

-- ---------------------------------------------------------------------------
-- STEP 2: Per-vendor per-month flag distribution (row counts + pct)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE FLAG_DISTRIBUTION_BY_VENDOR_MONTH AS
SELECT
    vendor,
    BILLING_MONTH,
    COUNT(*)                                                        AS total_rows,
    -- Clear variants
    COUNT_IF(OUTCOME_FLAG = 'Clear')                                AS clear_rows,
    COUNT_IF(OUTCOME_FLAG = 'Clear - Discounted / Bundled')         AS clear_bundled_rows,
    COUNT_IF(OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')) AS combined_clear_rows,
    ROUND(COUNT_IF(OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')) * 100.0
          / NULLIF(COUNT(*), 0), 1)                                 AS combined_clear_pct,
    -- Issues
    COUNT_IF(OUTCOME_FLAG = 'Billed by Vendor, Missing CW Billing') AS vendor_no_cw_rows,
    COUNT_IF(OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing') AS cw_no_vendor_rows,
    COUNT_IF(OUTCOME_FLAG = 'Missing CW Billing - API Confirmed')   AS api_confirmed_rows,
    COUNT_IF(OUTCOME_FLAG = 'Vendor Billing > CW Billing')          AS vendor_over_cw_rows,
    COUNT_IF(OUTCOME_FLAG = 'Overage')                              AS overage_rows,
    COUNT_IF(OUTCOME_FLAG = 'Duplicate Billing')                    AS duplicate_billing_rows,
    COUNT_IF(OUTCOME_FLAG = 'Unmapped SKU')                         AS unmapped_sku_rows,
    COUNT_IF(OUTCOME_FLAG = 'Other Issue')                          AS other_issue_rows,
    -- Quantity metrics
    SUM(COALESCE(VENDOR_QUANTITY, 0))::NUMBER                       AS total_vendor_seats,
    SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0))::NUMBER                AS total_billing_seats,
    SUM(ABS_QTY_DELTA)                                              AS total_abs_qty_delta,
    -- Dollar metrics
    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2)                       AS total_vendor_amount,
    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2)                AS total_billing_amount,
    -- Pct breakdowns for non-clear flags
    ROUND(COUNT_IF(OUTCOME_FLAG = 'Billed by Vendor, Missing CW Billing') * 100.0
          / NULLIF(COUNT(*), 0), 1)                                 AS vendor_no_cw_pct,
    ROUND(COUNT_IF(OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing') * 100.0
          / NULLIF(COUNT(*), 0), 1)                                 AS cw_no_vendor_pct,
    ROUND(COUNT_IF(OUTCOME_FLAG = 'Unmapped SKU') * 100.0
          / NULLIF(COUNT(*), 0), 1)                                 AS unmapped_pct
FROM RECON_ALL_VENDORS_PROD
GROUP BY 1, 2
ORDER BY 1, 2;

-- ---------------------------------------------------------------------------
-- STEP 3: High-level clear-rate summary by vendor (latest 3 months)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE FLAG_DISTRIBUTION_SUMMARY AS
WITH ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY vendor ORDER BY BILLING_MONTH DESC) AS month_rank
    FROM FLAG_DISTRIBUTION_BY_VENDOR_MONTH
)
SELECT
    vendor,
    BILLING_MONTH,
    total_rows,
    combined_clear_rows,
    combined_clear_pct,
    unmapped_sku_rows,
    vendor_no_cw_rows,
    cw_no_vendor_rows,
    vendor_over_cw_rows,
    overage_rows,
    duplicate_billing_rows,
    api_confirmed_rows,
    other_issue_rows,
    total_vendor_seats,
    total_billing_seats,
    total_abs_qty_delta,
    total_vendor_amount,
    total_billing_amount
FROM ranked
ORDER BY vendor, BILLING_MONTH DESC;

-- ---------------------------------------------------------------------------
-- STEP 4: Quick pivot — clear rate by vendor across all months (for review)
-- ---------------------------------------------------------------------------
SELECT
    vendor,
    BILLING_MONTH,
    total_rows,
    combined_clear_rows,
    combined_clear_pct                                              AS clear_rate_pct,
    unmapped_sku_rows,
    vendor_no_cw_rows,
    api_confirmed_rows,
    vendor_over_cw_rows,
    overage_rows,
    duplicate_billing_rows,
    other_issue_rows,
    total_abs_qty_delta,
    ROUND(total_vendor_amount, 0)                                   AS vendor_amt,
    ROUND(total_billing_amount, 0)                                  AS billing_amt
FROM FLAG_DISTRIBUTION_BY_VENDOR_MONTH
ORDER BY vendor, BILLING_MONTH;
