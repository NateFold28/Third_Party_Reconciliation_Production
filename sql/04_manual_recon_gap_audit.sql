-- =============================================================================
-- MANUAL RECON GAP AUDIT
-- =============================================================================
-- Purpose: Surface the gaps between the automated _PROD pipeline outputs and
-- what the manual reconciliation team marks as "clear" in their workbooks.
--
-- How to use:
--   1. Run all vendor 02_final_reconciliation.sql files to refresh _PROD tables.
--   2. Run 03_flag_distribution_report.sql.
--   3. Run this script.
--   4. Compare FLAG_GAP_AUDIT results against the manual recon workbooks
--      (the "data" or "consolidated" tabs in each vendor's monthly file).
--
-- Key gap categories identified from manual workbook reverse-engineering:
--   A. UNMAPPED PARTNERS: pipeline can't resolve sf_id → "Unmapped SKU" in
--      automated but "clear" in manual (team knows the account by name).
--   B. SKU SCOPE MISMATCH: pipeline includes/excludes different charge names
--      than the manual team's scope filter (e.g., KeepIT retention add-ons
--      included in automated but excluded in manual).
--   C. BILLING SIDE TIMING: Marketplace or Zuora billing in a different month
--      than vendor usage → automated flags "Billed by Vendor, Missing CW Billing"
--      but manual team accepts timing lag.
--   D. BUNDLED/PACKAGE BILLING: Auvik-style account-level package pricing means
--      vendor quantity (devices) ≠ CW quantity (packages) → automated qty delta
--      high but manual team clears on amount-match.
--   E. VENDOR-ONLY EXCLUDED FROM MANUAL: Some vendor rows excluded from manual
--      recon scope entirely (e.g., KeepIT Promo, KeepIT Retention Add-On).
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

-- ---------------------------------------------------------------------------
-- BLOCK 1: Pipeline clear rate vs expected manual clear rate by vendor/month
-- ---------------------------------------------------------------------------
-- This gives you the starting-point comparison table.
-- Paste in the manual clear counts after reviewing each workbook.
-- ---------------------------------------------------------------------------
SELECT
    vendor,
    BILLING_MONTH,
    total_rows                                                         AS pipeline_total_rows,
    combined_clear_rows                                                AS pipeline_clear_rows,
    combined_clear_pct                                                 AS pipeline_clear_pct,
    unmapped_sku_rows,
    vendor_no_cw_rows,
    cw_no_vendor_rows,
    vendor_over_cw_rows,
    overage_rows,
    duplicate_billing_rows,
    api_confirmed_rows,
    other_issue_rows,
    total_abs_qty_delta                                                AS pipeline_abs_qty_delta,
    total_vendor_seats,
    total_billing_seats
FROM FLAG_DISTRIBUTION_BY_VENDOR_MONTH
ORDER BY vendor, BILLING_MONTH;

-- ---------------------------------------------------------------------------
-- BLOCK 2: Gap category A — Unmapped partners (sf_id NOT ILIKE 'ACT-%')
-- These rows the pipeline flags as "Unmapped SKU" but manual team clears
-- by name because they know the account even without an SF ID.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TEMPORARY TABLE GAP_A_UNMAPPED_PARTNERS AS
SELECT
    'Acronis'         AS vendor, BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, SKU_MATCH_GROUP,
    VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, OUTCOME_FLAG
FROM ACRONIS_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Unmapped SKU'

UNION ALL
SELECT 'Auvik',       BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, SKU_MATCH_GROUP,
    VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, OUTCOME_FLAG
FROM AUVIK_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Unmapped SKU'

UNION ALL
SELECT 'Bitdefender', BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, SKU_MATCH_GROUP,
    VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, OUTCOME_FLAG
FROM BITDEFENDER_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Unmapped SKU'

UNION ALL
SELECT 'ESET',        BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, SKU_MATCH_GROUP,
    VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, NULL, TOTAL_BILLING_AMOUNT, OUTCOME_FLAG
FROM ESET_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Unmapped SKU'

UNION ALL
SELECT 'Exium',       BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, SKU_MATCH_GROUP,
    VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, OUTCOME_FLAG
FROM EXIUM_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Unmapped SKU'

UNION ALL
SELECT 'KeepIT',      BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, SKU_MATCH_GROUP,
    VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, OUTCOME_FLAG
FROM KEEPIT_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Unmapped SKU'

UNION ALL
SELECT 'Proofpoint',  BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, SKU_MATCH_GROUP,
    VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, OUTCOME_FLAG
FROM PROOFPOINT_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Unmapped SKU'

UNION ALL
SELECT 'SentinelOne', BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, SKU_MATCH_GROUP,
    VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, OUTCOME_FLAG
