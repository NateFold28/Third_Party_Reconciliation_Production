-- =============================================================================
-- STEP 2: WEBROOT FINAL RECONCILIATION
-- =============================================================================
-- Billing truth: Zuora posted BillRun + Marketplace royalties.
-- External usage: invoice-validated Webroot Aggregator Order Details.
-- Internal validation: TRT endpoint/DNS/SAT usage snapshots. TRT supports
-- investigation, but does not overwrite billing quantity or amount.
--
-- Grain: billing_month + recon_stream (CW/CMS) + resolved sf_id/unmapped
-- partner key + sku_match_group. CW and CMS must stay separate because only
-- CMS has TRT usage validation; CW compares aggregator to Zuora/Marketplace.
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

CREATE OR REPLACE TABLE WEBROOT_RECON_DETAIL AS

WITH usage_product_map AS (
    SELECT column1::VARCHAR AS vendor_product, column2::VARCHAR AS sku_match_group
    FROM VALUES
        ('SAEP', 'GSM'),
        ('SDNS', 'DNS'),
        ('SECA', 'SAT')
),

partner_map AS (
    SELECT
        billing_month,
        UPPER(TRIM(partner_name)) AS partner_name_key,
        partner_name,
        sf_id,
        cms_id,
        zuora_name
    FROM RECON_PARTNER_MAP_MONTHLY
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY billing_month, UPPER(TRIM(partner_name))
        ORDER BY
            IFF(sf_id IS NOT NULL, 0, 1),
            IFF(cms_id IS NOT NULL, 0, 1),
            partner_name,
            sf_id,
            cms_id
    ) = 1
),

usage_base AS (
    SELECT
        u.billing_month,
        u.modifier AS stream,
        pm.sf_id,
        IFF(pm.sf_id IS NULL,
            'UNMAPPED:' || COALESCE(u.vendor_partner_name, ''),
            pm.sf_id
        ) AS recon_partner_key,
        NULL::VARCHAR AS vendor_partner_code,
        u.vendor_partner_name,
        u.vendor_product_sku AS vendor_product,
        u.vendor_product_sku AS sku_match_group,
        u.quantity,
        u.amount,
        COALESCE(u.amount, 0) <> 0 AS chargeable_flag,
        NULL::VARCHAR AS source_file
    FROM WEBROOT_USAGE u
    LEFT JOIN partner_map pm
        ON pm.billing_month = u.billing_month::DATE
       AND pm.partner_name_key = UPPER(TRIM(u.vendor_partner_name))
    WHERE COALESCE(u.quantity, 0) <> 0
       OR COALESCE(u.amount, 0) <> 0
),

usage_agg AS (
    SELECT
        billing_month,
        stream AS recon_stream,
        recon_partner_key,
        MAX(sf_id) AS sf_id,
        stream AS source_streams,
        NULL::VARCHAR AS source_channels,
        MAX(vendor_partner_name) AS vendor_partner_name,
        MAX(vendor_partner_code) AS vendor_partner_code,
        sku_match_group,
        LISTAGG(DISTINCT vendor_product, ' | ') WITHIN GROUP (ORDER BY vendor_product) AS vendor_product,
        SUM(quantity) AS vendor_quantity,
        SUM(amount) AS vendor_amount,
        COUNT(*) AS vendor_source_row_count,
        COUNT_IF(chargeable_flag) AS vendor_chargeable_row_count,
        ARRAY_AGG(DISTINCT source_file) WITHIN GROUP (ORDER BY source_file) AS vendor_source_files
    FROM usage_base
    GROUP BY billing_month, stream, recon_partner_key, sku_match_group
),

cw_sku_group_map AS (
    SELECT
        m.cw_sku,
        m.sku_match_key AS sku_match_group,
        CASE
            WHEN UPPER(pb.source) = 'CMS' THEN 'CMS'
            WHEN m.cw_sku ILIKE 'CMS-%'
              OR m.cw_sku ILIKE 'CU-%'
              OR m.cw_sku ILIKE '3P-SAAS%'
              OR m.cw_sku ILIKE '3RDPARTYSAASIIT%'
              OR m.cw_sku ILIKE 'CW-RMM-WR-EEP-OVERAG%'
                THEN 'CMS'
            ELSE 'CW'
        END AS billing_stream
    FROM (SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Webroot') m
    LEFT JOIN WEBROOT_CW_PRICEBOOK_SKU_RATES pb
        ON pb.product_code = m.cw_sku
    WHERE m.cw_sku IS NOT NULL
      AND m.sku_match_key IN ('GSM', 'DNS', 'SAT', 'BUNDLE')
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY m.cw_sku
        ORDER BY
            m.sku_match_key,
            billing_stream
    ) = 1
),

webroot_zuora_rows AS (
    SELECT
        z.sf_id,
        z.billing_month::DATE AS billing_month,
        UPPER(TRIM(z.product_sku)) AS product_sku,
        z.invoice_number AS zuora_invoice_numbers,
        z.invoice_id AS zuora_invoice_ids,
        z.charge_name AS zuora_charge_names,
        NULL::VARCHAR AS zuora_subscription_names,
        NULL::DATE AS first_invoice_date,
        NULL::DATE AS last_invoice_date,
        NULL::DATE AS first_service_start_date,
        NULL::DATE AS last_service_end_date,
        COALESCE(z.qty, 0) AS zuora_quantity,
        COALESCE(z.charge_amount_usd, 0) AS zuora_charge_amount,
        1::NUMBER AS billing_row_count
    FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD z
    WHERE z.vendor = 'Webroot'
      AND z.sf_id ILIKE 'ACT-%'
      AND COALESCE(z.qty, 0) <> 0
),

webroot_marketplace_rows AS (
    SELECT
        m.sf_id,
        m.billing_month::DATE AS billing_month,
        UPPER(TRIM(m.product_sku)) AS product_sku,
        COALESCE(m.qty, 0) AS marketplace_quantity,
        COALESCE(m.amount, 0) AS marketplace_amount,
        1::NUMBER AS marketplace_row_count,
        m.transaction_source AS marketplace_transaction_sources
    FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD m
    WHERE m.vendor = 'Webroot'
      AND m.sf_id ILIKE 'ACT-%'
      AND COALESCE(m.qty, 0) <> 0
),

