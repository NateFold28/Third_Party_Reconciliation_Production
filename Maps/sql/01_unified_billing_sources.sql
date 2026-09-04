-- =============================================================================
-- UNIFIED BILLING SOURCE TABLES (PRODUCTION SCOPE)
-- =============================================================================
-- Centralized billing-source layer used by:
--   * All 9 production vendors (via vendor-specific source selection)
--   * Marketplace-focused subset: Auvik, Bitdefender, Webroot, KeepIT,
--     Proofpoint, SentinelOne, Acronis, ESET, Exium
--
-- Output tables:
--   THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
--   THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
--   THIRD_PARTY_RECON_SOURCE_TRT_PROD
--   THIRD_PARTY_RECON_SOURCE_ROYALTIES_PROD
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

CREATE OR REPLACE VIEW THIRD_PARTY_RECON_SOURCE_ZUORA_PROD AS
WITH fx_rates AS (
    SELECT UPPER(currency_id) AS currency_id, budget_ex_rate
    FROM analytics.dbo_seed_files.seed__fpa_budget_exchange_rates
    WHERE YEAR(start_date) = (
        SELECT MAX(YEAR(start_date))
        FROM analytics.dbo_seed_files.seed__fpa_budget_exchange_rates
    )
),
zuora_base AS (
    SELECT
        z.*,
        CASE
            WHEN TRIM(COALESCE(z.SUBSCRIPTION_SOLD_TO_SFDC_ID, '')) ILIKE 'ACT-%'
                THEN TRIM(z.SUBSCRIPTION_SOLD_TO_SFDC_ID)
            WHEN TRIM(COALESCE(z.SFDC_ACCOUNT_NUMBER, '')) ILIKE 'ACT-%'
                THEN TRIM(z.SFDC_ACCOUNT_NUMBER)
            WHEN TRIM(COALESCE(z.SUBSCRIPTION_SOLD_TO_SFDC_ID, '')) <> ''
                THEN TRIM(z.SUBSCRIPTION_SOLD_TO_SFDC_ID)
            ELSE TRIM(z.SFDC_ACCOUNT_NUMBER)
        END AS raw_sf_id,
        CASE
            WHEN TRIM(COALESCE(z.SUBSCRIPTION_SOLD_TO_SFDC_ID, '')) ILIKE 'ACT-%'
                THEN 'subscription_sold_to_sfdc_id'
            WHEN TRIM(COALESCE(z.SFDC_ACCOUNT_NUMBER, '')) ILIKE 'ACT-%'
                THEN 'sfdc_account_number'
            WHEN TRIM(COALESCE(z.SUBSCRIPTION_SOLD_TO_SFDC_ID, '')) <> ''
                THEN 'subscription_sold_to_non_act'
            WHEN TRIM(COALESCE(z.SFDC_ACCOUNT_NUMBER, '')) <> ''
                THEN 'sfdc_account_number_non_act'
            ELSE 'unresolved'
        END AS raw_sf_id_source
    FROM ANALYTICS_DEV.DBT_NFOLD.FINAL_TPR_ENGINEERING_ZUORA_SOURCE_V2 z
),
month_offsets AS (
    -- Supports annual and multi-year service periods without a calendar-table
    -- dependency. The source currently contains no terms approaching 120 months.
    SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) - 1 AS month_offset
    FROM TABLE(GENERATOR(ROWCOUNT => 120))
),
zuora_recon_rows AS (
    -- Ordinary invoice lines retain their source billing month and amount.
    SELECT
        z.*,
        z.BILLING_MONTH::DATE AS recon_billing_month,
        z.UNIT_PRICE AS recon_unit_price,
        z.CHARGE_AMOUNT AS recon_charge_amount,
        'SOURCE_BILLING_MONTH'::VARCHAR AS billing_period_method
    FROM zuora_base z
    WHERE NOT (
        UPPER(TRIM(z.VENDOR_NAME)) = 'AUVIK'
        AND z.SERVICE_START_DATE IS NOT NULL
        AND z.SERVICE_END_DATE IS NOT NULL
        AND DATEDIFF(day, z.SERVICE_START_DATE, z.SERVICE_END_DATE) >= 60
    )

    UNION ALL

    -- Auvik annual commitments are invoiced once in advance but represent
    -- quantity and economics for every covered service month. Expanding and
    -- amortizing them prevents false "No CW Billing" exceptions after the
    -- invoice month while preserving the original invoice audit fields.
    SELECT
        z.*,
        DATEADD(month, mo.month_offset, DATE_TRUNC('month', z.SERVICE_START_DATE))::DATE
            AS recon_billing_month,
        z.UNIT_PRICE / NULLIF(
            DATEDIFF(
                month,
                DATE_TRUNC('month', z.SERVICE_START_DATE),
                DATE_TRUNC('month', z.SERVICE_END_DATE)
            ) + 1,
            0
        ) AS recon_unit_price,
        z.CHARGE_AMOUNT / NULLIF(
            DATEDIFF(
                month,
                DATE_TRUNC('month', z.SERVICE_START_DATE),
                DATE_TRUNC('month', z.SERVICE_END_DATE)
            ) + 1,
            0
        ) AS recon_charge_amount,
        'SERVICE_PERIOD_AMORTIZED'::VARCHAR AS billing_period_method
    FROM zuora_base z
    JOIN month_offsets mo
        ON mo.month_offset <= DATEDIFF(
            month,
            DATE_TRUNC('month', z.SERVICE_START_DATE),
            DATE_TRUNC('month', z.SERVICE_END_DATE)
        )
    WHERE UPPER(TRIM(z.VENDOR_NAME)) = 'AUVIK'
      AND z.SERVICE_START_DATE IS NOT NULL
      AND z.SERVICE_END_DATE IS NOT NULL
      AND DATEDIFF(day, z.SERVICE_START_DATE, z.SERVICE_END_DATE) >= 60
)
SELECT
    CASE UPPER(TRIM(z.VENDOR_NAME))
        WHEN 'PROOFPOINT' THEN 'Proofpoint'
        WHEN 'SENTINELONE' THEN 'SentinelOne'
        WHEN 'WEBROOT' THEN 'Webroot'
        WHEN 'ACRONIS' THEN 'Acronis'
        WHEN 'KEEPIT' THEN 'KeepIT'
        WHEN 'AUVIK' THEN 'Auvik'
        WHEN 'BITDEFENDER' THEN 'Bitdefender'
        WHEN 'ESET' THEN 'ESET'
        WHEN 'EXIUM' THEN 'Exium'
        ELSE z.VENDOR_NAME
    END AS vendor,
    CASE
        WHEN am.old_sf_id IS NOT NULL
         AND (am.merge_effective_month IS NULL OR z.recon_billing_month >= am.merge_effective_month)
            THEN am.canonical_sf_id
        ELSE z.raw_sf_id
    END AS sf_id,
    CASE
        WHEN am.old_sf_id IS NOT NULL
         AND am.canonical_source = 'MERGED_ACCOUNT_MAP'
            AND (am.merge_effective_month IS NULL OR z.recon_billing_month >= am.merge_effective_month)
            THEN z.raw_sf_id_source || '_merged_account_map'
        WHEN am.old_sf_id IS NOT NULL
         AND am.canonical_source = 'PARENT_ROLLUP'
         AND am.canonical_sf_id <> z.raw_sf_id
            THEN z.raw_sf_id_source || '_parent_rollup'
        ELSE z.raw_sf_id_source
    END AS sf_id_source,
    z.ACCOUNT_CONTINUUM_ID::VARCHAR AS cms_id,
    UPPER(TRIM(z.ENTITY)) AS billing_entity,
    z.ACCOUNT_NUMBER AS zuora_account_number,
    z.ACCOUNT_NAME AS zuora_account_name,
    z.SUBSCRIPTION_SOLD_TO_SFDC_ID AS subscription_sold_to_sf_id_raw,
    z.SUBSCRIPTION_SOLD_TO_ACCOUNT_NAME AS subscription_sold_to_account_name,
    z.recon_billing_month AS billing_month,
    z.BILLING_MONTH::DATE AS source_billing_month,
    z.INVOICE_NUMBER,
    z.INVOICE_ID,
    z.INVOICE_DATE::DATE AS invoice_date,
    z.PRODUCT_SKU,
    z.PRODUCT_NAME,
    z.CHARGE_NAME,
    z.INVOICE_ITEM_SKU,
    z.SUBSCRIPTION_NAME,
    z.QUANTITY AS qty,
    z.recon_unit_price * COALESCE(fx.budget_ex_rate, 1) AS unit_price_usd,
    z.recon_charge_amount * COALESCE(fx.budget_ex_rate, 1) AS charge_amount_usd,
    z.ITEM_TAX_AMOUNT * COALESCE(fx.budget_ex_rate, 1) AS item_tax_amount_usd,
    z.CHARGE_DATE,
    z.SERVICE_START_DATE::DATE AS service_start_date,
    z.SERVICE_END_DATE::DATE AS service_end_date,
    z.billing_period_method,
    z.ACCOUNT_CURRENCY,
    z.INVOICE_SOURCE,
    z.INVOICE_STATUS
