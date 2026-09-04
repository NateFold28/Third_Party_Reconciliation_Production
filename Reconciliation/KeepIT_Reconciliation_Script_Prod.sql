-- KeepIT final reconciliation.
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
-- KeepIT partner mapping uses the billing-month-aware unified map so merged
-- accounts retain the historical SF ID before the effective merge month.
partner_bridge AS (
    SELECT
        billing_month,
        vendor_partner_name,
        vendor_partner_name_normalized,
        cms_id,
        sf_id,
        sf_account_name,
        partner_review_flag,
        partner_mapping_source
    FROM (
        SELECT
            billing_month,
            partner_name AS vendor_partner_name,
            TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS vendor_partner_name_normalized,
            cms_id,
            sf_id,
            COALESCE(zuora_name, partner_name) AS sf_account_name,
            'OK' AS partner_review_flag,
            'RECON_PARTNER_MAP_MONTHLY' AS partner_mapping_source
        FROM RECON_PARTNER_MAP_MONTHLY
        WHERE sf_id ILIKE 'ACT-%'
          AND partner_name IS NOT NULL
    )
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY billing_month, vendor_partner_name_normalized
        ORDER BY CASE WHEN sf_id ILIKE 'ACT-%' THEN 0 ELSE 1 END,
                 CASE WHEN cms_id IS NULL THEN 1 ELSE 0 END,
                 sf_id NULLS LAST
    ) = 1
)
SELECT
    u.*,
    u.VENDOR_PRODUCT_SKU AS VENDOR_SKU_OR_PRODUCT,
    CASE
        -- TAKEOUT and PROMO workbooks are separate vendor source cohorts,
        -- but CW bills both through the Zuora 3-year Promo bundle lane.
        -- MODIFIER retains source lineage in VENDOR_USAGE_PROD.
        WHEN UPPER(COALESCE(u.MODIFIER, '')) IN ('PROMO', 'TAKEOUT', 'TAKEOUT-3Y-PROMO') THEN 'PROMO'
        ELSE 'MAIN'
    END AS SOURCE_FAMILY,
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
FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD u
LEFT JOIN vendor_sku_map vsm
    ON UPPER(TRIM(vsm.vendor_product)) = UPPER(TRIM(u.VENDOR_PRODUCT_SKU))
LEFT JOIN partner_bridge pb
    ON pb.vendor_partner_name_normalized = TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(u.VENDOR_PARTNER_NAME), '[^a-z0-9]+', ' '), '\\s+', ' '))
   AND pb.billing_month = u.BILLING_MONTH::DATE
WHERE u.VENDOR = 'KeepIT';

