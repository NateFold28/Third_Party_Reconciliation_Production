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
--   * Auvik compares by normalized partner and SKU. Some Auvik invoice rows
--     append a product/overage label to the partner field, so those rows are
--     bridged to an exact raw-usage partner prefix from the same month.
--   * Bitdefender royalties descriptions are collapsed to the same governed
--     product families used by the parsed Bitdefender invoice SKUs.
--   * KeepIT compares within MAIN/TAKEOUT invoice-type lanes.
--   * Webroot OpenText invoice SKUs are aligned to the GSM/DNS/SAT families
--     in Aggregator Order Details. The OpenText invoice account identifies
--     its CW or CMS stream so each invoice remains individually selectable.
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
auvik_usage_partners AS (
    SELECT DISTINCT
        BILLING_MONTH::DATE AS billing_month,
        NULLIF(TRIM(VENDOR_PARTNER_NAME), '') AS comparison_partner,
        NULLIF(UPPER(REGEXP_REPLACE(TRIM(VENDOR_PARTNER_NAME), '[^A-Za-z0-9]', '')), '') AS partner_key
    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
    WHERE UPPER(TRIM(VENDOR)) = 'AUVIK'
      AND BILLING_MONTH IS NOT NULL
      AND NULLIF(TRIM(VENDOR_PARTNER_NAME), '') IS NOT NULL
),
auvik_invoice_partners AS (
    SELECT DISTINCT
        BILLING_MONTH::DATE AS billing_month,
        NULLIF(UPPER(REGEXP_REPLACE(TRIM(PARTNER), '[^A-Za-z0-9]', '')), '') AS invoice_partner_key
    FROM THIRD_PARTY_RECON_VENDOR_INVOICES
    WHERE UPPER(TRIM(VENDOR)) = 'AUVIK'
      AND BILLING_MONTH IS NOT NULL
      AND NULLIF(TRIM(PARTNER), '') IS NOT NULL
),
auvik_partner_bridge AS (
    SELECT
        i.billing_month,
        i.invoice_partner_key,
        u.comparison_partner,
        u.partner_key
    FROM auvik_invoice_partners i
    INNER JOIN auvik_usage_partners u
        ON u.billing_month = i.billing_month
       AND STARTSWITH(i.invoice_partner_key, u.partner_key)
       AND REGEXP_LIKE(
            SUBSTR(i.invoice_partner_key, LENGTH(u.partner_key) + 1),
            '^(OVERAGE)?ANM(ESSENTIALS|PERFORMANCEADDONS?)EVERGREEN$'
       )
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY i.billing_month, i.invoice_partner_key
        ORDER BY LENGTH(u.partner_key) DESC, u.partner_key
    ) = 1
),
invoice_source AS (
    SELECT
        CASE
            -- Exium invoice files are posted one month after the usage period.
            -- Align invoice-side month to usage-side month for intra control only.
                WHEN UPPER(TRIM(v.VENDOR)) = 'EXIUM' THEN DATEADD('month', -1, v.BILLING_MONTH::DATE)
                ELSE v.BILLING_MONTH::DATE
        END AS billing_month,
            NULLIF(TRIM(v.VENDOR), '') AS vendor,
            UPPER(TRIM(v.VENDOR)) AS vendor_key,
                CASE
                    WHEN UPPER(TRIM(v.VENDOR)) = 'KEEPIT'
                         AND (
                            UPPER(COALESCE(v.INVOICE_DESCRIPTION, '')) LIKE '%TAKEOUT%'
                         OR UPPER(COALESCE(v.DESCRIPTION, '')) LIKE '%TAKEOUT%'
                         OR UPPER(COALESCE(v.FILE_PATH, '')) LIKE '%TAKEOUT%'
                         ) THEN 'TAKEOUT'
                    WHEN UPPER(TRIM(v.VENDOR)) = 'KEEPIT'
                         AND (
                            UPPER(COALESCE(v.INVOICE_DESCRIPTION, '')) LIKE '%MAIN%'
                         OR UPPER(COALESCE(v.DESCRIPTION, '')) LIKE '%MAIN%'
                         OR UPPER(COALESCE(v.FILE_PATH, '')) LIKE '%MAIN%'
                         ) THEN 'MAIN'
                    WHEN UPPER(TRIM(v.VENDOR)) = 'KEEPIT' THEN 'OTHER'
                        ELSE 'MAIN'
                END AS inv_type,
                COALESCE(NULLIF(TRIM(v.INVOICE_ID), ''), 'UNIDENTIFIED_INVOICE') AS invoice_id,
                NULLIF(TRIM(v.INVOICE_DESCRIPTION), '') AS invoice_description,
                NULLIF(TRIM(v.NETSUITE_URL), '') AS netsuite_url,
                NULLIF(UPPER(TRIM(v.SOURCE_STREAM)), '') AS source_stream,
                COALESCE(b.comparison_partner, NULLIF(TRIM(v.PARTNER), '')) AS comparison_partner,
                COALESCE(b.partner_key, NULLIF(UPPER(REGEXP_REPLACE(TRIM(v.PARTNER), '[^A-Za-z0-9]', '')), '')) AS partner_key,
            COALESCE(NULLIF(TRIM(v.VENDOR_PRODUCT_SKU), ''), '(MISSING SKU)') AS raw_sku,
        NULLIF(
            TRIM(
                REGEXP_REPLACE(
                    REGEXP_REPLACE(
                    UPPER(REGEXP_REPLACE(TRIM(v.VENDOR_PRODUCT_SKU), '[^A-Za-z0-9]+', ' ')),
                        '^OVERAGE\\s+',
                        ''
                    ),
                    '\\s+',
                    ' '
                )
            ),
            ''
        ) AS raw_sku_key,
                v.QUANTITY::FLOAT AS quantity,
                v.AMOUNT::FLOAT AS amount
        FROM THIRD_PARTY_RECON_VENDOR_INVOICES v
        LEFT JOIN auvik_partner_bridge b
                ON b.billing_month = v.BILLING_MONTH::DATE
             AND b.invoice_partner_key = NULLIF(UPPER(REGEXP_REPLACE(TRIM(v.PARTNER), '[^A-Za-z0-9]', '')), '')
             AND UPPER(TRIM(v.VENDOR)) = 'AUVIK'
        WHERE v.BILLING_MONTH IS NOT NULL
            AND NULLIF(TRIM(v.VENDOR), '') IS NOT NULL
),
invoice_lines AS (
    SELECT
        s.billing_month,
        s.vendor,
        s.inv_type,
        s.invoice_id,
        s.invoice_description,
        s.netsuite_url,
        s.source_stream,
        s.comparison_partner,
        s.partner_key,
        COALESCE(
            CASE
                -- July's Bitdefender invoice uses the legacy ME_Loy code for
                -- the same Email Security charge carried as BP_2765_EMS.
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku_key = 'BP 2765 ME LOY' THEN 'EMAIL'
                WHEN UPPER(s.vendor) = 'WEBROOT' AND s.raw_sku_key = '1000062533' THEN 'GSM'
                WHEN UPPER(s.vendor) = 'WEBROOT' AND s.raw_sku_key = '1000063236' THEN 'DNS'
                WHEN UPPER(s.vendor) = 'WEBROOT' AND s.raw_sku_key = '1000063234' THEN 'SAT'
            END,
            m.canonical_sku,
            s.raw_sku_key,
            '(MISSING SKU)'
        ) AS sku,
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
webroot_invoice_stream_registry AS (
    SELECT
        vendor,
        billing_month,
        inv_type,
        source_stream,
        COUNT(DISTINCT invoice_id) AS invoice_count,
        MIN(invoice_id) AS sole_invoice_id
    FROM invoice_lines
    WHERE UPPER(vendor) = 'WEBROOT'
      AND source_stream IN ('CW', 'CMS')
    GROUP BY 1, 2, 3, 4
),
invoice_prepared AS (
    SELECT
        i.*,
        CASE
            WHEN UPPER(i.vendor) = 'AUVIK' THEN 'PARTNER_SKU'
            WHEN UPPER(i.vendor) = 'KEEPIT' THEN 'INVOICE_TYPE_SKU'
            WHEN UPPER(i.vendor) = 'WEBROOT' THEN 'INVOICE_SKU'
            WHEN r.invoice_count = 1 THEN 'INVOICE_SKU'
            ELSE 'MONTH_SKU'
        END AS comparison_grain,
        CASE
            WHEN UPPER(i.vendor) = 'AUVIK' THEN COALESCE(i.partner_key, '(MISSING PARTNER)')
            WHEN UPPER(i.vendor) = 'KEEPIT' THEN i.inv_type
            WHEN UPPER(i.vendor) = 'WEBROOT' THEN i.invoice_id
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
        NULLIF(UPPER(TRIM(MODIFIER)), '') AS source_stream,
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
            WHEN UPPER(s.vendor) = 'WEBROOT' AND w.invoice_count = 1 THEN 'INVOICE_SKU'
            WHEN UPPER(s.vendor) = 'WEBROOT' THEN 'MONTH_SKU'
            WHEN r.invoice_count = 1 THEN 'INVOICE_SKU'
            ELSE 'MONTH_SKU'
        END AS comparison_grain,
        CASE
            WHEN UPPER(s.vendor) = 'AUVIK' THEN COALESCE(s.partner_key, '(MISSING PARTNER)')
            WHEN UPPER(s.vendor) = 'KEEPIT' THEN s.inv_type
            WHEN UPPER(s.vendor) = 'WEBROOT' AND w.invoice_count = 1 THEN w.sole_invoice_id
            WHEN UPPER(s.vendor) = 'WEBROOT' THEN 'WEBROOT_' || COALESCE(s.source_stream, 'UNKNOWN') || '_USAGE'
            WHEN r.invoice_count = 1 THEN r.sole_invoice_id
            ELSE 'MONTH_SKU_SUMMARY'
        END AS comparison_key,
        IFF(UPPER(s.vendor) = 'AUVIK', s.comparison_partner, NULL) AS comparison_partner,
        COALESCE(
            CASE
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE '%GravityZone Email Security%' THEN 'EMAIL'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE '%Email Security%' THEN 'EMAIL'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE '%ATS & EDR%' THEN 'ATS EDR'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE '%Advanced Threat Security%' THEN 'ATS'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE '%EDR (MSP Secure)%' THEN 'MSP SECURE'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE ANY ('%Secure Plus%', '%Secure Extra%') THEN 'MSP SECURE'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE ANY ('%Cloud Sec%GravityZone%', '%Cloud Security Gravity Zone%') THEN 'BD GRAVITYZONE'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE '%Security TSP CW Automate%' THEN 'BD GRAVITYZONE'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE '%Cloud Encryption%' THEN 'ENCRYPTION'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE '%Patch Management%' THEN 'PATCH'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE '%PHASR%' THEN 'PHASR'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE '%XDR%' THEN 'XDR'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE '%Mobile%' THEN 'MOBILE'
                WHEN UPPER(s.vendor) = 'BITDEFENDER' AND s.raw_sku ILIKE '%Security for Virtualized%' THEN 'BD VE VS'
            END,
            m.canonical_sku,
            s.raw_sku_key,
            '(MISSING SKU)'
        ) AS sku,
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
    LEFT JOIN webroot_invoice_stream_registry w
        ON s.vendor = w.vendor
       AND s.billing_month = w.billing_month
       AND s.inv_type = w.inv_type
       AND s.source_stream = w.source_stream
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
    'Vendor invoice vs raw usage reconciliation at an evidenced vendor-aware grain: Auvik partner/SKU, KeepIT invoice-type/SKU, Webroot OpenText invoices individually aligned to their CW/CMS Aggregator Order Details stream, direct invoice/SKU for one-invoice lanes, and combined month/SKU otherwise. Unmatched usage is shown once; delta is raw usage minus parsed invoice.';