FROM zuora_recon_rows z
LEFT JOIN fx_rates fx
    ON fx.currency_id = UPPER(z.ACCOUNT_CURRENCY)
LEFT JOIN RECON_ACCOUNT_MERGE_RESOLVER am
    ON am.old_sf_id = z.raw_sf_id
WHERE UPPER(TRIM(z.VENDOR_NAME)) IN (
    'PROOFPOINT', 'SENTINELONE', 'WEBROOT', 'ACRONIS', 'KEEPIT',
    'AUVIK', 'BITDEFENDER', 'ESET', 'EXIUM'
)
  AND z.INVOICE_STATUS = 'Posted'
  AND z.INVOICE_SOURCE = 'BillRun'
    AND z.recon_billing_month >= '2026-01-01'
    AND (
                COALESCE(z.CHARGE_AMOUNT, 0) <> 0
                OR (
                        UPPER(TRIM(z.VENDOR_NAME)) = 'ACRONIS'
                        AND COALESCE(z.QUANTITY, 0) <> 0
                        AND COALESCE(z.CHARGE_AMOUNT, 0) = 0
                        AND UPPER(TRIM(COALESCE(z.PRODUCT_SKU, ''))) <> 'NOCSRVACRCYBPROTSERV'
                )
            );

CREATE OR REPLACE VIEW THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD AS
WITH carr_base AS (
    SELECT
        'Auvik' AS vendor,
        a.cws_account_unique_identifier_c AS sf_id,
        DATE_TRUNC('month', c.month_year)::DATE AS billing_month,
        c.prod_sku AS product_sku,
        COALESCE(c.ns_usage_qty, 0) AS qty,
        COALESCE(c.arr_budget_rate, 0) / 12 AS amount,
        c.transaction_source,
        c.ns_transaction_id::VARCHAR AS marketplace_invoice_id
    FROM ANALYTICS.DBO.CARR__ALL_TRANSACTIONS c
    LEFT JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT a
        ON a.id = c.acc_id AND a.is_deleted = FALSE
    WHERE c.transaction_source IN (
            'Netsuite Evergreen Usage CW', 'Netsuite Evergreen Usage',
            'Netsuite Evergreen Credit Memos CW', 'Netsuite Evergreen Credit Memos')
        -- CARR vendor ownership is authoritative. The former broad 3RDPARTYSAAS
        -- pattern also admitted Webroot, Perch, Veeam, and Gozynta transactions.
        AND UPPER(TRIM(c.vendor)) = 'AUVIK'
      AND DATE_TRUNC('month', c.month_year)::DATE >= '2026-01-01'
      AND COALESCE(c.ns_usage_qty, 0) <> 0

    UNION ALL

    SELECT
        'Bitdefender' AS vendor,
        COALESCE(a.cws_account_unique_identifier_c, 'UNMAPPED') AS sf_id,
        DATE_TRUNC('month', c.month_year)::DATE AS billing_month,
        c.prod_sku AS product_sku,
        COALESCE(c.ns_usage_qty, 0) AS qty,
        COALESCE(c.product_usage_arr_usd, 0) / 12 AS amount,
        c.transaction_source,
        c.ns_transaction_id::VARCHAR AS marketplace_invoice_id
    FROM ANALYTICS.DBO.CARR__ALL_TRANSACTIONS c
    LEFT JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT a
        ON a.id = c.acc_id AND a.is_deleted = FALSE
    WHERE c.transaction_source IN (
            'Netsuite Evergreen Usage CW', 'Netsuite Evergreen Usage',
            'Netsuite Evergreen Credit Memos CW', 'Netsuite Evergreen Credit Memos')
      AND c.prod_sku ILIKE '3PARTYONPREM%'
      AND (c.prod_sku ILIKE '%BTCD%' OR c.prod_sku ILIKE '%CLDSECGZ%')
      AND DATE_TRUNC('month', c.month_year)::DATE >= '2026-01-01'
      AND COALESCE(c.ns_usage_qty, 0) <> 0

    UNION ALL

    SELECT
        'Webroot' AS vendor,
        a.cws_account_unique_identifier_c AS sf_id,
        DATE_TRUNC('month', c.month_year)::DATE AS billing_month,
        c.prod_sku AS product_sku,
        COALESCE(c.ns_usage_qty, 0) AS qty,
        COALESCE(c.arr_budget_rate, 0) / 12 AS amount,
        c.transaction_source,
        c.ns_transaction_id::VARCHAR AS marketplace_invoice_id
    FROM ANALYTICS.DBO.CARR__ALL_TRANSACTIONS c
    LEFT JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT a
        ON a.id = c.acc_id AND a.is_deleted = FALSE
    WHERE c.transaction_source IN (
            'Netsuite Evergreen Usage CW', 'Netsuite Evergreen Usage',
            'Netsuite Evergreen Credit Memos CW', 'Netsuite Evergreen Credit Memos')
                -- CARR vendor ownership is authoritative. The Webroot catalog includes
                -- legacy names and newer CWP/CMS codes that cannot be covered safely by
                -- a fixed collection of SKU patterns.
            AND UPPER(TRIM(c.vendor)) = 'WEBROOT'
      AND DATE_TRUNC('month', c.month_year)::DATE >= '2026-01-01'
      AND COALESCE(c.ns_usage_qty, 0) <> 0

    UNION ALL

    SELECT
        'KeepIT' AS vendor,
        a.cws_account_unique_identifier_c AS sf_id,
        DATE_TRUNC('month', c.month_year)::DATE AS billing_month,
        c.prod_sku AS product_sku,
        COALESCE(c.ns_usage_qty, c.order_item_quantity, 0) AS qty,
        COALESCE(c.arr_budget_rate, 0) / 12 AS amount,
        c.transaction_source,
        c.ns_transaction_id::VARCHAR AS marketplace_invoice_id
    FROM ANALYTICS.DBO.CARR__ALL_TRANSACTIONS c
    LEFT JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT a
        ON a.id = c.acc_id AND a.is_deleted = FALSE
    WHERE DATE_TRUNC('month', c.month_year)::DATE >= '2026-01-01'
      AND c.vendor ILIKE '%keepit%'

    UNION ALL

    SELECT
        'Proofpoint' AS vendor,
        a.cws_account_unique_identifier_c AS sf_id,
        DATE_TRUNC('month', c.month_year)::DATE AS billing_month,
        c.prod_sku AS product_sku,
        COALESCE(c.ns_usage_qty, 0) AS qty,
        COALESCE(c.arr_budget_rate, 0) / 12 AS amount,
        c.transaction_source,
        c.ns_transaction_id::VARCHAR AS marketplace_invoice_id
    FROM ANALYTICS.DBO.CARR__ALL_TRANSACTIONS c
    LEFT JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT a
        ON a.id = c.acc_id AND a.is_deleted = FALSE
    INNER JOIN (
        SELECT DISTINCT cw_sku
        FROM THIRD_PARTY_RECON_SKU_MAP_PROD
        WHERE vendor = 'Proofpoint'
          AND cw_sku IS NOT NULL
    ) p
        ON p.cw_sku = c.prod_sku
    WHERE c.transaction_source IN (
            'Netsuite Evergreen Usage CW', 'Netsuite Evergreen Usage',
            'Netsuite Evergreen Credit Memos CW', 'Netsuite Evergreen Credit Memos',
            'Salesforce Contract',
            'Min Commit Salesforce Contract and NetSuite Invoice')
      AND DATE_TRUNC('month', c.month_year)::DATE >= '2026-01-01'

    UNION ALL

    SELECT
        'SentinelOne' AS vendor,
        a.cws_account_unique_identifier_c AS sf_id,
        DATE_TRUNC('month', c.month_year)::DATE AS billing_month,
        c.prod_sku AS product_sku,
        COALESCE(c.ns_usage_qty, 0) AS qty,
        COALESCE(c.arr_budget_rate, 0) / 12 AS amount,
        c.transaction_source,
        c.ns_transaction_id::VARCHAR AS marketplace_invoice_id
    FROM ANALYTICS.DBO.CARR__ALL_TRANSACTIONS c
    LEFT JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT a
        ON a.id = c.acc_id AND a.is_deleted = FALSE
    WHERE c.transaction_source IN (
            'Netsuite Evergreen Usage CW', 'Netsuite Evergreen Usage',
            'Netsuite Evergreen Credit Memos CW', 'Netsuite Evergreen Credit Memos')
      AND c.prod_sku ILIKE 'CWSENTINEL1%'
      AND DATE_TRUNC('month', c.month_year)::DATE >= '2026-01-01'

    UNION ALL

    SELECT
        'Acronis' AS vendor,
        a.cws_account_unique_identifier_c AS sf_id,
        DATE_TRUNC('month', c.month_year)::DATE AS billing_month,
        c.prod_sku AS product_sku,
        COALESCE(c.ns_usage_qty, 0) AS qty,
        COALESCE(c.arr_budget_rate, 0) / 12 AS amount,
        c.transaction_source,
        c.ns_transaction_id::VARCHAR AS marketplace_invoice_id
    FROM ANALYTICS.DBO.CARR__ALL_TRANSACTIONS c
    LEFT JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT a
        ON a.id = c.acc_id AND a.is_deleted = FALSE
    WHERE c.transaction_source IN (
            'Netsuite Evergreen Usage CW', 'Netsuite Evergreen Usage',
            'Netsuite Evergreen Credit Memos CW', 'Netsuite Evergreen Credit Memos')
        AND (
            c.prod_sku ILIKE '%MSENS%'
           OR c.prod_sku ILIKE '%ACRONIS%'
           OR c.prod_sku ILIKE 'LEGACYSKU%'
        )
      AND DATE_TRUNC('month', c.month_year)::DATE >= '2026-01-01'

    UNION ALL

    SELECT
        'ESET' AS vendor,
        a.cws_account_unique_identifier_c AS sf_id,
        DATE_TRUNC('month', c.month_year)::DATE AS billing_month,
        c.prod_sku AS product_sku,
        COALESCE(c.ns_usage_qty, 0) AS qty,
        COALESCE(c.arr_budget_rate, 0) / 12 AS amount,
        c.transaction_source,
        c.ns_transaction_id::VARCHAR AS marketplace_invoice_id
    FROM ANALYTICS.DBO.CARR__ALL_TRANSACTIONS c
    LEFT JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT a
        ON a.id = c.acc_id AND a.is_deleted = FALSE
    WHERE c.transaction_source IN (
            'Netsuite Evergreen Usage CW', 'Netsuite Evergreen Usage',
            'Netsuite Evergreen Credit Memos CW', 'Netsuite Evergreen Credit Memos')
      AND c.vendor = 'ESET'
      AND DATE_TRUNC('month', c.month_year)::DATE >= '2026-01-01'

    UNION ALL

    SELECT
        'Exium' AS vendor,
        a.cws_account_unique_identifier_c AS sf_id,
        DATE_TRUNC('month', c.month_year)::DATE AS billing_month,
        c.prod_sku AS product_sku,
        COALESCE(c.ns_usage_qty, 0) AS qty,
        COALESCE(c.arr_budget_rate, 0) / 12 AS amount,
        c.transaction_source,
        c.ns_transaction_id::VARCHAR AS marketplace_invoice_id
    FROM ANALYTICS.DBO.CARR__ALL_TRANSACTIONS c
    LEFT JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT a
        ON a.id = c.acc_id AND a.is_deleted = FALSE
    INNER JOIN (
        SELECT DISTINCT cw_sku
                FROM THIRD_PARTY_RECON_SKU_MAP_PROD
                WHERE vendor = 'Exium'
          AND cw_sku IS NOT NULL
    ) ex
        ON ex.cw_sku = c.prod_sku
    WHERE c.transaction_source IN (
            'Netsuite Evergreen Usage CW', 'Netsuite Evergreen Usage',
            'Netsuite Evergreen Credit Memos CW', 'Netsuite Evergreen Credit Memos')
      AND DATE_TRUNC('month', c.month_year)::DATE >= '2026-01-01'
),
carr_normalized AS (
    SELECT
        c.vendor,
        CASE
            WHEN am.old_sf_id IS NOT NULL
             AND (am.merge_effective_month IS NULL OR c.billing_month >= am.merge_effective_month)
                THEN am.canonical_sf_id
            ELSE c.sf_id
        END AS sf_id,
        c.billing_month,
        c.product_sku,
        c.qty,
        c.amount,
        c.transaction_source,
        c.marketplace_invoice_id
    FROM carr_base c
    LEFT JOIN RECON_ACCOUNT_MERGE_RESOLVER am
      ON am.old_sf_id = c.sf_id
)
SELECT *
FROM carr_normalized
WHERE COALESCE(qty, 0) <> 0
    OR (
          vendor = 'Proofpoint'
     AND transaction_source = 'Salesforce Contract'
     AND COALESCE(amount, 0) <> 0
    );