zuora_billing AS (
    SELECT
        b.sf_id,
        b.billing_month,
        COALESCE(m.billing_stream, 'CW') AS recon_stream,
        COALESCE(m.sku_match_group, 'UNMAPPED_CW_SKU') AS sku_match_group,
        ARRAY_AGG(DISTINCT b.product_sku) WITHIN GROUP (ORDER BY b.product_sku) AS zuora_skus,
        LISTAGG(DISTINCT b.zuora_invoice_numbers, ' | ') WITHIN GROUP (ORDER BY b.zuora_invoice_numbers) AS zuora_invoice_numbers,
        LISTAGG(DISTINCT b.zuora_invoice_ids, ' | ') WITHIN GROUP (ORDER BY b.zuora_invoice_ids) AS zuora_invoice_ids,
        LISTAGG(DISTINCT b.zuora_charge_names, ' | ') WITHIN GROUP (ORDER BY b.zuora_charge_names) AS zuora_charge_names,
        LISTAGG(DISTINCT b.zuora_subscription_names, ' | ') WITHIN GROUP (ORDER BY b.zuora_subscription_names) AS zuora_subscription_names,
        MIN(b.first_invoice_date) AS first_invoice_date,
        MAX(b.last_invoice_date) AS last_invoice_date,
        MIN(b.first_service_start_date) AS first_service_start_date,
        MAX(b.last_service_end_date) AS last_service_end_date,
        SUM(b.zuora_quantity) AS zuora_quantity,
        SUM(b.zuora_charge_amount) AS zuora_amount,
        SUM(b.billing_row_count) AS zuora_row_count
    FROM webroot_zuora_rows b
    LEFT JOIN cw_sku_group_map m
        ON m.cw_sku = b.product_sku
    GROUP BY 1,2,3,4
),

marketplace_billing AS (
    SELECT
        b.sf_id,
        b.billing_month,
        COALESCE(m.billing_stream, 'CW') AS recon_stream,
        COALESCE(m.sku_match_group, 'UNMAPPED_CW_SKU') AS sku_match_group,
        ARRAY_AGG(DISTINCT b.product_sku) WITHIN GROUP (ORDER BY b.product_sku) AS marketplace_skus,
        SUM(b.marketplace_quantity) AS marketplace_quantity,
        SUM(b.marketplace_amount) AS marketplace_amount,
        SUM(b.marketplace_row_count) AS marketplace_row_count,
        LISTAGG(DISTINCT b.marketplace_transaction_sources, ' | ')
            WITHIN GROUP (ORDER BY b.marketplace_transaction_sources) AS marketplace_transaction_sources
    FROM webroot_marketplace_rows b
    LEFT JOIN cw_sku_group_map m
        ON m.cw_sku = b.product_sku
    GROUP BY 1,2,3,4
),

billing_agg AS (
    SELECT
        COALESCE(z.sf_id, m.sf_id) AS sf_id,
        COALESCE(z.billing_month, m.billing_month) AS billing_month,
        COALESCE(z.recon_stream, m.recon_stream) AS recon_stream,
        COALESCE(z.sku_match_group, m.sku_match_group) AS sku_match_group,
        z.zuora_skus,
        z.zuora_invoice_numbers,
        z.zuora_invoice_ids,
        z.zuora_charge_names,
        z.zuora_subscription_names,
        z.first_invoice_date,
        z.last_invoice_date,
        z.first_service_start_date,
        z.last_service_end_date,
        m.marketplace_skus,
        COALESCE(z.zuora_quantity, 0) AS zuora_quantity,
        COALESCE(z.zuora_amount, 0) AS zuora_amount,
        COALESCE(z.zuora_row_count, 0) AS zuora_row_count,
        COALESCE(m.marketplace_quantity, 0) AS marketplace_quantity,
        COALESCE(m.marketplace_amount, 0) AS marketplace_amount,
        COALESCE(m.marketplace_row_count, 0) AS marketplace_row_count,
        m.marketplace_transaction_sources,
        COALESCE(z.zuora_quantity, 0) + COALESCE(m.marketplace_quantity, 0) AS total_billing_quantity,
        COALESCE(z.zuora_amount, 0) + COALESCE(m.marketplace_amount, 0) AS total_billing_amount
    FROM zuora_billing z
    FULL OUTER JOIN marketplace_billing m
        ON m.sf_id = z.sf_id
       AND m.billing_month = z.billing_month
       AND m.recon_stream = z.recon_stream
       AND m.sku_match_group = z.sku_match_group
),

