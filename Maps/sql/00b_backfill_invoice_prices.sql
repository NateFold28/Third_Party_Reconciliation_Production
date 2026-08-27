-- =============================================================================
-- DYNAMIC INVOICE PRICE BACKFILL
-- =============================================================================
-- For any row in THIRD_PARTY_RECON_VENDOR_USAGE_PROD where UNIT_PRICE IS NULL
-- or AMOUNT IS NULL (or both = 0), this step:
--   1. Looks up the rate from THIRD_PARTY_RECON_VENDOR_INVOICES for the exact
--      (VENDOR, VENDOR_PRODUCT_SKU, BILLING_MONTH) combination.
--   2. If that month has no invoice entry, carries forward the MOST RECENT
--      prior month's rate using LAST_VALUE IGNORE NULLS over the rolling
--      (vendor, sku, month ordered ASC) window.
--   3. Recomputes AMOUNT = QUANTITY * backfilled_unit_price where AMOUNT is
--      still NULL or 0 after step 1/2.
--   4. Never overwrites a UNIT_PRICE or AMOUNT that was already populated by
--      the ingestion script (i.e., only fills gaps).
--
-- Vendors that include unit prices in raw usage files (e.g., Proofpoint,
-- SentinelOne) are unaffected — their rows already have non-null AMOUNT.
--
-- Vendors that require invoice rate lookup (e.g., ESET): rows that were
-- ingested without a price will be backfilled here on every pipeline run,
-- so the moment a new invoice month is loaded the rates propagate forward.
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