FROM SENTINELONE_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Unmapped SKU'

UNION ALL
SELECT 'Webroot',     BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, SKU_MATCH_GROUP,
    VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, OUTCOME_FLAG
FROM WEBROOT_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Unmapped SKU';

-- Summary of unmapped by vendor/month
SELECT
    vendor,
    BILLING_MONTH,
    COUNT(*)                                            AS unmapped_rows,
    COUNT(DISTINCT VENDOR_PARTNER_NAME)                 AS distinct_partner_names,
    SUM(VENDOR_QUANTITY)                                AS unmapped_vendor_seats,
    ROUND(SUM(VENDOR_AMOUNT), 2)                        AS unmapped_vendor_amount
FROM GAP_A_UNMAPPED_PARTNERS
GROUP BY 1, 2
ORDER BY 1, 2;

-- ---------------------------------------------------------------------------
-- BLOCK 3: Gap category B — CW billing present but no vendor usage
-- These are rows the pipeline flags as "Billed by CW, Missing Vendor Billing"
-- but the manual team may clear because they know billing is legitimate
-- (e.g., CW-only SKUs, retention add-ons, promo billing excluded from vendor).
-- ---------------------------------------------------------------------------
SELECT
    v,
    BILLING_MONTH,
    COUNT(*)                                            AS cw_no_vendor_rows,
    SUM(TOTAL_BILLING_QUANTITY)::NUMBER                 AS billing_seats,
    ROUND(SUM(TOTAL_BILLING_AMOUNT), 2)                 AS billing_amount,
    ROUND(SUM(VENDOR_AMOUNT), 2)                        AS vendor_amount
FROM (
    SELECT 'Acronis'     AS v, BILLING_MONTH, TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT, VENDOR_AMOUNT
    FROM ACRONIS_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing'
    UNION ALL SELECT 'Auvik',       BILLING_MONTH, TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT, VENDOR_AMOUNT FROM AUVIK_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing'
    UNION ALL SELECT 'Bitdefender', BILLING_MONTH, TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT, VENDOR_AMOUNT FROM BITDEFENDER_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing'
    UNION ALL SELECT 'ESET',        BILLING_MONTH, TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT, NULL FROM ESET_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing'
    UNION ALL SELECT 'Exium',       BILLING_MONTH, TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT, VENDOR_AMOUNT FROM EXIUM_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing'
    UNION ALL SELECT 'KeepIT',      BILLING_MONTH, TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT, VENDOR_AMOUNT FROM KEEPIT_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing'
    UNION ALL SELECT 'Proofpoint',  BILLING_MONTH, TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT, VENDOR_AMOUNT FROM PROOFPOINT_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing'
    UNION ALL SELECT 'SentinelOne', BILLING_MONTH, TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT, VENDOR_AMOUNT FROM SENTINELONE_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing'
    UNION ALL SELECT 'Webroot',     BILLING_MONTH, TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT, VENDOR_AMOUNT FROM WEBROOT_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing'
) x
GROUP BY 1, 2 ORDER BY 1, 2;

-- ---------------------------------------------------------------------------
-- BLOCK 4: Gap category C — Vendor billed, no CW billing
-- The manual team may clear some of these because they have confirmed the
-- partner onboarded, billing was delayed, or it's a timing window.
-- Filter to material rows (vendor amount > $100) to focus attention.
-- ---------------------------------------------------------------------------
SELECT
    v,
    BILLING_MONTH,
    COUNT(*)                                            AS vendor_no_cw_rows,
    SUM(VENDOR_QUANTITY)::NUMBER                        AS missing_vendor_seats,
    ROUND(SUM(VENDOR_AMOUNT), 2)                        AS missing_vendor_amount,
    -- Rows the manual team typically clears (small dollar, known timing)
    COUNT_IF(COALESCE(VENDOR_AMOUNT, 0) <= 100)         AS likely_timing_small_dollar,
    COUNT_IF(COALESCE(VENDOR_AMOUNT, 0) > 100)          AS material_missing_billing
