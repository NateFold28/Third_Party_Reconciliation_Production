-- =============================================================================
-- THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
--
-- Vendor-owned reconciliation control:
--   Compare parsed vendor invoice lines to raw vendor usage files at the shared
--   vendor / billing_month / vendor SKU grain before CW billing is introduced.
--
-- Delta convention:
--   DELTA_* = raw vendor usage - parsed vendor invoice
--
-- Null-side convention:
--   If Engineering has not refreshed invoice JSONs for a vendor/month/SKU, the
--   invoice-side fields stay NULL while the raw usage side remains visible.
--
-- SKU alignment:
--   Invoice JSONs often carry vendor SKUs (for example PP-ESS-ADV), while raw
--   usage files can carry product labels (for example Advanced). The governed
--   THIRD_PARTY_RECON_SKU_MAP_PROD aliases both to SKU_MATCH_KEY, so this script
--   compares on that canonical key when the alias is unambiguous.
--   The output retains both source-side labels so the app can show the invoice
--   SKU and raw usage SKU that were mapped together.
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

CREATE OR REPLACE TABLE THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD AS
WITH sku_map_base AS (
    SELECT DISTINCT
        UPPER(TRIM(VENDOR)) AS vendor_key,
        NULLIF(
            TRIM(
                REGEXP_REPLACE(
                    REGEXP_REPLACE(
                        UPPER(REGEXP_REPLACE(TRIM(SKU_MATCH_KEY), '[^A-Za-z0-9]+', ' ')),
                        '^OVERAGE\\s+',
                        ''
                    ),
                    '\\s+',
                    ' '
                )
            ),
            ''
        ) AS canonical_sku,
        NULLIF(TRIM(REGEXP_REPLACE(UPPER(REGEXP_REPLACE(TRIM(VENDOR_PRODUCT), '[^A-Za-z0-9]+', ' ')), '\\s+', ' ')), '') AS vendor_product_key,
        NULLIF(TRIM(REGEXP_REPLACE(UPPER(REGEXP_REPLACE(TRIM(VENDOR_SKU), '[^A-Za-z0-9]+', ' ')), '\\s+', ' ')), '') AS vendor_sku_key,
        NULLIF(TRIM(REGEXP_REPLACE(UPPER(REGEXP_REPLACE(TRIM(SKU_MATCH_KEY), '[^A-Za-z0-9]+', ' ')), '\\s+', ' ')), '') AS sku_match_key,
        IFF(NULLIF(TRIM(MAPPING_NOTES), '') IS NULL, 0, 1) AS has_mapping_notes
    FROM THIRD_PARTY_RECON_SKU_MAP_PROD
    WHERE NULLIF(TRIM(VENDOR), '') IS NOT NULL
      AND NULLIF(TRIM(SKU_MATCH_KEY), '') IS NOT NULL
),
sku_alias_raw AS (
    SELECT vendor_key, vendor_product_key AS sku_key, canonical_sku, 1 AS alias_priority, has_mapping_notes FROM sku_map_base
    UNION ALL
    SELECT vendor_key, vendor_sku_key AS sku_key, canonical_sku, 2 AS alias_priority, has_mapping_notes FROM sku_map_base
    UNION ALL
    SELECT vendor_key, sku_match_key AS sku_key, canonical_sku, 3 AS alias_priority, has_mapping_notes FROM sku_map_base
),
sku_alias_map AS (
    SELECT
        vendor_key,
        sku_key,
        canonical_sku
    FROM sku_alias_raw
    WHERE sku_key IS NOT NULL
      AND canonical_sku IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY vendor_key, sku_key
        ORDER BY alias_priority, has_mapping_notes, canonical_sku
    ) = 1
),
invoice_source AS (
    SELECT
        CASE
            -- Exium invoice files are posted one month after the usage period.
            -- Align invoice-side month to usage-side month for intra control only.
            WHEN UPPER(TRIM(VENDOR)) = 'EXIUM' THEN DATEADD('month', -1, BILLING_MONTH::DATE)
            ELSE BILLING_MONTH::DATE
        END AS billing_month,
        NULLIF(TRIM(VENDOR), '') AS vendor,
        UPPER(TRIM(VENDOR)) AS vendor_key,
        COALESCE(NULLIF(TRIM(VENDOR_PRODUCT_SKU), ''), '(MISSING SKU)') AS raw_sku,
        NULLIF(
            TRIM(
                REGEXP_REPLACE(
                    REGEXP_REPLACE(
                        UPPER(REGEXP_REPLACE(TRIM(VENDOR_PRODUCT_SKU), '[^A-Za-z0-9]+', ' ')),
                        '^OVERAGE\\s+',
                        ''
                    ),
                    '\\s+',
                    ' '
                )
            ),
            ''
        ) AS raw_sku_key,
        QUANTITY::FLOAT AS quantity,
        AMOUNT::FLOAT AS amount
    FROM THIRD_PARTY_RECON_VENDOR_INVOICES
    WHERE BILLING_MONTH IS NOT NULL
      AND NULLIF(TRIM(VENDOR), '') IS NOT NULL
),
invoice_lines AS (
    SELECT
        s.billing_month,
        s.vendor,
        COALESCE(m.canonical_sku, s.raw_sku_key, '(MISSING SKU)') AS sku,
        s.raw_sku AS vendor_invoice_sku,
        s.quantity,
        s.amount
    FROM invoice_source s
    LEFT JOIN sku_alias_map m
        ON s.vendor_key = m.vendor_key
       AND s.raw_sku_key = m.sku_key
),
invoice_rollup AS (
    SELECT
        vendor,
        billing_month,
        sku,
        LISTAGG(DISTINCT vendor_invoice_sku, ' | ')
            WITHIN GROUP (ORDER BY vendor_invoice_sku) AS vendor_invoice_sku,
        SUM(quantity) AS vendor_invoice_seats,
        SUM(amount) AS vendor_invoice_amount,
        COUNT(*) AS vendor_invoice_line_count
    FROM invoice_lines
    GROUP BY 1, 2, 3
),
usage_source AS (
    SELECT
        BILLING_MONTH::DATE AS billing_month,
        NULLIF(TRIM(VENDOR), '') AS vendor,
        UPPER(TRIM(VENDOR)) AS vendor_key,
        COALESCE(NULLIF(TRIM(VENDOR_PRODUCT_SKU), ''), '(MISSING SKU)') AS raw_sku,
        NULLIF(
            TRIM(
                REGEXP_REPLACE(
                    REGEXP_REPLACE(
                        UPPER(REGEXP_REPLACE(TRIM(VENDOR_PRODUCT_SKU), '[^A-Za-z0-9]+', ' ')),
                        '^OVERAGE\\s+',
                        ''
                    ),
                    '\\s+',
                    ' '
                )
            ),
            ''
        ) AS raw_sku_key,
        QUANTITY::FLOAT AS quantity,
        AMOUNT::FLOAT AS amount
    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
    WHERE BILLING_MONTH IS NOT NULL
      AND NULLIF(TRIM(VENDOR), '') IS NOT NULL
),
usage_lines AS (
    SELECT
        s.billing_month,
        s.vendor,
        COALESCE(m.canonical_sku, s.raw_sku_key, '(MISSING SKU)') AS sku,
        s.raw_sku AS vendor_usage_sku,
        s.quantity,
        s.amount
    FROM usage_source s
    LEFT JOIN sku_alias_map m
        ON s.vendor_key = m.vendor_key
       AND s.raw_sku_key = m.sku_key
),
usage_rollup AS (
    SELECT
        vendor,
        billing_month,
        sku,
        LISTAGG(DISTINCT vendor_usage_sku, ' | ')
            WITHIN GROUP (ORDER BY vendor_usage_sku) AS vendor_usage_sku,
        SUM(quantity) AS vendor_raw_usage_seats,
        SUM(amount) AS vendor_raw_usage_amount,
        COUNT(*) AS vendor_raw_usage_line_count
    FROM usage_lines
    GROUP BY 1, 2, 3
),
joined AS (
    SELECT
        COALESCE(u.vendor, i.vendor) AS vendor,
        COALESCE(u.billing_month, i.billing_month) AS billing_month,
        COALESCE(u.sku, i.sku) AS sku,
        i.vendor_invoice_sku,
        u.vendor_usage_sku,
        i.vendor_invoice_seats,
        u.vendor_raw_usage_seats,
        i.vendor_invoice_amount,
        COALESCE(
            u.vendor_raw_usage_amount,
            IFF(
                u.vendor_raw_usage_seats IS NOT NULL
                AND i.vendor_invoice_seats IS NOT NULL
                AND i.vendor_invoice_seats <> 0
                AND i.vendor_invoice_amount IS NOT NULL,
                u.vendor_raw_usage_seats * (i.vendor_invoice_amount / i.vendor_invoice_seats),
                NULL
            )
        ) AS vendor_raw_usage_amount,
        COALESCE(u.vendor_raw_usage_seats, 0) - COALESCE(i.vendor_invoice_seats, 0) AS delta_seats,
        COALESCE(
            COALESCE(
                u.vendor_raw_usage_amount,
                IFF(
                    u.vendor_raw_usage_seats IS NOT NULL
                    AND i.vendor_invoice_seats IS NOT NULL
                    AND i.vendor_invoice_seats <> 0
                    AND i.vendor_invoice_amount IS NOT NULL,
                    u.vendor_raw_usage_seats * (i.vendor_invoice_amount / i.vendor_invoice_seats),
                    NULL
                )
            ),
            0
        ) - COALESCE(i.vendor_invoice_amount, 0) AS delta_amount,
        i.vendor_invoice_line_count,
        u.vendor_raw_usage_line_count
    FROM usage_rollup u
    FULL OUTER JOIN invoice_rollup i
        ON u.vendor = i.vendor
       AND u.billing_month = i.billing_month
       AND u.sku = i.sku
)
SELECT
    vendor,
    billing_month,
    sku,
    vendor_invoice_sku,
    vendor_usage_sku,
    vendor_invoice_seats,
    vendor_raw_usage_seats,
    vendor_invoice_amount,
    vendor_raw_usage_amount,
    delta_seats,
    delta_amount,
    vendor_invoice_line_count,
    vendor_raw_usage_line_count,
    CASE
        WHEN vendor_invoice_line_count IS NULL THEN 'RAW_USAGE_ONLY'
        WHEN vendor_raw_usage_line_count IS NULL THEN 'INVOICE_ONLY'
        WHEN ABS(COALESCE(delta_seats, 0)) < 0.0001
         AND ABS(COALESCE(delta_amount, 0)) < 0.01 THEN 'MATCH'
        ELSE 'VARIANCE'
    END AS source_status,
    CURRENT_TIMESTAMP() AS built_at
FROM joined
WHERE billing_month >= '2026-01-01'::DATE
ORDER BY vendor, billing_month, sku;

COMMENT ON TABLE THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD IS
    'Vendor invoice vs raw vendor usage reconciliation at vendor/month/vendor-SKU grain. Delta is raw usage minus parsed invoice.';