-- =============================================================================
-- THIRD_PARTY_RECON_SOURCE_TRT_PROD -- RETIRED 2026-08-29
-- -----------------------------------------------------------------------------
-- This view (and the pass-through THIRD_PARTY_RECON_TRT_BILLING_PROD, plus the
-- unused WEBROOT_TRT_PROD base table) have been dropped. All vendor scripts
-- now source usage directly from
--     ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
-- via inline direct-from-live CTEs. Non-wired vendors (Auvik, Bitdefender,
-- ESET) leave API_QUANTITY / AVG_API_QUANTITY NULL until they are re-wired.
-- =============================================================================
DROP VIEW  IF EXISTS THIRD_PARTY_RECON_TRT_BILLING_PROD;
DROP VIEW  IF EXISTS THIRD_PARTY_RECON_SOURCE_TRT_PROD;
DROP TABLE IF EXISTS WEBROOT_TRT_PROD;

-- =============================================================================
-- Retired 2026-08-30: WEBROOT_TRT_USAGE_MONTHLY was an intermediate view that
-- packaged raw BASE_CW_DP_TRT into per-partner/per-month agent-day rows for the
-- Webroot recon script.  That logic is now inlined directly inside
--     PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/
--         Reconciliation/Webroot_Reconciliation_Script_Prod.sql
-- (see the `webroot_trt_*` CTEs feeding `trt_agg`).  The view + its NULL-stub
-- companion WEBROOT_TRT_ENDPOINT_RMM_DISCOUNT_MONTHLY have been dropped so no
-- intermediate state sits between raw TRT and the recon output.  This DROP is
-- idempotent (both objects are already gone after the initial rewire commit).
-- =============================================================================
DROP VIEW IF EXISTS WEBROOT_TRT_USAGE_MONTHLY;
DROP VIEW IF EXISTS WEBROOT_TRT_ENDPOINT_RMM_DISCOUNT_MONTHLY;