trt_agg AS (
    SELECT
        t.sf_id,
        t.billing_month,
        'CMS' AS recon_stream,
        t.sku_match_group,
        LISTAGG(DISTINCT t.cms_id, ' | ') WITHIN GROUP (ORDER BY t.cms_id) AS trt_cms_ids,
        LISTAGG(DISTINCT t.trt_product_skus, ' | ') WITHIN GROUP (ORDER BY t.trt_product_skus) AS trt_product_skus,
        LISTAGG(DISTINCT t.trt_charge_skus, ' | ') WITHIN GROUP (ORDER BY t.trt_charge_skus) AS trt_charge_skus,
        MIN(t.trt_first_usage_date) AS trt_first_usage_date,
        MAX(t.trt_last_usage_date) AS trt_last_usage_date,
        MAX(t.trt_usage_days) AS trt_usage_days,
        SUM(t.trt_quantity_avg_daily) AS trt_quantity_avg_daily,
        SUM(t.trt_quantity_max_daily) AS trt_quantity_max_daily,
        SUM(t.trt_agent_days) AS trt_agent_days,
        MAX(IFF(t.sku_match_group = 'GSM', d.rmm_partner_types, NULL)) AS rmm_partner_types,
        MAX(IFF(t.sku_match_group = 'GSM', d.webroot_desktop_endpoint_pit, NULL)) AS webroot_desktop_endpoint_pit,
        MAX(IFF(t.sku_match_group = 'GSM', d.webroot_server_endpoint_pit, NULL)) AS webroot_server_endpoint_pit,
        MAX(IFF(t.sku_match_group = 'GSM', d.webroot_endpoint_qty_pit, NULL)) AS webroot_endpoint_qty_pit,
        MAX(IFF(t.sku_match_group = 'GSM', d.rmm_desktop_pit, NULL)) AS rmm_desktop_pit,
        MAX(IFF(t.sku_match_group = 'GSM', d.rmm_server_pit, NULL)) AS rmm_server_pit,
        MAX(IFF(t.sku_match_group = 'GSM', d.rmm_endpoint_qty_pit, NULL)) AS rmm_endpoint_qty_pit,
        MAX(IFF(t.sku_match_group = 'GSM', d.rmm_free_license_qty_pit, NULL)) AS rmm_free_license_qty_pit,
        MAX(IFF(t.sku_match_group = 'GSM', d.webroot_endpoint_to_bill_pit, NULL)) AS webroot_endpoint_to_bill_pit,
        MAX(IFF(t.sku_match_group = 'GSM', d.rmm_discount_qty_pit, NULL)) AS rmm_discount_qty_pit,
        MAX(IFF(t.sku_match_group = 'GSM', d.rolling_usage_days, NULL)) AS rmm_discount_rolling_usage_days,
        MAX(IFF(t.sku_match_group = 'GSM', d.avg_webroot_endpoint_qty, NULL)) AS avg_webroot_endpoint_qty,
        MAX(IFF(t.sku_match_group = 'GSM', d.avg_rmm_endpoint_qty, NULL)) AS avg_rmm_endpoint_qty,
        MAX(IFF(t.sku_match_group = 'GSM', d.avg_rmm_free_license_qty, NULL)) AS avg_rmm_free_license_qty,
        MAX(IFF(t.sku_match_group = 'GSM', d.avg_webroot_endpoint_to_bill, NULL)) AS avg_webroot_endpoint_to_bill,
        MAX(IFF(t.sku_match_group = 'GSM', d.avg_vs_19th_raw_endpoint_qty_delta, NULL)) AS avg_vs_19th_raw_endpoint_qty_delta,
        MAX(IFF(t.sku_match_group = 'GSM', d.avg_vs_19th_billable_endpoint_qty_delta, NULL)) AS avg_vs_19th_billable_endpoint_qty_delta
    FROM WEBROOT_TRT_USAGE_MONTHLY t
    LEFT JOIN WEBROOT_TRT_ENDPOINT_RMM_DISCOUNT_MONTHLY d
        ON d.sf_id = t.sf_id
       AND d.billing_month = t.billing_month
       AND t.sku_match_group = 'GSM'
    GROUP BY 1,2,3,4
),
sf_id_to_partner AS (
    SELECT
        sf_id,
        partner_name
    FROM RECON_PARTNER_MAP
    WHERE sf_id ILIKE 'ACT-%'
      AND partner_name IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY sf_id
        ORDER BY
            IFF(UPPER(partner_name) LIKE '%-INTERNAL', 1, 0),
            IFF(UPPER(partner_name) LIKE '%-CORPORATE', 1, 0),
            LENGTH(partner_name),
            UPPER(partner_name)
    ) = 1
),
sf_account_names AS (
    SELECT
        CWS_ACCOUNT_UNIQUE_IDENTIFIER_C AS sf_id,
        NAME AS account_name
    FROM ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT
    WHERE CWS_ACCOUNT_UNIQUE_IDENTIFIER_C ILIKE 'ACT-%'
      AND IS_DELETED = FALSE
),