FROM (
    SELECT 'Acronis'     AS v, BILLING_MONTH, VENDOR_QUANTITY, VENDOR_AMOUNT FROM ACRONIS_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by Vendor, Missing CW Billing'
    UNION ALL SELECT 'Auvik',       BILLING_MONTH, VENDOR_QUANTITY, VENDOR_AMOUNT FROM AUVIK_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by Vendor, Missing CW Billing'
    UNION ALL SELECT 'Bitdefender', BILLING_MONTH, VENDOR_QUANTITY, VENDOR_AMOUNT FROM BITDEFENDER_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by Vendor, Missing CW Billing'
    UNION ALL SELECT 'ESET',        BILLING_MONTH, VENDOR_QUANTITY, NULL FROM ESET_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by Vendor, Missing CW Billing'
    UNION ALL SELECT 'Exium',       BILLING_MONTH, VENDOR_QUANTITY, VENDOR_AMOUNT FROM EXIUM_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by Vendor, Missing CW Billing'
    UNION ALL SELECT 'KeepIT',      BILLING_MONTH, VENDOR_QUANTITY, VENDOR_AMOUNT FROM KEEPIT_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by Vendor, Missing CW Billing'
    UNION ALL SELECT 'Proofpoint',  BILLING_MONTH, VENDOR_QUANTITY, VENDOR_AMOUNT FROM PROOFPOINT_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by Vendor, Missing CW Billing'
    UNION ALL SELECT 'SentinelOne', BILLING_MONTH, VENDOR_QUANTITY, VENDOR_AMOUNT FROM SENTINELONE_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by Vendor, Missing CW Billing'
    UNION ALL SELECT 'Webroot',     BILLING_MONTH, VENDOR_QUANTITY, VENDOR_AMOUNT FROM WEBROOT_RECON_DETAIL_PROD WHERE OUTCOME_FLAG = 'Billed by Vendor, Missing CW Billing'
) x
GROUP BY 1, 2 ORDER BY 1, 2;

-- ---------------------------------------------------------------------------
-- BLOCK 5: ABS_QTY_DELTA distribution — find where pipeline and manual differ
-- The manual team's "data" tab has raw quantities; if our ABS_QTY_DELTA is
-- large for cleared rows, we're clearing on amount-match but qty is off.
-- ---------------------------------------------------------------------------
SELECT
    v,
    BILLING_MONTH,
    COUNT(*)                                            AS clear_rows,
    SUM(ABS_QTY_DELTA)                                  AS abs_qty_delta_on_clear_rows,
    AVG(ABS_QTY_DELTA)                                  AS avg_abs_qty_delta_on_clear,
    -- These "big delta / clear amount" rows are the exact gap with manual recon:
    -- pipeline says "Clear" (amount ok) but manual sees the qty difference
    COUNT_IF(ABS_QTY_DELTA > 10)                        AS clear_rows_with_large_qty_delta
FROM (
    SELECT 'Acronis'     AS v, BILLING_MONTH, ABS_QTY_DELTA FROM ACRONIS_RECON_DETAIL_PROD WHERE OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')
    UNION ALL SELECT 'Auvik',       BILLING_MONTH, ABS_QTY_DELTA FROM AUVIK_RECON_DETAIL_PROD WHERE OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')
    UNION ALL SELECT 'Bitdefender', BILLING_MONTH, ABS_QTY_DELTA FROM BITDEFENDER_RECON_DETAIL_PROD WHERE OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')
    UNION ALL SELECT 'ESET',        BILLING_MONTH, ABS_QTY_DELTA FROM ESET_RECON_DETAIL_PROD WHERE OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')
    UNION ALL SELECT 'Exium',       BILLING_MONTH, ABS_QTY_DELTA FROM EXIUM_RECON_DETAIL_PROD WHERE OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')
    UNION ALL SELECT 'KeepIT',      BILLING_MONTH, ABS_QTY_DELTA FROM KEEPIT_RECON_DETAIL_PROD WHERE OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')
    UNION ALL SELECT 'Proofpoint',  BILLING_MONTH, ABS_QTY_DELTA FROM PROOFPOINT_RECON_DETAIL_PROD WHERE OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')
    UNION ALL SELECT 'SentinelOne', BILLING_MONTH, ABS_QTY_DELTA FROM SENTINELONE_RECON_DETAIL_PROD WHERE OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')
    UNION ALL SELECT 'Webroot',     BILLING_MONTH, ABS_QTY_DELTA FROM WEBROOT_RECON_DETAIL_PROD WHERE OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')
) x
GROUP BY 1, 2 ORDER BY 1, 2;

-- ---------------------------------------------------------------------------
-- BLOCK 6: Known scope exclusions — rows the manual team drops but pipeline
-- includes (inflates automated row count vs manual).
--
-- KeepIT: Retention add-ons and Promo/Recover lines are excluded in manual.
--   In the pipeline these appear as 'Clear - Discounted / Bundled' (RMM bundle)
--   or 'Billed by CW, Missing Vendor Billing' (CW-only retention).
-- Auvik: Billed by CW rows with no vendor data are often legacy/stale
--   subscriptions that manual team already actioned.
-- ---------------------------------------------------------------------------
-- KeepIT scope exclusion estimate (CW-only lines excluded from manual)
SELECT
    'KeepIT - CW-only lines excluded from manual scope' AS gap_category,
    BILLING_MONTH,
    COUNT(*)                                            AS excluded_pipeline_rows,
    SUM(TOTAL_BILLING_QUANTITY)::NUMBER                 AS excluded_billing_seats,
    ROUND(SUM(TOTAL_BILLING_AMOUNT), 2)                 AS excluded_billing_amount
