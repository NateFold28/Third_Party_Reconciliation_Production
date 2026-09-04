-- =============================================================================
-- THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
--
-- Vendor-owned reconciliation control:
--   Compare parsed vendor invoice lines to raw vendor usage files at the
--   narrowest evidenced shared grain before CW billing is introduced.
--
-- Delta convention:
--   DELTA_* = raw vendor usage - parsed vendor invoice
--
-- Null-side convention:
--   If Engineering has not refreshed invoice JSONs for a vendor/month/SKU, the
--   invoice-side fields stay NULL while the raw usage side remains visible.

-- Comparison strategy:
--   * Auvik compares by normalized partner and SKU; invoice IDs are retained
--     only when that partner belongs to one document.
--   * KeepIT compares within MAIN/TAKEOUT invoice-type lanes.
--   * A lane with one invoice compares directly to that invoice.
--   * Other multi-invoice lanes compare at combined month/SKU grain because
--     raw usage has no defensible invoice attribution key.
--   Usage that has no matching invoice row at the supported grain is shown
--   once as UNALLOCATED_USAGE_POOL. No proportional allocation is performed.
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
                CASE
                        WHEN UPPER(TRIM(VENDOR)) = 'KEEPIT'
                         AND (
                                        UPPER(COALESCE(INVOICE_DESCRIPTION, '')) LIKE '%TAKEOUT%'
                                 OR UPPER(COALESCE(DESCRIPTION, '')) LIKE '%TAKEOUT%'
                                 OR UPPER(COALESCE(FILE_PATH, '')) LIKE '%TAKEOUT%'
                         ) THEN 'TAKEOUT'
                        WHEN UPPER(TRIM(VENDOR)) = 'KEEPIT'
                         AND (
                                        UPPER(COALESCE(INVOICE_DESCRIPTION, '')) LIKE '%MAIN%'
                                 OR UPPER(COALESCE(DESCRIPTION, '')) LIKE '%MAIN%'
                                 OR UPPER(COALESCE(FILE_PATH, '')) LIKE '%MAIN%'
                         ) THEN 'MAIN'
                        WHEN UPPER(TRIM(VENDOR)) = 'KEEPIT' THEN 'OTHER'
                        ELSE 'MAIN'
                END AS inv_type,
                COALESCE(NULLIF(TRIM(INVOICE_ID), ''), 'UNIDENTIFIED_INVOICE') AS invoice_id,
                NULLIF(TRIM(INVOICE_DESCRIPTION), '') AS invoice_description,
                NULLIF(TRIM(NETSUITE_URL), '') AS netsuite_url,
                NULLIF(TRIM(PARTNER), '') AS comparison_partner,
                NULLIF(UPPER(REGEXP_REPLACE(TRIM(PARTNER), '[^A-Za-z0-9]', '')), '') AS partner_key,
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
        s.inv_type,
        s.invoice_id,
        s.invoice_description,
        s.netsuite_url,
        s.comparison_partner,
        s.partner_key,
        COALESCE(m.canonical_sku, s.raw_sku_key, '(MISSING SKU)') AS sku,
        s.raw_sku AS vendor_invoice_sku,
        s.quantity,
        s.amount
    FROM invoice_source s
    LEFT JOIN sku_alias_map m
        ON s.vendor_key = m.vendor_key
       AND s.raw_sku_key = m.sku_key
),
invoice_lane_registry AS (
    SELECT
        vendor,
        billing_month,
        inv_type,
        COUNT(DISTINCT invoice_id) AS invoice_count,
        MIN(invoice_id) AS sole_invoice_id
    FROM invoice_lines
    GROUP BY 1, 2, 3
),
invoice_prepared AS (
    SELECT
        i.*,
        CASE
            WHEN UPPER(i.vendor) = 'AUVIK' THEN 'PARTNER_SKU'
            WHEN UPPER(i.vendor) = 'KEEPIT' THEN 'INVOICE_TYPE_SKU'
            WHEN r.invoice_count = 1 THEN 'INVOICE_SKU'
            ELSE 'MONTH_SKU'
        END AS comparison_grain,
        CASE
            WHEN UPPER(i.vendor) = 'AUVIK' THEN COALESCE(i.partner_key, '(MISSING PARTNER)')
            WHEN UPPER(i.vendor) = 'KEEPIT' THEN i.inv_type
            WHEN r.invoice_count = 1 THEN r.sole_invoice_id
            ELSE 'MONTH_SKU_SUMMARY'
        END AS comparison_key
    FROM invoice_lines i
    INNER JOIN invoice_lane_registry r
        ON i.vendor = r.vendor
       AND i.billing_month = r.billing_month
       AND i.inv_type = r.inv_type
),
invoice_rollup AS (
    SELECT
        vendor,
        billing_month,
        inv_type,
        comparison_grain,
        comparison_key,
        IFF(comparison_grain = 'PARTNER_SKU', MAX(comparison_partner), NULL) AS comparison_partner,
        LISTAGG(DISTINCT invoice_id, ' | ')
            WITHIN GROUP (ORDER BY invoice_id) AS invoice_id,
        LISTAGG(
            DISTINCT invoice_id || '~~' || COALESCE(netsuite_url, ''),
            ' | '
        ) WITHIN GROUP (ORDER BY invoice_id || '~~' || COALESCE(netsuite_url, ''))
            AS invoice_link_keys,
        CASE
            WHEN comparison_grain IN ('MONTH_SKU', 'PARTNER_SKU') THEN
                COUNT(DISTINCT invoice_id) || ' invoice(s): ' ||
                LISTAGG(DISTINCT invoice_id, ' | ') WITHIN GROUP (ORDER BY invoice_id)
            ELSE MAX(invoice_description)
        END AS invoice_description,
        sku,
        LISTAGG(DISTINCT vendor_invoice_sku, ' | ')
            WITHIN GROUP (ORDER BY vendor_invoice_sku) AS vendor_invoice_sku,
        SUM(quantity) AS vendor_invoice_seats,
        SUM(amount) AS vendor_invoice_amount,
        COUNT(*) AS vendor_invoice_line_count
    FROM invoice_prepared
    GROUP BY 1, 2, 3, 4, 5, 10
),
usage_source AS (
    SELECT
        BILLING_MONTH::DATE AS billing_month,
        NULLIF(TRIM(VENDOR), '') AS vendor,
        UPPER(TRIM(VENDOR)) AS vendor_key,
        CASE
            WHEN UPPER(TRIM(VENDOR)) = 'KEEPIT'
             AND UPPER(COALESCE(MODIFIER, '')) IN ('TAKEOUT', 'PROMO') THEN 'TAKEOUT'
            ELSE 'MAIN'
        END AS inv_type,
        NULLIF(TRIM(VENDOR_PARTNER_NAME), '') AS comparison_partner,
        NULLIF(UPPER(REGEXP_REPLACE(TRIM(VENDOR_PARTNER_NAME), '[^A-Za-z0-9]', '')), '') AS partner_key,
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
        s.inv_type,
        CASE
            WHEN UPPER(s.vendor) = 'AUVIK' THEN 'PARTNER_SKU'
            WHEN UPPER(s.vendor) = 'KEEPIT' THEN 'INVOICE_TYPE_SKU'
            WHEN r.invoice_count = 1 THEN 'INVOICE_SKU'
            ELSE 'MONTH_SKU'
        END AS comparison_grain,
        CASE
            WHEN UPPER(s.vendor) = 'AUVIK' THEN COALESCE(s.partner_key, '(MISSING PARTNER)')
            WHEN UPPER(s.vendor) = 'KEEPIT' THEN s.inv_type
            WHEN r.invoice_count = 1 THEN r.sole_invoice_id
            ELSE 'MONTH_SKU_SUMMARY'
        END AS comparison_key,
        IFF(UPPER(s.vendor) = 'AUVIK', s.comparison_partner, NULL) AS comparison_partner,
        COALESCE(m.canonical_sku, s.raw_sku_key, '(MISSING SKU)') AS sku,
        s.raw_sku AS vendor_usage_sku,
        s.quantity,
        s.amount
    FROM usage_source s
    LEFT JOIN sku_alias_map m
        ON s.vendor_key = m.vendor_key
       AND s.raw_sku_key = m.sku_key
    LEFT JOIN invoice_lane_registry r
        ON s.vendor = r.vendor
       AND s.billing_month = r.billing_month
       AND s.inv_type = r.inv_type
),
usage_rollup AS (
    SELECT
        vendor,
        billing_month,
        inv_type,
        comparison_grain,
        comparison_key,
        MAX(comparison_partner) AS comparison_partner,
        sku,
        LISTAGG(DISTINCT vendor_usage_sku, ' | ')
            WITHIN GROUP (ORDER BY vendor_usage_sku) AS vendor_usage_sku,
        SUM(quantity) AS vendor_raw_usage_seats,
        SUM(amount) AS vendor_raw_usage_amount,
        COUNT(*) AS vendor_raw_usage_line_count
    FROM usage_lines
    GROUP BY 1, 2, 3, 4, 5, 7
),
joined AS (
    SELECT
        COALESCE(u.vendor, i.vendor) AS vendor,
        COALESCE(u.billing_month, i.billing_month) AS billing_month,
        COALESCE(u.inv_type, i.inv_type, 'UNCLASSIFIED') AS inv_type,
        COALESCE(u.comparison_grain, i.comparison_grain) AS comparison_grain,
        COALESCE(u.comparison_partner, i.comparison_partner) AS comparison_partner,
        IFF(i.vendor_invoice_line_count IS NULL, 'UNALLOCATED_USAGE_POOL', i.invoice_id) AS invoice_id,
        i.invoice_link_keys,
        IFF(
            i.vendor_invoice_line_count IS NULL,
            'Usage without a matching invoice row at the supported comparison grain',
            i.invoice_description
        ) AS invoice_description,
        COALESCE(u.sku, i.sku) AS sku,
        i.vendor_invoice_sku,
        u.vendor_usage_sku,
        i.vendor_invoice_seats,
        u.vendor_raw_usage_seats,
        i.vendor_invoice_amount,
        u.vendor_raw_usage_amount,
        COALESCE(u.vendor_raw_usage_seats, 0) - COALESCE(i.vendor_invoice_seats, 0) AS delta_seats,
        COALESCE(u.vendor_raw_usage_amount, 0) - COALESCE(i.vendor_invoice_amount, 0) AS delta_amount,
        i.vendor_invoice_line_count,
        u.vendor_raw_usage_line_count
    FROM usage_rollup u
    FULL OUTER JOIN invoice_rollup i
        ON u.vendor = i.vendor
       AND u.billing_month = i.billing_month
       AND u.inv_type = i.inv_type
       AND u.comparison_grain = i.comparison_grain
       AND u.comparison_key = i.comparison_key
       AND u.sku = i.sku
)
SELECT
    vendor,
    billing_month,
    inv_type,
    comparison_grain,
    comparison_partner,
    invoice_id,
    invoice_link_keys,
    invoice_description,
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
        WHEN invoice_id = 'UNALLOCATED_USAGE_POOL' THEN 'UNALLOCATED_USAGE_POOL'
        WHEN vendor_raw_usage_line_count IS NULL THEN 'INVOICE_ONLY'
        WHEN ABS(COALESCE(delta_seats, 0)) < 0.0001
         AND ABS(COALESCE(delta_amount, 0)) < 0.01 THEN 'MATCH'
        ELSE 'VARIANCE'
    END AS source_status,
    CURRENT_TIMESTAMP() AS built_at
FROM joined
WHERE billing_month >= '2026-01-01'::DATE
    AND (
                ABS(COALESCE(vendor_invoice_seats, 0)) >= 0.0001
         OR ABS(COALESCE(vendor_raw_usage_seats, 0)) >= 0.0001
         OR ABS(COALESCE(vendor_invoice_amount, 0)) >= 0.01
         OR ABS(COALESCE(vendor_raw_usage_amount, 0)) >= 0.01
    )
ORDER BY vendor, billing_month, inv_type, invoice_id, sku;

COMMENT ON TABLE THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD IS
    'Vendor invoice vs raw usage reconciliation at an evidenced vendor-aware grain: Auvik partner/SKU, KeepIT invoice-type/SKU, direct invoice/SKU for one-invoice lanes, and combined month/SKU otherwise. Unmatched usage is shown once; delta is raw usage minus parsed invoice.';