joined AS (
    SELECT
        COALESCE(u.billing_month, b.billing_month, t.billing_month) AS billing_month,
        COALESCE(u.recon_stream, b.recon_stream, t.recon_stream) AS recon_stream,
        COALESCE(u.recon_partner_key, b.sf_id, t.sf_id) AS recon_partner_key,
        COALESCE(u.sf_id, b.sf_id, t.sf_id) AS sf_id,
        u.source_streams,
        u.source_channels,
        COALESCE(u.vendor_partner_name, sp.partner_name, sa.account_name) AS vendor_partner_name,
        u.vendor_partner_code,
        COALESCE(u.sku_match_group, b.sku_match_group, t.sku_match_group) AS sku_match_group,
        u.vendor_product,
        b.zuora_skus,
        b.zuora_invoice_numbers,
        b.zuora_invoice_ids,
        b.zuora_charge_names,
        b.zuora_subscription_names,
        b.first_invoice_date,
        b.last_invoice_date,
        b.first_service_start_date,
        b.last_service_end_date,
        b.marketplace_skus,
        COALESCE(u.vendor_quantity, 0) AS vendor_quantity,
        COALESCE(u.vendor_amount, 0) AS vendor_amount,
        COALESCE(u.vendor_source_row_count, 0) AS vendor_source_row_count,
        COALESCE(u.vendor_chargeable_row_count, 0) AS vendor_chargeable_row_count,
        u.vendor_source_files,
        COALESCE(b.zuora_quantity, 0) AS zuora_quantity,
        COALESCE(b.zuora_amount, 0) AS zuora_amount,
        COALESCE(b.zuora_row_count, 0) AS zuora_row_count,
        COALESCE(b.marketplace_quantity, 0) AS marketplace_quantity,
        COALESCE(b.marketplace_amount, 0) AS marketplace_amount,
        COALESCE(b.marketplace_row_count, 0) AS marketplace_row_count,
        b.marketplace_transaction_sources,
        COALESCE(b.total_billing_quantity, 0) AS total_billing_quantity,
        COALESCE(b.total_billing_amount, 0) AS total_billing_amount,
        t.trt_cms_ids,
        t.trt_product_skus,
        t.trt_charge_skus,
        t.trt_first_usage_date,
        t.trt_last_usage_date,
        COALESCE(t.trt_usage_days, 0) AS trt_usage_days,
        COALESCE(t.trt_quantity_avg_daily, 0) AS trt_quantity_avg_daily,
        COALESCE(t.trt_quantity_max_daily, 0) AS trt_quantity_max_daily,
        COALESCE(t.trt_agent_days, 0) AS trt_agent_days,
        t.rmm_partner_types,
        COALESCE(t.webroot_desktop_endpoint_pit, 0) AS webroot_desktop_endpoint_pit,
        COALESCE(t.webroot_server_endpoint_pit, 0) AS webroot_server_endpoint_pit,
        COALESCE(t.webroot_endpoint_qty_pit, 0) AS webroot_endpoint_qty_pit,
        COALESCE(t.rmm_desktop_pit, 0) AS rmm_desktop_pit,
        COALESCE(t.rmm_server_pit, 0) AS rmm_server_pit,
        COALESCE(t.rmm_endpoint_qty_pit, 0) AS rmm_endpoint_qty_pit,
        COALESCE(t.rmm_free_license_qty_pit, 0) AS rmm_free_license_qty_pit,
        COALESCE(t.webroot_endpoint_to_bill_pit, 0) AS webroot_endpoint_to_bill_pit,
        COALESCE(t.rmm_discount_qty_pit, 0) AS rmm_discount_qty_pit,
        COALESCE(t.rmm_discount_rolling_usage_days, 0) AS rmm_discount_rolling_usage_days,
        COALESCE(t.avg_webroot_endpoint_qty, 0) AS avg_webroot_endpoint_qty,
        COALESCE(t.avg_rmm_endpoint_qty, 0) AS avg_rmm_endpoint_qty,
        COALESCE(t.avg_rmm_free_license_qty, 0) AS avg_rmm_free_license_qty,
        COALESCE(t.avg_webroot_endpoint_to_bill, 0) AS avg_webroot_endpoint_to_bill,
        COALESCE(t.avg_vs_19th_raw_endpoint_qty_delta, 0) AS avg_vs_19th_raw_endpoint_qty_delta,
        COALESCE(t.avg_vs_19th_billable_endpoint_qty_delta, 0) AS avg_vs_19th_billable_endpoint_qty_delta
    FROM usage_agg u
    FULL OUTER JOIN billing_agg b
        ON b.sf_id = u.sf_id
       AND b.billing_month = u.billing_month
       AND b.recon_stream = u.recon_stream
       AND b.sku_match_group = u.sku_match_group
    FULL OUTER JOIN trt_agg t
        ON t.sf_id = COALESCE(u.sf_id, b.sf_id)
       AND t.billing_month = COALESCE(u.billing_month, b.billing_month)
       AND t.recon_stream = COALESCE(u.recon_stream, b.recon_stream)
       AND t.sku_match_group = COALESCE(u.sku_match_group, b.sku_match_group)
    LEFT JOIN sf_id_to_partner sp
        ON sp.sf_id = COALESCE(u.sf_id, b.sf_id, t.sf_id)
    LEFT JOIN sf_account_names sa
        ON sa.sf_id = COALESCE(u.sf_id, b.sf_id, t.sf_id)
),

scored AS (
    SELECT
        *,
        total_billing_quantity - vendor_quantity AS qty_delta,
        ABS(total_billing_quantity - vendor_quantity) AS abs_qty_delta,
        total_billing_amount - vendor_amount AS amount_delta,
        ABS(total_billing_amount - vendor_amount) AS abs_amount_delta,
        CASE WHEN zuora_quantity <> 0 AND marketplace_quantity <> 0 THEN TRUE ELSE FALSE END AS duplicate_billing_flag,
        IFF(recon_stream = 'CMS' AND sku_match_group = 'GSM', webroot_endpoint_to_bill_pit, NULL) AS rmm_discounted_qty_pit,
        IFF(recon_stream = 'CMS' AND sku_match_group = 'GSM', total_billing_quantity - webroot_endpoint_to_bill_pit, NULL) AS billing_vs_rmm_discounted_qty_delta,
        IFF(recon_stream = 'CMS' AND sku_match_group = 'GSM', ABS(total_billing_quantity - webroot_endpoint_to_bill_pit), NULL) AS abs_billing_vs_rmm_discounted_qty_delta,
        CASE
            WHEN recon_stream = 'CMS'
             AND sku_match_group = 'GSM'
             AND vendor_quantity <> 0
             AND COALESCE(rmm_discount_qty_pit, 0) > 0
             AND ABS(total_billing_quantity - webroot_endpoint_to_bill_pit) <= GREATEST(3, ABS(webroot_endpoint_to_bill_pit) * 0.05)
                THEN TRUE
            ELSE FALSE
        END AS rmm_discount_explains_billing_flag,
        CASE
            WHEN vendor_quantity <> 0 AND sf_id IS NULL THEN 'PARTNER_MAPPING_REQUIRED'
            WHEN sku_match_group = 'UNMAPPED_CW_SKU' THEN 'SKU_MAPPING_REQUIRED'
            WHEN vendor_quantity <> 0
             AND total_billing_quantity <> 0
             AND ABS(total_billing_quantity - vendor_quantity) <= GREATEST(3, ABS(vendor_quantity) * 0.03)
                THEN 'CLEAR'
            WHEN vendor_quantity <> 0
             AND total_billing_quantity <> 0
             AND ABS(total_billing_quantity - vendor_quantity) <= GREATEST(25, ABS(vendor_quantity) * 0.05)
                THEN 'MINOR_DRIFT'
            WHEN recon_stream = 'CMS'
             AND sku_match_group = 'GSM'
             AND vendor_quantity <> 0
             AND COALESCE(rmm_discount_qty_pit, 0) > 0
             AND ABS(total_billing_quantity - webroot_endpoint_to_bill_pit) <= GREATEST(3, ABS(webroot_endpoint_to_bill_pit) * 0.05)
                THEN 'RMM_DISCOUNTED'
            WHEN total_billing_quantity <> 0
             AND vendor_quantity = 0
             AND trt_quantity_avg_daily <> 0
                THEN 'STRUCTURAL_BILLING_ONLY_TRT_CONFIRMED'
            WHEN total_billing_quantity <> 0
             AND vendor_quantity = 0
                THEN 'BILLING_ONLY_NO_VENDOR_USAGE'
            WHEN total_billing_quantity = 0
             AND vendor_quantity <> 0
             AND trt_quantity_avg_daily <> 0
                THEN 'STRUCTURAL_VENDOR_ONLY_TRT_CONFIRMED'
            WHEN total_billing_quantity = 0
             AND vendor_quantity <> 0
                THEN 'NO_BILLING_NO_HISTORY'
            WHEN total_billing_quantity = 0
             AND vendor_quantity = 0
             AND trt_quantity_avg_daily <> 0
                THEN 'TRT_VENDOR_USAGE_NOT_BILLED'
            WHEN total_billing_quantity > vendor_quantity THEN 'BILLING_DIFFERENTIAL_OVER'
            WHEN total_billing_quantity < vendor_quantity THEN 'BILLING_DIFFERENTIAL_UNDER'
            ELSE 'REVIEW_EXCEPTION'
        END AS outcome_flag,
        CASE
            WHEN trt_quantity_avg_daily = 0 THEN 'NO_TRT_EVIDENCE'
            WHEN total_billing_quantity = 0 THEN 'TRT_PRESENT_WITHOUT_BILLING'
            WHEN ABS(total_billing_quantity - trt_quantity_avg_daily) <= GREATEST(3, ABS(total_billing_quantity) * 0.03) THEN 'TRT_ALIGNS_TO_BILLING'
            WHEN ABS(total_billing_quantity - trt_quantity_max_daily) <= GREATEST(3, ABS(total_billing_quantity) * 0.03) THEN 'TRT_MAX_ALIGNS_TO_BILLING'
            ELSE 'TRT_VARIANCE_TO_BILLING'
        END AS trt_validation_flag
    FROM joined
    WHERE vendor_quantity <> 0
       OR total_billing_quantity <> 0
       OR vendor_amount <> 0
       OR total_billing_amount <> 0
       OR trt_quantity_avg_daily <> 0
)