-- ── Step 1: Build the canonical per-vendor/SKU/month rate table ────────────
-- Uses ConnectWise parent-level rates as the primary lookup (these are the
-- portfolio-level rates applicable to all partners).
-- Falls back to any-partner rate for the same SKU/month if no CW-parent row.
-- Uses LAST_VALUE IGNORE NULLS over the rolling month window to carry rates
-- forward into months where no invoice has been loaded yet.
CREATE OR REPLACE TEMPORARY TABLE _INVOICE_RATE_SPINE AS
WITH
-- All (vendor, sku) combos that appear in usage
usage_skus AS (
    SELECT DISTINCT
        VENDOR                   AS vendor,
        VENDOR_PRODUCT_SKU       AS vendor_product_sku
    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
    WHERE UNIT_PRICE IS NULL OR UNIT_PRICE = 0
      OR AMOUNT    IS NULL OR AMOUNT    = 0
),
-- All months in the usage table for each vendor
usage_months AS (
    SELECT DISTINCT
        VENDOR          AS vendor,
        BILLING_MONTH   AS billing_month
    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
),
-- Cross-join to get all (vendor, sku, month) cells we may need to fill
spine AS (
    SELECT s.vendor, s.vendor_product_sku, m.billing_month
    FROM usage_skus s
    JOIN usage_months m ON m.vendor = s.vendor
),
-- ConnectWise parent-level invoice rates per (vendor, sku, month)
cw_rates AS (
    SELECT
        VENDOR                   AS vendor,
        VENDOR_PRODUCT_SKU       AS vendor_product_sku,
        BILLING_MONTH::DATE      AS billing_month,
        MAX(UNIT_PRICE)          AS unit_price
    FROM THIRD_PARTY_RECON_VENDOR_INVOICES
    WHERE PARTNER ILIKE 'ConnectWise%'
      AND UNIT_PRICE IS NOT NULL
      AND UNIT_PRICE > 0
    GROUP BY 1, 2, 3
),
-- Any-partner fallback rates (used only when no CW-parent row exists)
any_rates AS (
    SELECT
        VENDOR                   AS vendor,
        VENDOR_PRODUCT_SKU       AS vendor_product_sku,
        BILLING_MONTH::DATE      AS billing_month,
        MAX(UNIT_PRICE)          AS unit_price
    FROM THIRD_PARTY_RECON_VENDOR_INVOICES
    WHERE UNIT_PRICE IS NOT NULL
      AND UNIT_PRICE > 0
    GROUP BY 1, 2, 3
),
-- Merge CW-parent + any-partner rates per cell, CW wins
combined AS (
    SELECT
        s.vendor,
        s.vendor_product_sku,
        s.billing_month,
        COALESCE(cw.unit_price, ar.unit_price) AS known_rate
    FROM spine s
    LEFT JOIN cw_rates cw
        ON  cw.vendor            = s.vendor
        AND cw.vendor_product_sku = s.vendor_product_sku
        AND cw.billing_month      = s.billing_month
    LEFT JOIN any_rates ar
        ON  ar.vendor            = s.vendor
        AND ar.vendor_product_sku = s.vendor_product_sku
        AND ar.billing_month      = s.billing_month
)
-- Carry the most recent known rate forward into months with no invoice yet
-- LAST_VALUE IGNORE NULLS fills gaps from the last non-null value.
-- If no prior rate exists (brand-new SKU), known_rate remains NULL
-- and no update is made (preserving the original NULL so ops can investigate).
SELECT
    vendor,
    vendor_product_sku,
    billing_month,
    known_rate,
    LAST_VALUE(known_rate) IGNORE NULLS OVER (
        PARTITION BY vendor, vendor_product_sku
        ORDER BY billing_month
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS backfill_rate
FROM combined;

-- ── Step 2: Update VENDOR_USAGE_PROD with backfilled prices ───────────────
-- Three targeted UPDATE blocks with different update conditions per vendor group:
--
--   Block A  SentinelOne — force-update every pipeline run.
--            Uses SENTINELONE_SKU_INVOICE_RATE_MAP to translate invoice codes
--            (e.g., S1ES-CMP-EN-T8-SA) → usage sku_match_groups (e.g., Complete).
--            Pulls the rate from VENDOR_INVOICES by month; carries forward when
--            no invoice exists for that month.
--
--   Block B  Acronis — force-update every pipeline run.
--            SKUs in VENDOR_USAGE_PROD (e.g., SPEAMSENS) match VENDOR_INVOICES
--            directly; average price across invoice lines for the month.
--
--   Block C  All other vendors — only fill NULL/zero gaps.
--            Vendors whose raw files already include unit prices (Webroot,
--            Proofpoint, Auvik, KeepIT, Exium, ESET) are not overwritten.

-- ── Block A: SentinelOne — dynamic per-SKU rates via mapping table ─────────
-- The rate spine is already built above; we join through the mapping table to
-- translate the invoice SKU code back to the usage sku_match_group.
CREATE OR REPLACE TEMPORARY TABLE _S1_RATE_SPINE AS
WITH
-- Per-month rates from VENDOR_INVOICES joined through the SKU mapping table
s1_monthly AS (
    SELECT
        inv.BILLING_MONTH::DATE                          AS billing_month,
        m.SKU_MATCH_GROUP,
        AVG(NULLIF(inv.UNIT_PRICE, 0))                   AS monthly_rate
    FROM THIRD_PARTY_RECON_VENDOR_INVOICES inv
    JOIN SENTINELONE_SKU_INVOICE_RATE_MAP  m
        ON m.VENDOR_INVOICE_SKU = inv.VENDOR_PRODUCT_SKU
    WHERE inv.VENDOR = 'SentinelOne'
      AND inv.UNIT_PRICE IS NOT NULL AND inv.UNIT_PRICE > 0
    GROUP BY 1, 2
),
-- All (sku_match_group, billing_month) cells from usage table
s1_cells AS (
    SELECT DISTINCT
        VENDOR_PRODUCT_SKU AS sku_match_group,
        BILLING_MONTH::DATE AS billing_month
    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
    WHERE VENDOR = 'SentinelOne'
),
-- Join and carry forward with LAST_VALUE IGNORE NULLS
spine AS (
    SELECT
        c.sku_match_group,
        c.billing_month,
        r.monthly_rate AS known_rate
    FROM s1_cells c
    LEFT JOIN s1_monthly r
        ON  r.SKU_MATCH_GROUP = c.sku_match_group
        AND r.billing_month    = c.billing_month
)
SELECT
    sku_match_group,
    billing_month,
    known_rate,
    LAST_VALUE(known_rate) IGNORE NULLS OVER (
        PARTITION BY sku_match_group
        ORDER BY billing_month
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS backfill_rate
FROM spine;

-- Also pull a fallback rate directly from the mapping seed CSV
-- (used when no invoice data exists at all for a given sku_match_group)
UPDATE THIRD_PARTY_RECON_VENDOR_USAGE_PROD u
SET
    u.UNIT_PRICE = COALESCE(rs.backfill_rate, m.VENDOR_INVOICE_UNIT_PRICE),
    u.AMOUNT     = ROUND(
                     COALESCE(u.QUANTITY, 0) *
                     COALESCE(rs.backfill_rate, m.VENDOR_INVOICE_UNIT_PRICE),
                     6)
FROM _S1_RATE_SPINE rs
JOIN SENTINELONE_SKU_INVOICE_RATE_MAP m ON m.SKU_MATCH_GROUP = rs.sku_match_group
WHERE rs.sku_match_group = u.VENDOR_PRODUCT_SKU
  AND rs.billing_month   = u.BILLING_MONTH::DATE
  AND u.VENDOR           = 'SentinelOne'
  AND COALESCE(rs.backfill_rate, m.VENDOR_INVOICE_UNIT_PRICE) IS NOT NULL;

-- ── Block B: Acronis — dynamic per-SKU rates direct from VENDOR_INVOICES ───
-- Acronis SKU codes in VENDOR_USAGE_PROD (e.g., SPEAMSENS) match VENDOR_INVOICES
-- directly. Average the per-invoice rates by month and carry forward when missing.
CREATE OR REPLACE TEMPORARY TABLE _ACRONIS_RATE_SPINE AS
WITH
acronis_monthly AS (
    SELECT
        BILLING_MONTH::DATE        AS billing_month,
        VENDOR_PRODUCT_SKU,
        AVG(NULLIF(UNIT_PRICE, 0)) AS monthly_rate
    FROM THIRD_PARTY_RECON_VENDOR_INVOICES
    WHERE VENDOR = 'Acronis'
      AND UNIT_PRICE IS NOT NULL AND UNIT_PRICE > 0
    GROUP BY 1, 2
),
acronis_cells AS (
    SELECT DISTINCT
        VENDOR_PRODUCT_SKU,
        BILLING_MONTH::DATE AS billing_month
    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
    WHERE VENDOR = 'Acronis'
),
spine AS (
    SELECT
        c.VENDOR_PRODUCT_SKU,
        c.billing_month,
        r.monthly_rate AS known_rate
    FROM acronis_cells c
    LEFT JOIN acronis_monthly r
        ON  r.VENDOR_PRODUCT_SKU = c.VENDOR_PRODUCT_SKU
        AND r.billing_month       = c.billing_month
)
SELECT
    VENDOR_PRODUCT_SKU,
    billing_month,
    known_rate,
    LAST_VALUE(known_rate) IGNORE NULLS OVER (
        PARTITION BY VENDOR_PRODUCT_SKU
        ORDER BY billing_month
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS backfill_rate
FROM spine;

UPDATE THIRD_PARTY_RECON_VENDOR_USAGE_PROD u
SET
    u.UNIT_PRICE = r.backfill_rate,
    u.AMOUNT     = ROUND(COALESCE(u.QUANTITY, 0) * r.backfill_rate, 6)
FROM _ACRONIS_RATE_SPINE r
WHERE r.VENDOR_PRODUCT_SKU = u.VENDOR_PRODUCT_SKU
  AND r.billing_month       = u.BILLING_MONTH::DATE
  AND u.VENDOR              = 'Acronis'
  AND r.backfill_rate IS NOT NULL;

-- ── Block C: All other vendors — fill NULL/zero gaps only ─────────────────
-- Webroot, Proofpoint, Auvik, KeepIT, Exium already include unit prices in
-- their raw usage files and are NOT overwritten here. ESET is handled by its
-- own invoice_rates CTE inside <Vendor>_Reconciliation_Script_Prod.sql. Bitdefender uses
-- internal cost accounting (no invoice rows).
UPDATE THIRD_PARTY_RECON_VENDOR_USAGE_PROD u
SET
    u.UNIT_PRICE = CASE
        WHEN u.UNIT_PRICE IS NULL OR u.UNIT_PRICE = 0 THEN r.backfill_rate
        ELSE u.UNIT_PRICE
    END,
    u.AMOUNT     = CASE
        WHEN u.AMOUNT IS NULL OR u.AMOUNT = 0
            THEN ROUND(COALESCE(u.QUANTITY, 0) * r.backfill_rate, 6)
        ELSE u.AMOUNT
    END
FROM _INVOICE_RATE_SPINE r
WHERE r.vendor            = u.VENDOR
  AND r.vendor_product_sku = u.VENDOR_PRODUCT_SKU
  AND r.billing_month      = u.BILLING_MONTH::DATE
  AND r.backfill_rate IS NOT NULL
  AND u.VENDOR NOT IN ('SentinelOne', 'Acronis')
    -- Preserve intentional zero-dollar Auvik overage lines from the vendor feed.
    -- Those rows represent waived overage and should not be converted into
    -- synthetic negative charges via QUANTITY * backfill_rate.
    AND NOT (u.VENDOR = 'Auvik' AND COALESCE(u.AMOUNT, 0) = 0)
  -- Only fill genuine gaps — do not overwrite existing values
  AND (u.UNIT_PRICE IS NULL OR u.UNIT_PRICE = 0
       OR u.AMOUNT   IS NULL OR u.AMOUNT   = 0);