CREATE OR REPLACE TABLE KEEPIT_RECON_DETAIL AS
WITH vendor_base AS (
    SELECT
        m.*,
        m.SOURCE_FAMILY AS RECON_SOURCE_FAMILY
    FROM KEEPIT_VENDOR_USAGE_MASTER m
),
vendor_agg AS (
    SELECT
        COALESCE(
            sf_id,
            RESOLVED_CMS_ID,
            'UNMAPPED:' || UPPER(TRIM(COALESCE(VENDOR_PARTNER_NAME, 'UNKNOWN')))
        ) AS vendor_partner_grain_key,
        sf_id,
        IFF(
            COUNT(DISTINCT RESOLVED_CMS_ID) = 1,
            MIN(RESOLVED_CMS_ID),
            NULL
        ) AS cms_id,
        BILLING_MONTH::DATE AS billing_month,
        IFF(
            COUNT(DISTINCT RECON_SOURCE_FAMILY) = 1,
            MIN(RECON_SOURCE_FAMILY),
            'MIXED'
        ) AS source_family,
        SKU_MATCH_GROUP AS sku_match_group,
        COUNT_IF(LOWER(COALESCE(VENDOR_PARTNER_NAME, '')) LIKE '%connectwise%continuum%consolidat%') > 0 AS is_aggregate_vendor_invoice,
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
    GROUP BY 1, 2, 4, 6
),
aggregate_vendor_coverage AS (
    SELECT DISTINCT
        billing_month,
        sku_match_group
    FROM vendor_agg
    WHERE is_aggregate_vendor_invoice
),
vendor_weights AS (
    SELECT
        sf_id,
        billing_month,
        sku_match_group,
        SUM(COALESCE(vendor_quantity, 0)) AS vendor_group_quantity,
        COUNT(*) AS vendor_row_count
    FROM vendor_agg
    GROUP BY 1, 2, 3
),
keepit_sku_map AS (
    SELECT DISTINCT
        UPPER(TRIM(sku_match_key)) AS sku_match_group,
        UPPER(TRIM(cw_sku)) AS cw_sku
    FROM RECON_SKU_MAP
    WHERE vendor = 'KeepIT'
      AND sku_match_key IS NOT NULL
      AND cw_sku IS NOT NULL
    AND sku_match_key NOT ILIKE 'KEEPIT_CW_ONLY_%'
),
keepit_sku_map_tokens AS (
    SELECT DISTINCT
        sm.sku_match_group,
        sm.cw_sku,
        UPPER(TRIM(tok.value)) AS cw_sku_token
    FROM keepit_sku_map sm,
         LATERAL SPLIT_TO_TABLE(REPLACE(sm.cw_sku, '/', '|'), '|') tok
    WHERE TRIM(tok.value) <> ''
),
keepit_zuora_mapped AS (
    SELECT
        z.sf_id,
        z.billing_month::DATE AS billing_month,
                CASE
                        WHEN UPPER(TRIM(z.product_sku)) ILIKE '%PROMO%'
                            OR UPPER(COALESCE(z.charge_name, '')) ILIKE '%PROMO%'
                            OR UPPER(COALESCE(z.product_name, '')) ILIKE '%PROMO%' THEN 'PROMO'
                        WHEN UPPER(TRIM(z.product_sku)) ILIKE '%TAKEOUT%'
                            OR UPPER(COALESCE(z.charge_name, '')) ILIKE '%TAKEOUT%'
                            OR UPPER(COALESCE(z.product_name, '')) ILIKE '%TAKEOUT%' THEN 'TAKEOUT'
                        WHEN COALESCE(sm.sku_match_group, UPPER(TRIM(z.product_sku))) ILIKE 'KEEPIT_PROMO_%' THEN 'PROMO'
                        WHEN COALESCE(sm.sku_match_group, UPPER(TRIM(z.product_sku))) ILIKE 'KEEPIT_TAKEOUT_%' THEN 'TAKEOUT'
                        ELSE 'MAIN'
                END AS source_family,
        COALESCE(
            CASE
                -- Promo billing uses one bundle SKU for several workloads;
                -- charge name is the authoritative workload discriminator.
                WHEN UPPER(COALESCE(z.charge_name, '')) ILIKE '%DYNAMICS%' THEN 'KI-D365-FUL'
                WHEN UPPER(COALESCE(z.charge_name, '')) ILIKE ANY ('%MS 365%', '%MICROSOFT 365%', '%OFFICE 365%') THEN 'KI-M365-FUL'
                WHEN UPPER(COALESCE(z.charge_name, '')) ILIKE '%GOOGLE%' THEN 'KI-GOOG-FUL'
                WHEN UPPER(COALESCE(z.charge_name, '')) ILIKE ANY ('%AZURE%', '%ENTRA%') THEN 'KI-AZUR-CSP'
                WHEN UPPER(COALESCE(z.charge_name, '')) ILIKE '%SALESFORCE%' THEN 'KI-SFDC-FUL'
                ELSE NULL
            END,
            sm.sku_match_group,
            UPPER(TRIM(z.product_sku))
        ) AS sku_match_group,
        UPPER(TRIM(z.product_sku)) AS product_sku,
        z.charge_name AS charge_name,
        COALESCE(z.qty, 0) AS qty,
        COALESCE(z.charge_amount_usd, 0) AS amount
    FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD z
    LEFT JOIN keepit_sku_map_tokens sm
      ON sm.cw_sku_token = UPPER(TRIM(z.product_sku))
    WHERE z.vendor = 'KeepIT'
      AND z.sf_id ILIKE 'ACT-%'
      AND COALESCE(z.qty, 0) <> 0
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY z.sf_id, z.billing_month::DATE, z.invoice_number, z.invoice_id, z.product_sku, z.charge_name, z.qty, z.charge_amount_usd
        ORDER BY IFF(sm.cw_sku = UPPER(TRIM(z.product_sku)), 1, 0) DESC,
                 IFF(sm.sku_match_group ILIKE 'KEEPIT_CW_ONLY_%', 1, 0),
                 LENGTH(COALESCE(sm.cw_sku, UPPER(TRIM(z.product_sku)))) ASC,
                 sm.sku_match_group
    ) = 1
),
keepit_zuora_rows AS (
    SELECT * FROM keepit_zuora_mapped
),
vendor_account_weights AS (
    SELECT
        sf_id,
        billing_month,
        SUM(vendor_amount) AS account_vendor_amount
    FROM vendor_agg
    WHERE sf_id IS NOT NULL
      AND sku_match_group <> 'Missing Vendor Usage by SKU'
      AND vendor_amount > 0
    GROUP BY 1,2
),
keepit_zuora_ancillary_agg AS (
    SELECT
        z.sf_id,
        z.billing_month,
        ARRAY_AGG(DISTINCT z.product_sku) WITHIN GROUP (ORDER BY z.product_sku) AS ancillary_zuora_skus,
        LISTAGG(DISTINCT z.charge_name, ' | ') WITHIN GROUP (ORDER BY z.charge_name) AS ancillary_zuora_charge_names,
        SUM(z.qty) AS ancillary_zuora_quantity,
        SUM(z.amount) AS ancillary_zuora_amount
    FROM keepit_zuora_rows z
    JOIN vendor_account_weights aw
      ON aw.sf_id = z.sf_id
     AND aw.billing_month = z.billing_month
    WHERE z.product_sku IN (
        'M2M-RMM-SB-UNLTD',
        'CW-3YPROMO-RETENTION',
        'CMS-3P-UMM-BCDR-SAAS-UNLIMTRT',
        'CW-RMM-SB-UNLTD',
        'CU-OTHERCORP900902RS'
    )
    GROUP BY 1,2
),
keepit_zuora_recon_rows AS (
    -- Allocatable ancillary charges contribute real dollars, but their seat
    -- quantities are retained separately and never added to workload usage.
    -- Ancillary charges with no eligible vendor workload remain billing-only.
    SELECT z.*
    FROM keepit_zuora_rows z
    LEFT JOIN vendor_account_weights aw
      ON aw.sf_id = z.sf_id
     AND aw.billing_month = z.billing_month
    WHERE z.product_sku NOT IN (
        'M2M-RMM-SB-UNLTD',
        'CW-3YPROMO-RETENTION',
        'CMS-3P-UMM-BCDR-SAAS-UNLIMTRT',
        'CW-RMM-SB-UNLTD',
        'CU-OTHERCORP900902RS'
    )
       OR aw.sf_id IS NULL
),
keepit_zuora_agg AS (
    SELECT
        sf_id,
        billing_month,
        IFF(COUNT(DISTINCT source_family) = 1, MIN(source_family), 'MIXED') AS source_family,
        sku_match_group,
        ARRAY_AGG(DISTINCT product_sku) WITHIN GROUP (ORDER BY product_sku) AS zuora_skus,
        LISTAGG(DISTINCT charge_name, ' | ') WITHIN GROUP (ORDER BY charge_name) AS zuora_charge_names,
        SUM(qty) AS zuora_quantity,
        IFF(SUM(qty)=0, NULL, SUM(amount)/NULLIF(SUM(qty),0)) AS zuora_unit_price,
        SUM(amount) AS zuora_amount,
        COUNT(*) AS zuora_row_count,
        0::NUMBER AS zuora_review_row_count
    FROM keepit_zuora_recon_rows
    GROUP BY 1,2,4
),
non_aggregate_vendor_coverage AS (
    SELECT DISTINCT
        sf_id,
        billing_month,
        sku_match_group
    FROM vendor_agg
    WHERE sf_id IS NOT NULL
      AND NOT is_aggregate_vendor_invoice
),
keepit_zuora_pool_agg AS (
    SELECT
        z.billing_month,
        z.sku_match_group,
        ARRAY_AGG(DISTINCT z.product_sku) WITHIN GROUP (ORDER BY z.product_sku) AS zuora_skus,
        LISTAGG(DISTINCT z.charge_name, ' | ') WITHIN GROUP (ORDER BY z.charge_name) AS zuora_charge_names,
        SUM(z.qty) AS zuora_quantity,
        IFF(SUM(z.qty)=0, NULL, SUM(z.amount)/NULLIF(SUM(z.qty),0)) AS zuora_unit_price,
        SUM(z.amount) AS zuora_amount,
        COUNT(*) AS zuora_row_count,
        0::NUMBER AS zuora_review_row_count
    FROM keepit_zuora_recon_rows z
    LEFT JOIN non_aggregate_vendor_coverage nav
      ON nav.sf_id = z.sf_id
     AND nav.billing_month = z.billing_month
     AND nav.sku_match_group = z.sku_match_group
    WHERE nav.sf_id IS NULL
    GROUP BY 1,2
),
keepit_carr_mapped AS (
    SELECT
        m.sf_id,
        m.billing_month::DATE AS billing_month,
        CASE
            WHEN UPPER(TRIM(m.product_sku)) ILIKE '%PROMO%' THEN 'PROMO'
            WHEN UPPER(TRIM(m.product_sku)) ILIKE '%TAKEOUT%' THEN 'TAKEOUT'
            WHEN COALESCE(sm.sku_match_group, UPPER(TRIM(m.product_sku))) ILIKE 'KEEPIT_PROMO_%' THEN 'PROMO'
            WHEN COALESCE(sm.sku_match_group, UPPER(TRIM(m.product_sku))) ILIKE 'KEEPIT_TAKEOUT_%' THEN 'TAKEOUT'
            ELSE 'MAIN'
        END AS source_family,
        COALESCE(sm.sku_match_group, UPPER(TRIM(m.product_sku))) AS sku_match_group,
        UPPER(TRIM(m.product_sku)) AS product_sku,
        COALESCE(m.qty, 0) AS qty,
        COALESCE(m.amount, 0) AS amount
    FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD m
    LEFT JOIN keepit_sku_map_tokens sm
      ON sm.cw_sku_token = UPPER(TRIM(m.product_sku))
    WHERE m.vendor = 'KeepIT'
      AND m.sf_id ILIKE 'ACT-%'
      AND COALESCE(m.qty, 0) <> 0
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY m.sf_id, m.billing_month::DATE, m.product_sku, m.qty, m.amount, m.transaction_source
        ORDER BY IFF(sm.cw_sku = UPPER(TRIM(m.product_sku)), 1, 0) DESC,
                 IFF(sm.sku_match_group ILIKE 'KEEPIT_CW_ONLY_%', 1, 0),
                 LENGTH(COALESCE(sm.cw_sku, UPPER(TRIM(m.product_sku)))) ASC,
                 sm.sku_match_group
    ) = 1
),
keepit_carr_rows AS (
    SELECT * FROM keepit_carr_mapped
),
keepit_carr_agg AS (
    SELECT
        sf_id,
        billing_month,
        IFF(COUNT(DISTINCT source_family) = 1, MIN(source_family), 'MIXED') AS source_family,
        sku_match_group,
        ARRAY_AGG(DISTINCT product_sku) WITHIN GROUP (ORDER BY product_sku) AS carr_skus,
        SUM(qty) AS carr_quantity,
        SUM(amount) AS carr_amount,
        COUNT(*) AS carr_row_count
    FROM keepit_carr_rows
    GROUP BY 1,2,4
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
        COALESCE(z.zuora_skus, zp.zuora_skus) AS zuora_skus,
        COALESCE(z.zuora_charge_names, zp.zuora_charge_names) AS zuora_charge_names,
        a.ancillary_zuora_skus,
        a.ancillary_zuora_charge_names,
        c.carr_skus,
        CASE
            WHEN (z.sf_id IS NOT NULL OR zp.billing_month IS NOT NULL OR a.sf_id IS NOT NULL)
                 AND c.sf_id IS NOT NULL THEN 'ZUORA_AND_MARKETPLACE'
            WHEN z.sf_id IS NOT NULL OR zp.billing_month IS NOT NULL OR a.sf_id IS NOT NULL THEN 'ZUORA_ONLY'
            WHEN c.sf_id IS NOT NULL THEN 'MARKETPLACE_ONLY'
            ELSE 'NO_BILLING_SOURCE'
        END AS billing_source_mix,
        v.vendor_quantity,
        v.vendor_amount,
        CASE
            WHEN COALESCE(z.zuora_quantity, zp.zuora_quantity, 0) > 0
                 AND COALESCE(w.vendor_group_quantity, 0) > 0
                THEN COALESCE(z.zuora_quantity, zp.zuora_quantity, 0)
                    * COALESCE(v.vendor_quantity, 0)
                    / NULLIF(w.vendor_group_quantity, 0)
            ELSE COALESCE(z.zuora_quantity, zp.zuora_quantity)
        END AS zuora_quantity,
        CASE
            WHEN v.sku_match_group <> 'Missing Vendor Usage by SKU'
                 AND v.vendor_amount > 0
                 AND COALESCE(aw.account_vendor_amount, 0) > 0
                THEN COALESCE(a.ancillary_zuora_quantity, 0)
                    * v.vendor_amount / NULLIF(aw.account_vendor_amount, 0)
            ELSE 0
        END AS ancillary_zuora_quantity,
        COALESCE(z.zuora_unit_price, zp.zuora_unit_price) AS zuora_unit_price,
        CASE
            WHEN COALESCE(z.zuora_amount, zp.zuora_amount, 0) <> 0
                 AND COALESCE(w.vendor_group_quantity, 0) > 0
                THEN COALESCE(z.zuora_amount, zp.zuora_amount, 0)
                    * COALESCE(v.vendor_quantity, 0)
                    / NULLIF(w.vendor_group_quantity, 0)
            ELSE COALESCE(z.zuora_amount, zp.zuora_amount)
        END AS zuora_amount,
        CASE
            WHEN v.sku_match_group <> 'Missing Vendor Usage by SKU'
                 AND v.vendor_amount > 0
                 AND COALESCE(aw.account_vendor_amount, 0) > 0
                THEN COALESCE(a.ancillary_zuora_amount, 0)
                    * v.vendor_amount / NULLIF(aw.account_vendor_amount, 0)
            ELSE 0
        END AS ancillary_zuora_amount,
        COALESCE(z.zuora_row_count, zp.zuora_row_count) AS zuora_row_count,
        COALESCE(z.zuora_review_row_count, zp.zuora_review_row_count) AS zuora_review_row_count,
        c.carr_quantity,
        c.carr_amount,
        c.carr_row_count,
        NULL::NUMBER AS support_quantity,
        NULL::NUMBER AS support_row_count,
        CASE
            WHEN COALESCE(z.zuora_quantity, zp.zuora_quantity, 0) > 0
                THEN CASE
                    WHEN COALESCE(w.vendor_group_quantity, 0) > 0
                        THEN COALESCE(z.zuora_quantity, zp.zuora_quantity, 0) * COALESCE(v.vendor_quantity, 0) / NULLIF(w.vendor_group_quantity, 0)
                    ELSE 0
                END
            ELSE COALESCE(c.carr_quantity, 0)
        END AS total_billing_quantity,
        CASE
            WHEN COALESCE(z.zuora_amount, zp.zuora_amount, 0) > 0
                THEN CASE
                    WHEN COALESCE(w.vendor_group_quantity, 0) > 0
                        THEN COALESCE(z.zuora_amount, zp.zuora_amount, 0) * COALESCE(v.vendor_quantity, 0) / NULLIF(w.vendor_group_quantity, 0)
                    ELSE 0
                END
            ELSE COALESCE(c.carr_amount, 0)
        END
        + CASE
            WHEN v.sku_match_group <> 'Missing Vendor Usage by SKU'
                 AND v.vendor_amount > 0
                 AND COALESCE(aw.account_vendor_amount, 0) > 0
                THEN COALESCE(a.ancillary_zuora_amount, 0)
                    * v.vendor_amount / NULLIF(aw.account_vendor_amount, 0)
            ELSE 0
        END AS total_billing_amount,
        v.vendor_source_row_count,
        v.vendor_partner_guid_count,
        v.vendor_unmapped_partner_rows
    FROM vendor_agg v
    LEFT JOIN vendor_weights w
        ON COALESCE(w.sf_id, '__NULL_SF_ID__') = COALESCE(v.sf_id, '__NULL_SF_ID__')
       AND w.billing_month = v.billing_month
       AND w.sku_match_group = v.sku_match_group
    LEFT JOIN vendor_account_weights aw
        ON aw.sf_id = v.sf_id
       AND aw.billing_month = v.billing_month
    LEFT JOIN keepit_zuora_ancillary_agg a
        ON a.sf_id = v.sf_id
       AND a.billing_month = v.billing_month
    LEFT JOIN keepit_zuora_agg z
        ON z.sf_id = v.sf_id
       AND z.billing_month = v.billing_month
       AND z.sku_match_group = v.sku_match_group
       AND NOT v.is_aggregate_vendor_invoice
    LEFT JOIN keepit_zuora_pool_agg zp
        ON zp.billing_month = v.billing_month
       AND zp.sku_match_group = v.sku_match_group
       AND v.is_aggregate_vendor_invoice
    LEFT JOIN keepit_carr_agg c
        ON c.sf_id = v.sf_id
       AND c.billing_month = v.billing_month
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
        NULL AS ancillary_zuora_skus,
        NULL::VARCHAR AS ancillary_zuora_charge_names,
        c.carr_skus,
        'ZUORA_ONLY' AS billing_source_mix,
        0::NUMBER AS vendor_quantity,
        0::NUMBER AS vendor_amount,
        z.zuora_quantity,
        0::NUMBER AS ancillary_zuora_quantity,
        z.zuora_unit_price,
        z.zuora_amount,
        0::NUMBER AS ancillary_zuora_amount,
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
    FROM keepit_zuora_agg z
    LEFT JOIN keepit_carr_agg c
        ON c.sf_id = z.sf_id
       AND c.billing_month = z.billing_month
       AND c.sku_match_group = z.sku_match_group
    LEFT JOIN vendor_weights w
        ON w.sf_id = z.sf_id
       AND w.billing_month = z.billing_month
       AND w.sku_match_group = z.sku_match_group
    LEFT JOIN aggregate_vendor_coverage avc
        ON avc.billing_month = z.billing_month
       AND avc.sku_match_group = z.sku_match_group
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
      AND avc.billing_month IS NULL
),
-- Reverse lookup: sf_id -> partner_name for billing-only rows
sf_id_to_partner AS (
    SELECT
        NULL::VARCHAR AS vendor_partner_guid,
        sf_id,
        ANY_VALUE(partner_name) AS vendor_partner_name
    FROM RECON_PARTNER_MAP
    WHERE sf_id IS NOT NULL
      AND sf_id ILIKE 'ACT-%'
      AND partner_name IS NOT NULL
    GROUP BY sf_id
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
        FALSE AS duplicate_billing_flag,
        CASE
            -- 1. Structural preconditions
            WHEN vendor_quantity > 0 AND sf_id IS NULL THEN 'PARTNER_MAPPING_REQUIRED'

            -- 2. One-sided rows with material exposure
            WHEN vendor_quantity > 0 AND total_billing_quantity = 0 THEN 'STRUCTURAL_VENDOR_ONLY'
            WHEN vendor_quantity = 0 AND vendor_amount <> 0 AND total_billing_amount = 0 THEN 'STRUCTURAL_VENDOR_ONLY'
            WHEN vendor_quantity = 0 AND total_billing_quantity > 0 THEN 'STRUCTURAL_BILLING_ONLY'

            -- 3. Two-sided CLEAR (within tolerance)
            WHEN ABS(total_billing_quantity - vendor_quantity) <= GREATEST(3, vendor_quantity * 0.05) THEN 'CLEAR'

            -- 4. Dollar noise gate
            WHEN GREATEST(COALESCE(vendor_amount, 0), COALESCE(total_billing_amount, 0)) <= 100 THEN 'NEGLIGIBLE_DOLLAR_EXPOSURE'

            -- 5. Vendor > billing (overage pattern)
            WHEN vendor_quantity > total_billing_quantity THEN
                CASE
                    WHEN (vendor_quantity - total_billing_quantity) <= GREATEST(10, vendor_quantity * 0.25) THEN 'OVERAGE_EXPECTED'
                    ELSE 'MATERIAL_UNDER_VENDOR'
                END

            -- 6. Billing > vendor
            WHEN total_billing_quantity > vendor_quantity THEN
                CASE
                    WHEN (total_billing_quantity - vendor_quantity) <= GREATEST(10, vendor_quantity * 0.25) THEN 'BILLING_DIFFERENTIAL_OVER'
                    ELSE 'MATERIAL_OVER_VENDOR'
                END

            -- 7. Fallback
            ELSE 'REVIEW_EXCEPTION'
        END AS outcome_flag
    FROM joined
    WHERE COALESCE(vendor_quantity, 0) > 0
       OR COALESCE(total_billing_quantity, 0) > 0
         OR COALESCE(vendor_amount, 0) <> 0
         OR COALESCE(total_billing_amount, 0) <> 0
),