SELECT
    scored.billing_month AS BILLING_MONTH,
    scored.recon_stream AS RECON_STREAM,
    sf_id,
    recon_partner_key,
    source_streams,
    source_channels,
    vendor_partner_name,
    vendor_partner_code,
    scored.vendor_product,
    scored.sku_match_group,
    zuora_skus,
    zuora_invoice_numbers,
    zuora_invoice_ids,
    zuora_charge_names,
    zuora_subscription_names,
    first_invoice_date,
    last_invoice_date,
    first_service_start_date,
    last_service_end_date,
    marketplace_skus,
    CASE
        WHEN zuora_quantity <> 0 AND marketplace_quantity <> 0 THEN 'ZUORA_AND_MARKETPLACE'
        WHEN zuora_quantity <> 0 THEN 'ZUORA_ONLY'
        WHEN marketplace_quantity <> 0 THEN 'MARKETPLACE_ONLY'
        WHEN trt_quantity_avg_daily <> 0 THEN 'TRT_INTERNAL_VALIDATION_ONLY'
        WHEN vendor_quantity <> 0 THEN 'NO_BILLING_SOURCE'
        ELSE 'BILLING_ONLY'
    END AS billing_source_mix,
    vendor_quantity,
    vendor_amount / NULLIF(vendor_quantity, 0) AS vendor_unit_price,
    vendor_amount,
    zuora_quantity,
    zuora_amount / NULLIF(zuora_quantity, 0) AS zuora_unit_price,
    zuora_amount,
    marketplace_quantity,
    marketplace_amount,
    total_billing_quantity,
    total_billing_amount / NULLIF(total_billing_quantity, 0) AS total_billing_unit_price,
    total_billing_amount,
    qty_delta,
    abs_qty_delta,
    amount_delta,
    abs_amount_delta,
    duplicate_billing_flag,
    FALSE AS marketplace_timing_flag,
    0::FLOAT AS marketplace_timing_quantity,
    trt_cms_ids,
    trt_product_skus,
    trt_charge_skus,
    trt_first_usage_date,
    trt_last_usage_date,
    trt_usage_days,
    trt_quantity_avg_daily,
    IFF(scored.recon_stream = 'CMS', trt_quantity_avg_daily, NULL) AS trt_qty,
    trt_quantity_max_daily,
    trt_agent_days,
    IFF(scored.recon_stream = 'CMS', total_billing_quantity - trt_quantity_avg_daily, NULL) AS billing_vs_trt_qty_delta,
    IFF(scored.recon_stream = 'CMS', ABS(total_billing_quantity - trt_quantity_avg_daily), NULL) AS abs_billing_vs_trt_qty_delta,
    rmm_partner_types,
    webroot_desktop_endpoint_pit,
    webroot_server_endpoint_pit,
    webroot_endpoint_qty_pit,
    rmm_desktop_pit,
    rmm_server_pit,
    rmm_endpoint_qty_pit,
    rmm_free_license_qty_pit,
    rmm_discount_qty_pit,
    rmm_discounted_qty_pit,
    billing_vs_rmm_discounted_qty_delta,
    abs_billing_vs_rmm_discounted_qty_delta,
    rmm_discount_explains_billing_flag,
    rmm_discount_rolling_usage_days,
    avg_webroot_endpoint_qty,
    avg_rmm_endpoint_qty,
    avg_rmm_free_license_qty,
    avg_webroot_endpoint_to_bill,
    avg_vs_19th_raw_endpoint_qty_delta,
    avg_vs_19th_billable_endpoint_qty_delta,
    trt_validation_flag,
    vendor_source_row_count,
    vendor_chargeable_row_count,
    vendor_source_files,
    marketplace_transaction_sources,
    'WEBROOT_USAGE_AGGREGATOR_ONLY_PLUS_INVOICE_VALIDATED' AS partner_match_methods,
    'RECON_SKU_MAP' AS sku_mapping_sources,
    cr.contract_cost_rate AS contract_cost_basis_quantity,
    ROUND(vendor_quantity * COALESCE(cr.contract_cost_rate, 0), 2)::NUMBER AS contract_cost_basis_amount,
    cr.contract_cost_rate,
    CASE WHEN cr.contract_cost_rate IS NOT NULL AND total_billing_quantity > 0
        THEN (total_billing_amount / total_billing_quantity) - cr.contract_cost_rate
        ELSE NULL END AS billing_vs_cost_delta_per_seat,
    CASE WHEN cr.contract_cost_rate IS NOT NULL AND total_billing_quantity > 0
        THEN ((total_billing_amount / total_billing_quantity) - cr.contract_cost_rate) * total_billing_quantity
        ELSE NULL END AS billing_vs_cost_dollar_impact,
    CASE WHEN cr.contract_cost_rate IS NOT NULL AND cr.contract_cost_rate > 0 AND total_billing_quantity > 0
        THEN ROUND(((total_billing_amount / total_billing_quantity) - cr.contract_cost_rate) / cr.contract_cost_rate * 100, 1)
        ELSE NULL END AS billing_vs_cost_pct,
    CASE
        WHEN cr.contract_cost_rate IS NULL THEN NULL
        WHEN total_billing_quantity = 0 THEN NULL
        WHEN (total_billing_amount / total_billing_quantity) > cr.contract_cost_rate * 1.05 THEN 'ABOVE_COST'
        WHEN (total_billing_amount / total_billing_quantity) >= cr.contract_cost_rate * 0.95 THEN 'AT_COST'
        ELSE 'BELOW_COST'
    END AS contract_price_flag,
    CASE WHEN cr.contract_cost_rate IS NOT NULL AND total_billing_quantity > 0
        AND (total_billing_amount / total_billing_quantity) < cr.contract_cost_rate * 0.80
        THEN TRUE ELSE FALSE END AS material_below_cost_flag,
    cr.source_doc AS contract_rate_source_docs,
    CURRENT_TIMESTAMP() AS recon_run_ts,
    scored.outcome_flag,
    CASE
        WHEN outcome_flag = 'PARTNER_MAPPING_REQUIRED' THEN 'External aggregator usage has no resolved SF account mapping.'
        WHEN outcome_flag = 'SKU_MAPPING_REQUIRED' THEN 'Billing SKU was not classified to a Webroot SKU group.'
        WHEN outcome_flag = 'STRUCTURAL_BILLING_ONLY_TRT_CONFIRMED' THEN 'Billed quantity has internal TRT evidence but no partner-level aggregator match.'
        WHEN outcome_flag = 'BILLING_ONLY_NO_VENDOR_USAGE' THEN 'Billed quantity has no matching aggregator usage or TRT support.'
        WHEN outcome_flag = 'STRUCTURAL_VENDOR_ONLY_TRT_CONFIRMED' THEN 'Aggregator usage has TRT evidence but no Zuora/Marketplace billing.'
        WHEN outcome_flag = 'NO_BILLING_NO_HISTORY' THEN 'Aggregator usage found but no matching Zuora/Marketplace billing found.'
        WHEN outcome_flag = 'TRT_VENDOR_USAGE_NOT_BILLED' THEN 'TRT usage exists with no Zuora/Marketplace billing and no aggregator row.'
        WHEN outcome_flag = 'RMM_DISCOUNTED' THEN 'CMS GSM billing aligns to TRT Webroot endpoint quantity after applying the RMM bundled-license discount.'
        WHEN outcome_flag = 'BILLING_DIFFERENTIAL_UNDER' THEN 'External aggregator quantity exceeds billed quantity.'
        WHEN outcome_flag = 'BILLING_DIFFERENTIAL_OVER' THEN 'Billed quantity exceeds external aggregator quantity.'
        ELSE NULL
    END AS investigation_reason,
    CASE
        WHEN outcome_flag IN ('PARTNER_MAPPING_REQUIRED', 'SKU_MAPPING_REQUIRED', 'BILLING_ONLY_NO_VENDOR_USAGE',
                              'STRUCTURAL_VENDOR_ONLY_TRT_CONFIRMED', 'NO_BILLING_NO_HISTORY',
                              'TRT_VENDOR_USAGE_NOT_BILLED', 'BILLING_DIFFERENTIAL_UNDER', 'BILLING_DIFFERENTIAL_OVER')
            THEN TRUE
        ELSE FALSE
    END AS billing_action_required,
    NULL::NUMBER AS vendor_vs_contract_delta_per_seat,
    NULL::NUMBER AS vendor_vs_contract_pct,
    NULL::VARCHAR AS vendor_vs_contract_flag,
    NULL::NUMBER AS vendor_vs_contract_dollar_impact