FROM KEEPIT_RECON_DETAIL_PROD
WHERE OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing'
   OR SKU_MATCH_GROUP LIKE 'KEEPIT_CW_ONLY_%'
GROUP BY 2 ORDER BY 2;

-- ---------------------------------------------------------------------------
-- BLOCK 7: Consolidate gap summary per vendor/month
-- Use this to compare directly against manual workbook clear totals.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE FLAG_GAP_AUDIT AS
WITH base AS (
    SELECT vendor, BILLING_MONTH, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY,
           VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, ABS_QTY_DELTA
    FROM (
        SELECT 'Acronis'     AS vendor, BILLING_MONTH, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, ABS_QTY_DELTA FROM ACRONIS_RECON_DETAIL_PROD
        UNION ALL SELECT 'Auvik',       BILLING_MONTH, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, ABS_QTY_DELTA FROM AUVIK_RECON_DETAIL_PROD
        UNION ALL SELECT 'Bitdefender', BILLING_MONTH, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, ABS_QTY_DELTA FROM BITDEFENDER_RECON_DETAIL_PROD
        UNION ALL SELECT 'ESET',        BILLING_MONTH, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, ABS_QTY_DELTA FROM ESET_RECON_DETAIL_PROD
        UNION ALL SELECT 'Exium',       BILLING_MONTH, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, ABS_QTY_DELTA FROM EXIUM_RECON_DETAIL_PROD
        UNION ALL SELECT 'KeepIT',      BILLING_MONTH, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, ABS_QTY_DELTA FROM KEEPIT_RECON_DETAIL_PROD
        UNION ALL SELECT 'Proofpoint',  BILLING_MONTH, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, ABS_QTY_DELTA FROM PROOFPOINT_RECON_DETAIL_PROD
        UNION ALL SELECT 'SentinelOne', BILLING_MONTH, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, ABS_QTY_DELTA FROM SENTINELONE_RECON_DETAIL_PROD
        UNION ALL SELECT 'Webroot',     BILLING_MONTH, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT, ABS_QTY_DELTA FROM WEBROOT_RECON_DETAIL_PROD
    ) all_v
)
SELECT
    vendor,
    BILLING_MONTH,
    COUNT(*)                                                        AS total_pipeline_rows,
    COUNT_IF(OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')) AS pipeline_clear_rows,
    ROUND(COUNT_IF(OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')) * 100.0
          / NULLIF(COUNT(*), 0), 1)                                 AS pipeline_clear_pct,
    -- Rows that look like clear to pipeline but manual may disagree (large qty delta)
    COUNT_IF(OUTCOME_FLAG IN ('Clear','Clear - Discounted / Bundled')
             AND ABS_QTY_DELTA > 10)                                AS clear_rows_with_qty_gap,
    -- Rows the manual team likely clears but pipeline doesn't
    COUNT_IF(OUTCOME_FLAG = 'Unmapped SKU')                         AS unmapped_rows,
    COUNT_IF(OUTCOME_FLAG = 'Billed by Vendor, Missing CW Billing') AS vendor_no_cw_rows,
    COUNT_IF(OUTCOME_FLAG = 'Billed by CW, Missing Vendor Billing') AS cw_no_vendor_rows,
    COUNT_IF(OUTCOME_FLAG IN ('Overage','Vendor Billing > CW Billing')) AS variance_rows,
    COUNT_IF(OUTCOME_FLAG = 'Other Issue')                          AS other_rows,
    -- Qty metrics
    SUM(COALESCE(VENDOR_QUANTITY, 0))::NUMBER                       AS total_vendor_seats,
    SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0))::NUMBER                AS total_billing_seats,
    SUM(ABS_QTY_DELTA)                                              AS total_abs_qty_delta,
    -- $ metrics
    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2)                       AS total_vendor_amount,
    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2)                AS total_billing_amount,
    -- Columns to fill in from manual recon workbooks:
    NULL::NUMBER                                                    AS manual_recon_clear_rows,
    NULL::FLOAT                                                     AS manual_recon_clear_pct,
    NULL::NUMBER                                                    AS manual_recon_abs_qty_delta,
    NULL::VARCHAR                                                   AS gap_notes
FROM base
GROUP BY 1, 2
ORDER BY 1, 2;

-- Final output for review
SELECT * FROM FLAG_GAP_AUDIT ORDER BY vendor, BILLING_MONTH;