keepit_api_sku_tokens AS (
    SELECT DISTINCT
        UPPER(TRIM(sku_match_group)) AS sku_match_group_key,
        UPPER(TRIM(cw_sku_token)) AS cw_sku_token
        FROM keepit_sku_map_tokens t
    WHERE sku_match_group IS NOT NULL
      AND cw_sku_token IS NOT NULL
      AND TRIM(cw_sku_token) <> ''
      AND sku_match_group NOT ILIKE 'KEEPIT_CW_ONLY_%'
            -- A shared bundle token has no workload identity in the API source.
            -- Charge name disambiguates it in Zuora, but the API table has no
            -- equivalent field, so assigning it to every workload multiplies usage.
            AND NOT EXISTS (
                    SELECT 1
                    FROM keepit_sku_map_tokens x
                    WHERE x.cw_sku_token = t.cw_sku_token
                    GROUP BY x.cw_sku_token
                    HAVING COUNT(DISTINCT x.sku_match_group) > 1
            )
),

keepit_api_partners AS (
    SELECT DISTINCT
        s.sf_id,
        s.billing_month,
        s.sku_match_group,
        UPPER(TRIM(s.sku_match_group)) AS sku_match_group_key,
        pm.cms_id
    FROM scored s
                JOIN RECON_PARTNER_MAP_MONTHLY_SF_UNIQUE pm
      ON pm.sf_id = s.sf_id
         AND pm.billing_month = s.billing_month
    WHERE s.sf_id IS NOT NULL
      AND s.sku_match_group IS NOT NULL
      AND s.sku_match_group NOT ILIKE 'KEEPIT_CW_ONLY_%'
      AND pm.cms_id IS NOT NULL
      AND TRIM(pm.cms_id) <> ''
),