FROM scored
LEFT JOIN WEBROOT_CONTRACT_RATES cr
    ON cr.vendor_product = scored.sku_match_group
   AND cr.currency = 'USD'
   AND scored.billing_month BETWEEN cr.valid_from AND cr.valid_to;

-- =============================================================================
-- SUMMARY
-- =============================================================================
CREATE OR REPLACE TABLE WEBROOT_RECON_SUMMARY AS
SELECT
    BILLING_MONTH,
    COUNT(*) AS total_rows,
    COUNT_IF(outcome_flag = 'CLEAR') AS perfect_match_rows,
    ROUND(COUNT_IF(outcome_flag = 'CLEAR') * 100.0 / NULLIF(COUNT(*), 0), 1) AS perfect_match_pct,
    COUNT_IF(outcome_flag IN ('CLEAR', 'MINOR_DRIFT', 'STRUCTURAL_BILLING_ONLY_TRT_CONFIRMED', 'RMM_DISCOUNTED')) AS operationally_cleared_rows,
    ROUND(COUNT_IF(outcome_flag IN ('CLEAR', 'MINOR_DRIFT', 'STRUCTURAL_BILLING_ONLY_TRT_CONFIRMED', 'RMM_DISCOUNTED')) * 100.0 / NULLIF(COUNT(*), 0), 1) AS operational_clear_pct,
    SUM(abs_qty_delta) AS abs_qty_variance,
    ROUND(SUM(abs_qty_delta) / NULLIF(SUM(ABS(total_billing_quantity)), 0), 6) AS abs_qty_delta_rate_vs_billing,
    SUM(vendor_quantity)::NUMBER AS total_vendor_seats,
    SUM(vendor_quantity)::NUMBER AS total_external_usage_seats,
    SUM(zuora_quantity) AS total_zuora_seats,
    SUM(marketplace_quantity) AS total_marketplace_seats,
    SUM(total_billing_quantity) AS total_billing_seats,
    SUM(trt_quantity_avg_daily) AS total_trt_avg_daily_seats,
    SUM(trt_quantity_max_daily) AS total_trt_max_daily_seats,
    SUM(COALESCE(vendor_amount, 0))::NUMBER AS total_vendor_amount,
    SUM(total_billing_amount) AS total_billing_amount,
    COUNT_IF(duplicate_billing_flag = TRUE) AS duplicate_billing_rows,
    SUM(IFF(duplicate_billing_flag, vendor_quantity, 0))::NUMBER AS duplicate_billing_vendor_seats,
    SUM(IFF(duplicate_billing_flag, zuora_quantity, 0)) AS duplicate_billing_zuora_seats,
    SUM(IFF(duplicate_billing_flag, marketplace_quantity, 0)) AS duplicate_billing_marketplace_seats,
    SUM(IFF(duplicate_billing_flag, abs_qty_delta, 0)) AS duplicate_billing_abs_qty_variance_impact,
    SUM(IFF(duplicate_billing_flag, abs_amount_delta, 0)) AS duplicate_billing_abs_amount_variance_impact,
    COUNT_IF(outcome_flag = 'PARTNER_MAPPING_REQUIRED') AS unmapped_rows,
    COUNT_IF(outcome_flag = 'SKU_MAPPING_REQUIRED') AS sku_mapping_required_rows,
    COUNT_IF(outcome_flag IN ('NO_BILLING_NO_HISTORY', 'STRUCTURAL_VENDOR_ONLY_TRT_CONFIRMED', 'TRT_VENDOR_USAGE_NOT_BILLED')) AS no_billing_rows,
    COUNT_IF(outcome_flag IN ('BILLING_ONLY_NO_VENDOR_USAGE', 'STRUCTURAL_BILLING_ONLY_TRT_CONFIRMED')) AS billing_only_rows,
    COUNT_IF(outcome_flag = 'BILLING_DIFFERENTIAL_OVER') AS billing_over_rows,
    COUNT_IF(outcome_flag = 'BILLING_DIFFERENTIAL_UNDER') AS vendor_over_rows,
    COUNT_IF(contract_price_flag = 'ABOVE_COST') AS contract_above_cost_rows,
    COUNT_IF(contract_price_flag = 'AT_COST') AS contract_at_cost_rows,
    COUNT_IF(contract_price_flag = 'BELOW_COST') AS contract_below_cost_rows,
    COUNT_IF(material_below_cost_flag = TRUE) AS contract_material_below_cost_rows,
    COUNT_IF(contract_price_flag IS NULL) AS contract_no_rate_rows,
    COALESCE(SUM(IFF(contract_price_flag = 'ABOVE_COST', billing_vs_cost_dollar_impact, 0)), 0) AS contract_above_cost_margin_dollars,
    COALESCE(SUM(IFF(contract_price_flag = 'BELOW_COST', billing_vs_cost_dollar_impact, 0)), 0) AS contract_below_cost_loss_dollars,
    COALESCE(SUM(IFF(material_below_cost_flag = TRUE, billing_vs_cost_dollar_impact, 0)), 0) AS contract_material_below_cost_loss_dollars