-- Archived SQL for WEBROOT_TRT_USAGE_MONTHLY was intentionally removed.
-- Source of truth is now the inline `webroot_trt_*` CTE stack in
-- Reconciliation/Webroot_Reconciliation_Script_Prod.sql.



CREATE OR REPLACE VIEW THIRD_PARTY_RECON_SOURCE_ROYALTIES_PROD AS
WITH royalties_base AS (
    SELECT
        COALESCE(seed_vendor, vendor) AS vendor,
        seed_vendor,
        vendor AS royalties_vendor,
        billing_month::DATE AS billing_month,
        sf_account_nbr AS sf_id,
        third_party_type,
        invoice_number,
        charge_or_credit,
        company_name,
        ship_to_company_name,
        region,
        sku,
        product_sku,
        product_description,
        qty,
        amount,
        original_amount_document
    FROM ANALYTICS.DBO.PRODUCT_MANAGEMENT__ROYALTIES
    WHERE billing_month >= '2026-01-01'
      AND (
          seed_vendor IN ('Auvik', 'Bitdefender', 'Webroot', 'KeepIT', 'ConnectWise', 'Proofpoint')
          OR vendor IN ('Auvik', 'Bitdefender', 'Webroot', 'KeepIT', 'ConnectWise', 'Proofpoint')
      )
)
SELECT
    rb.vendor,
    rb.seed_vendor,
    rb.royalties_vendor,
    rb.billing_month,
    CASE
        WHEN am.old_sf_id IS NOT NULL
         AND (am.merge_effective_month IS NULL OR rb.billing_month >= am.merge_effective_month)
            THEN am.canonical_sf_id
        ELSE rb.sf_id
    END AS sf_id,
    rb.third_party_type,
    rb.invoice_number,
    rb.charge_or_credit,
    rb.company_name,
    rb.ship_to_company_name,
    rb.region,
    rb.sku,
    rb.product_sku,
    rb.product_description,
    rb.qty,
    rb.amount,
    rb.original_amount_document
FROM royalties_base rb
LEFT JOIN RECON_ACCOUNT_MERGE_RESOLVER am
  ON am.old_sf_id = rb.sf_id;