keepit_api_daily AS (
    SELECT
        pa.sf_id,
        pa.billing_month,
        pa.sku_match_group,
        DATEADD('day', 20, pa.billing_month)::DATE AS snapshot_date,
        u.on_date::DATE AS on_date,
        SUM(COALESCE(u.agent_cnt, 0)) AS day_quantity
    FROM keepit_api_partners pa
    JOIN keepit_api_sku_tokens st
      ON st.sku_match_group_key = pa.sku_match_group_key
    JOIN ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
      ON u.partner_id::VARCHAR = pa.cms_id
     AND UPPER(TRIM(COALESCE(u.product_sku, ''))) = st.cw_sku_token
     AND UPPER(TRIM(COALESCE(u.product_sku, ''))) <> 'CW-3YPROMO-RETENTION'
     AND u.on_date::DATE > DATEADD('day', 20, DATEADD('month', -1, pa.billing_month))::DATE
     AND u.on_date::DATE <= DATEADD('day', 20, pa.billing_month)::DATE
    GROUP BY 1, 2, 3, 4, 5
),

keepit_api_rollup AS (
    SELECT
        sf_id,
        billing_month,
        sku_match_group,
        MAX(IFF(on_date = snapshot_date, day_quantity, NULL)) AS api_quantity,
        AVG(day_quantity) AS avg_api_quantity
    FROM keepit_api_daily
    GROUP BY 1, 2, 3
),