FROM WEBROOT_RECON_DETAIL
GROUP BY BILLING_MONTH
ORDER BY BILLING_MONTH;

CREATE OR REPLACE TABLE WEBROOT_OUTCOME_FLAG_DISTRIBUTION AS
SELECT
    BILLING_MONTH,
    outcome_flag,
    COUNT(*) AS row_count,
    SUM(abs_qty_delta) AS abs_qty_delta,
    SUM(vendor_quantity) AS vendor_quantity,
    SUM(total_billing_quantity) AS total_billing_quantity,
    SUM(trt_quantity_avg_daily) AS trt_quantity_avg_daily
FROM WEBROOT_RECON_DETAIL
GROUP BY 1,2
ORDER BY 1,2;

CREATE OR REPLACE TABLE WEBROOT_RECON_SUMMARY_BY_STREAM AS
SELECT
    BILLING_MONTH,
    RECON_STREAM,
    COUNT(*) AS total_rows,
    COUNT_IF(outcome_flag = 'CLEAR') AS perfect_match_rows,
    ROUND(COUNT_IF(outcome_flag = 'CLEAR') * 100.0 / NULLIF(COUNT(*), 0), 1) AS perfect_match_pct,
    COUNT_IF(outcome_flag IN ('CLEAR', 'MINOR_DRIFT', 'STRUCTURAL_BILLING_ONLY_TRT_CONFIRMED', 'RMM_DISCOUNTED')) AS operationally_cleared_rows,
    ROUND(COUNT_IF(outcome_flag IN ('CLEAR', 'MINOR_DRIFT', 'STRUCTURAL_BILLING_ONLY_TRT_CONFIRMED', 'RMM_DISCOUNTED')) * 100.0 / NULLIF(COUNT(*), 0), 1) AS operational_clear_pct,
    SUM(abs_qty_delta) AS abs_qty_variance,
    SUM(vendor_quantity)::NUMBER AS total_external_usage_seats,
    SUM(total_billing_quantity) AS total_billing_seats,
    SUM(trt_quantity_avg_daily) AS total_trt_19th_seats,
    SUM(total_billing_amount) AS total_billing_amount,
    SUM(vendor_amount)::NUMBER AS total_vendor_amount,
    COUNT_IF(outcome_flag = 'PARTNER_MAPPING_REQUIRED') AS unmapped_rows,
    COUNT_IF(outcome_flag = 'SKU_MAPPING_REQUIRED') AS sku_mapping_required_rows,
    COUNT_IF(billing_action_required) AS action_required_rows
FROM WEBROOT_RECON_DETAIL
GROUP BY 1,2
ORDER BY 1,2;

CREATE OR REPLACE TABLE WEBROOT_OUTCOME_FLAG_DISTRIBUTION_BY_STREAM AS
SELECT
    BILLING_MONTH,
    RECON_STREAM,
    outcome_flag,
    COUNT(*) AS row_count,
    SUM(abs_qty_delta) AS abs_qty_delta,
    SUM(vendor_quantity) AS vendor_quantity,
    SUM(total_billing_quantity) AS total_billing_quantity,
    SUM(trt_quantity_avg_daily) AS trt_quantity_avg_daily