scored_with_api AS (
    SELECT
        s.*,
        a.api_quantity,
        a.avg_api_quantity
    FROM scored s
    LEFT JOIN keepit_api_rollup a
      ON a.sf_id = s.sf_id
     AND a.billing_month = s.billing_month
     AND a.sku_match_group = s.sku_match_group
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
    ancillary_zuora_skus,
    ancillary_zuora_charge_names,
    carr_skus,
    billing_source_mix,
    api_quantity,
    avg_api_quantity,
    vendor_quantity,
    vendor_unit_price,
    vendor_amount,
    zuora_quantity,
    ancillary_zuora_quantity,
    zuora_unit_price,
    zuora_amount,
    ancillary_zuora_amount,
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
        WHEN outcome_flag = 'NEGLIGIBLE_DOLLAR_EXPOSURE' THEN 'Variance exists but total dollar exposure is <= $100. No action required.'
        WHEN outcome_flag = 'OVERAGE_EXPECTED' THEN 'Vendor usage exceeds CW billing within expected overage band (<=25%). Typical growth pattern.'
        WHEN outcome_flag = 'MATERIAL_UNDER_VENDOR' THEN 'Vendor usage exceeds CW billing by >25%. Review for missing billing or overage capture.'
        WHEN outcome_flag = 'MATERIAL_OVER_VENDOR' THEN 'CW billing exceeds vendor usage by >25%. Review for stale subscription or over-billing.'
        WHEN outcome_flag = 'BILLING_DIFFERENTIAL_OVER' THEN 'CW billing exceeds vendor usage in the 5-25% band. Minor drift; validate seat count.'
        ELSE NULL
    END AS investigation_reason,
    CASE
        WHEN outcome_flag IN ('CLEAR', 'NEGLIGIBLE_DOLLAR_EXPOSURE', 'OVERAGE_EXPECTED') THEN FALSE
        ELSE TRUE
    END AS billing_action_required,
    NULL::NUMBER AS vendor_vs_contract_delta_per_seat,
    NULL::NUMBER AS vendor_vs_contract_pct,
    NULL::VARCHAR AS vendor_vs_contract_flag,
    NULL::NUMBER AS vendor_vs_contract_dollar_impact
FROM scored_with_api;

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
    0::NUMBER AS takeout_support_rows,
    0::NUMBER AS cw_included_zero_dollar_rows,
    0::NUMBER AS cw_only_addon_rows,
    COUNT_IF(outcome_flag = 'NEGLIGIBLE_DOLLAR_EXPOSURE') AS negligible_dollar_exposure_rows,
    COUNT_IF(outcome_flag = 'OVERAGE_EXPECTED') AS overage_expected_rows,
    COUNT_IF(outcome_flag = 'BILLING_DIFFERENTIAL_OVER') AS billing_differential_over_rows,
    ROUND(COUNT_IF(outcome_flag = 'CLEAR') * 100.0 /
        NULLIF(COUNT(*), 0), 1) AS actionable_clear_pct,
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