FROM WEBROOT_RECON_DETAIL
GROUP BY 1,2,3
ORDER BY 1,2,3;

CREATE OR REPLACE TABLE WEBROOT_TRT_BILLING_VALIDATION AS
SELECT
    BILLING_MONTH,
    RECON_STREAM,
    sku_match_group,
    trt_validation_flag,
    outcome_flag,
    COUNT(*) AS row_count,
    SUM(total_billing_quantity) AS total_billing_quantity,
    SUM(vendor_quantity) AS external_usage_quantity,
    SUM(trt_quantity_avg_daily) AS trt_quantity_avg_daily,
    SUM(ABS(total_billing_quantity - trt_quantity_avg_daily)) AS abs_billing_vs_trt_qty_delta,
    SUM(abs_qty_delta) AS abs_billing_vs_external_qty_delta
FROM WEBROOT_RECON_DETAIL
GROUP BY 1,2,3,4,5
ORDER BY 1,2,3,4,5;

-- Slim app/audit surface. WEBROOT_RECON_DETAIL remains the canonical detail with
-- source files and contract fields; this table is the stable integration grain
-- for the Webroot app and future combined app.
CREATE OR REPLACE TABLE WEBROOT_RECON_DETAIL_APP AS
SELECT
    'Webroot' AS VENDOR,
    BILLING_MONTH,
    SF_ID,
    VENDOR_PARTNER_NAME,
    VENDOR_PRODUCT,
    SKU_MATCH_GROUP,
    ARRAY_DISTINCT(
        ARRAY_CAT(
            COALESCE(ZUORA_SKUS, ARRAY_CONSTRUCT()),
            COALESCE(MARKETPLACE_SKUS, ARRAY_CONSTRUCT())
        )
    ) AS CW_SKUS,
    ZUORA_SKUS,
    MARKETPLACE_SKUS,
    BILLING_SOURCE_MIX,
    ZUORA_INVOICE_NUMBERS AS ZUORA_INV,
    NULL::VARCHAR AS MP_INV,
    VENDOR_QUANTITY,
    VENDOR_UNIT_PRICE,
    VENDOR_AMOUNT,
    ZUORA_QUANTITY,
    ZUORA_UNIT_PRICE,
    ZUORA_AMOUNT,
    MARKETPLACE_QUANTITY,
    MARKETPLACE_AMOUNT,
    TOTAL_BILLING_QUANTITY,
    TOTAL_BILLING_UNIT_PRICE,
    TOTAL_BILLING_AMOUNT,
    QTY_DELTA,
    ABS_QTY_DELTA,
    TRT_QTY,
    IFF(RECON_STREAM = 'CMS' AND SKU_MATCH_GROUP = 'GSM', AVG_WEBROOT_ENDPOINT_QTY, TRT_QTY) AS AVG_API_USAGE,
    BILLING_VS_TRT_QTY_DELTA,
    ABS_BILLING_VS_TRT_QTY_DELTA,
    RMM_DISCOUNTED_QTY_PIT,
    BILLING_VS_RMM_DISCOUNTED_QTY_DELTA,
    ABS_BILLING_VS_RMM_DISCOUNTED_QTY_DELTA,
    RMM_DISCOUNT_EXPLAINS_BILLING_FLAG,
    AMOUNT_DELTA,
    ABS_AMOUNT_DELTA,
    DUPLICATE_BILLING_FLAG,
    MARKETPLACE_TIMING_FLAG,
    MARKETPLACE_TIMING_QUANTITY,
    VENDOR_SOURCE_ROW_COUNT,
    PARTNER_MATCH_METHODS,
    SKU_MAPPING_SOURCES,
    CONTRACT_COST_BASIS_QUANTITY,
    CONTRACT_COST_BASIS_AMOUNT,
    CONTRACT_COST_RATE,
    BILLING_VS_COST_DELTA_PER_SEAT,
    BILLING_VS_COST_DOLLAR_IMPACT,
    BILLING_VS_COST_PCT,
    CONTRACT_PRICE_FLAG,
    MATERIAL_BELOW_COST_FLAG,
    CONTRACT_RATE_SOURCE_DOCS,
    RECON_RUN_TS,
    OUTCOME_FLAG,
    INVESTIGATION_REASON,
    BILLING_ACTION_REQUIRED,
    VENDOR_VS_CONTRACT_DELTA_PER_SEAT,
    VENDOR_VS_CONTRACT_PCT,
    VENDOR_VS_CONTRACT_FLAG,
    VENDOR_VS_CONTRACT_DOLLAR_IMPACT,
    RECON_STREAM,
    RECON_PARTNER_KEY,
    VENDOR_PARTNER_CODE,
    SOURCE_STREAMS,
    SOURCE_CHANNELS,
    VENDOR_CHARGEABLE_ROW_COUNT,
    ZUORA_CHARGE_NAMES,
    RMM_PARTNER_TYPES,
    WEBROOT_DESKTOP_ENDPOINT_PIT,
    WEBROOT_SERVER_ENDPOINT_PIT,
    WEBROOT_ENDPOINT_QTY_PIT,
    RMM_DESKTOP_PIT,
    RMM_SERVER_PIT,
    RMM_ENDPOINT_QTY_PIT,
    RMM_FREE_LICENSE_QTY_PIT,
    RMM_DISCOUNT_QTY_PIT,
    RMM_DISCOUNT_ROLLING_USAGE_DAYS,
    AVG_WEBROOT_ENDPOINT_QTY,
    AVG_RMM_ENDPOINT_QTY,
    AVG_RMM_FREE_LICENSE_QTY,
    AVG_WEBROOT_ENDPOINT_TO_BILL,
    AVG_VS_19TH_RAW_ENDPOINT_QTY_DELTA,
    AVG_VS_19TH_BILLABLE_ENDPOINT_QTY_DELTA,
    TRT_CMS_IDS,
    TRT_PRODUCT_SKUS,
    TRT_CHARGE_SKUS,
    TRT_VALIDATION_FLAG,
    MARKETPLACE_TRANSACTION_SOURCES
FROM WEBROOT_RECON_DETAIL;

