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

CREATE OR REPLACE TABLE THIRD_PARTY_RECON_SOURCE_ZUORA_PROD AS
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
    FROM ANALYTICS_DEV.DBT_NFOLD.ZUORA_THIRD_PARTY_RECON_BASE z
)
SELECT
    z.VENDOR_NAME AS vendor,
    CASE
        WHEN am.old_sf_id IS NOT NULL
         AND (am.merge_effective_month IS NULL OR z.BILLING_MONTH::DATE >= am.merge_effective_month)
            THEN am.canonical_sf_id
        ELSE z.raw_sf_id
    END AS sf_id,
    CASE
        WHEN am.old_sf_id IS NOT NULL
         AND am.canonical_source = 'MERGED_ACCOUNT_MAP'
         AND (am.merge_effective_month IS NULL OR z.BILLING_MONTH::DATE >= am.merge_effective_month)
            THEN z.raw_sf_id_source || '_merged_account_map'
        WHEN am.old_sf_id IS NOT NULL
         AND am.canonical_source = 'PARENT_ROLLUP'
         AND am.canonical_sf_id <> z.raw_sf_id
            THEN z.raw_sf_id_source || '_parent_rollup'
        ELSE z.raw_sf_id_source
    END AS sf_id_source,
    z.ACCOUNT_CONTINUUM_ID::VARCHAR AS cms_id,
    z.ACCOUNT_NUMBER AS zuora_account_number,
    z.ACCOUNT_NAME AS zuora_account_name,
    z.SUBSCRIPTION_SOLD_TO_SFDC_ID AS subscription_sold_to_sf_id_raw,
    z.SUBSCRIPTION_SOLD_TO_ACCOUNT_NAME AS subscription_sold_to_account_name,
    z.BILLING_MONTH::DATE AS billing_month,
    z.INVOICE_NUMBER,
    z.INVOICE_ID,
    z.PRODUCT_SKU,
    z.PRODUCT_NAME,
    z.CHARGE_NAME,
    z.QUANTITY AS qty,
    z.UNIT_PRICE * COALESCE(fx.budget_ex_rate, 1) AS unit_price_usd,
    z.CHARGE_AMOUNT * COALESCE(fx.budget_ex_rate, 1) AS charge_amount_usd,
    z.ACCOUNT_CURRENCY,
    z.INVOICE_SOURCE,
    z.INVOICE_STATUS
FROM zuora_base z
LEFT JOIN fx_rates fx
    ON fx.currency_id = UPPER(z.ACCOUNT_CURRENCY)
LEFT JOIN RECON_ACCOUNT_MERGE_RESOLVER am
    ON am.old_sf_id = z.raw_sf_id
WHERE z.VENDOR_NAME IN (
    'Proofpoint', 'SentinelOne', 'Webroot', 'Acronis', 'KeepIT',
    'Auvik', 'Bitdefender', 'ESET', 'Exium'
)
  AND z.INVOICE_STATUS = 'Posted'
  AND z.INVOICE_SOURCE = 'BillRun'
  AND z.BILLING_MONTH >= '2026-01-01'
    AND COALESCE(z.CHARGE_AMOUNT, 0) <> 0;

CREATE OR REPLACE TABLE THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD AS
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
      AND (c.prod_sku ILIKE '%AUVIK%' OR c.prod_sku ILIKE 'CULC%' OR c.prod_sku ILIKE 'CWANN%'
           OR c.prod_sku ILIKE '3RDPARTYSAAS%' OR c.prod_sku ILIKE '3PARTYSAAS%'
           OR c.prod_sku ILIKE 'SRM-SAM%')
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
      AND (c.prod_sku ILIKE '%WEBROOT%' OR c.prod_sku ILIKE '%WRSEC%' OR c.prod_sku ILIKE '%SEWRS%'
           OR c.prod_sku ILIKE '%3P-SAAS3002%' OR c.prod_sku ILIKE '%3RDPARTYSAASIIT%'
           OR c.prod_sku ILIKE 'CU-WEBROOT%')
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
-- THIRD_PARTY_RECON_SOURCE_TRT_PROD (cycle-aware, seed-scoped)
-- -----------------------------------------------------------------------------
-- Cycle-billed vendors: SentinelOne, Bitdefender, Webroot, Auvik, Proofpoint.
-- All cycle vendors' manual recon workbooks (S1 "ConnectWise Usage", Webroot
-- "DNS_SAT", Bitdefender "Usage", Auvik CMS, Proofpoint Usage) are built from this raw table:
--
--     ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
--
-- with the same filter shape:
--     WHERE on_date = <cycle_day of billing_month>
--       AND partner_id = <CW numeric partner id>
--       AND product_sku IN (SELECT prod_sku FROM seed__product_categorization
--                           WHERE vendor      ILIKE '%<vendor>%'
--                              OR sub_category ILIKE '%<vendor>%')
--       [AND is_server = '']   -- Webroot DNS/SAT product only
--
-- Vendor cycle snapshot days (agent counts on this day drive the invoice):
--   SentinelOne   21    Bitdefender   21    Webroot   19    Auvik   21    Proofpoint  21
--
-- Cycle window used for AVG_API_QUANTITY (matches the invoice period):
--   (previous cycle_day, current cycle_day]     e.g. Webroot Jun 2026 = 5/20 - 6/19
--
-- SF_ID bridge: partner_id in the raw table is numeric (e.g. 15431), matching
-- ZUORA.ACCOUNT_CONTINUUM_ID. Two lookup paths are combined, preferring the
-- curated map:
--   1. THIRD_PARTY_RECON_PARTNER_MAP_PROD.CMS_ID = raw.partner_id (curated per-vendor)
--   2. ZUORA_THIRD_PARTY_RECON_BASE.ACCOUNT_CONTINUUM_ID -> SFDC_ACCOUNT_NUMBER
--      (all-vendor Zuora bridge — daily-refreshed, ACT-* preferred)
--
-- Zuora bridge coverage: ~98% of BD partners, 100% of S1/Auvik/Webroot partners.
-- The prior CORE__RPT_CMS_USAGE fallback was stale (max ON_DATE = 2023-03-12)
-- and has been removed.
--
-- Output columns (used by downstream vendor reconciliations):
--   VENDOR, sf_id, cms_id (numeric partner_id), billing_month, snapshot_date,
--   trt_quantity        - point-in-time agent_cnt on snapshot_date (== vendor invoice qty)
--   avg_api_quantity    - daily average across the cycle window
--   max_api_quantity    - peak daily count in the cycle
--   min_api_quantity    - trough daily count in the cycle
--   days_reporting      - # distinct days in the cycle with any usage row for the partner
-- =============================================================================
CREATE OR REPLACE TABLE THIRD_PARTY_RECON_SOURCE_TRT_PROD AS
WITH vendor_config AS (
    -- Cycle day + vendor pattern for seed__product_categorization scope.
    -- To enroll another cycle-billed vendor, add a row here plus a matching
    -- config row and re-run the pipeline; no other code changes required.
    SELECT 'SentinelOne'::VARCHAR AS vendor, 21::INT AS cycle_day, '%sentinel%'::VARCHAR    AS vendor_pattern, FALSE AS is_server_blank_only
    UNION ALL SELECT 'Bitdefender'::VARCHAR, 21::INT, '%bitdefender%'::VARCHAR, FALSE
    UNION ALL SELECT 'Webroot'::VARCHAR,     19::INT, '%webroot%'::VARCHAR,     TRUE   -- SAT/DNS product uses is_server=''
    UNION ALL SELECT 'Auvik'::VARCHAR,       21::INT, '%auvik%'::VARCHAR,       FALSE
    UNION ALL SELECT 'Proofpoint'::VARCHAR,  21::INT, '%proof%'::VARCHAR,       FALSE
),
vendor_skus AS (
    -- Canonical SKU set per vendor. We combine two SKU sources for maximum
    -- coverage of the raw TRT feed:
    --   (a) seed__product_categorization matched by vendor / sub_category
    --   (b) any SKU that Zuora has invoiced under this vendor since 2026-01-01
    -- Path (b) catches partners billed under vendor-specific SKUs whose seed
    -- rows haven't caught up to the current catalog (e.g. Webroot GSM=SEWRS*,
    -- WSADNS*, WRSECGSM*). This widens API coverage without duplicating logic
    -- across vendors.
    SELECT vendor, prod_sku FROM (
        SELECT
            vc.vendor,
            p.prod_sku
        FROM vendor_config vc
        JOIN analytics.dbo_transformation.seed__product_categorization p
          ON p.vendor       ILIKE vc.vendor_pattern
          OR p.sub_category ILIKE vc.vendor_pattern
        UNION
        SELECT DISTINCT
            vc.vendor,
            z.PRODUCT_SKU AS prod_sku
        FROM vendor_config vc
        JOIN ANALYTICS_DEV.DBT_NFOLD.ZUORA_THIRD_PARTY_RECON_BASE z
          ON UPPER(z.VENDOR_NAME) = UPPER(vc.vendor)
        WHERE z.PRODUCT_SKU IS NOT NULL
          AND z.INVOICE_STATUS = 'Posted'
          AND z.BILLING_MONTH >= '2026-01-01'
    )
    GROUP BY 1, 2
),
raw_usage AS (
    -- Filter raw table to only vendor-relevant SKUs. Apply Webroot's
    -- is_server = '' guard for the SAT/DNS product line (endpoints are the
    -- N/Y is_server rows and are billed via RMM, not the DNS SAT cycle).
    SELECT
        vc.vendor,
        u.partner_id::VARCHAR      AS partner_id,
        u.on_date::DATE            AS on_date,
        u.product_sku,
        u.agent_cnt::FLOAT         AS agent_cnt
    FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
    JOIN vendor_skus vs
      ON vs.prod_sku = u.product_sku
    JOIN vendor_config vc
      ON vc.vendor = vs.vendor
    WHERE u.on_date::DATE >= '2025-12-01'
      AND (NOT vc.is_server_blank_only OR COALESCE(u.is_server, '') = '')
),
partner_daily AS (
    -- One row per (vendor, partner_id, on_date). Sum across product SKUs and
    -- sites — the cycle count is the total active agents for the account.
    SELECT
        vendor,
        partner_id,
        on_date,
        SUM(agent_cnt) AS agent_cnt
    FROM raw_usage
    GROUP BY 1, 2, 3
),
month_spine AS (
    SELECT DATEADD('month', SEQ4(), '2026-01-01')::DATE AS billing_month
    FROM TABLE(GENERATOR(ROWCOUNT => 36))
),
cycles AS (
    -- Snapshot date + prior-snapshot date for each vendor/billing_month.
    SELECT
        v.vendor,
        v.cycle_day,
        s.billing_month,
        DATEADD('day', v.cycle_day - 1, s.billing_month)::DATE               AS snapshot_date,
        DATEADD('day', v.cycle_day - 1, DATEADD('month', -1, s.billing_month))::DATE
                                                                             AS prev_snapshot_date
    FROM month_spine s
    CROSS JOIN vendor_config v
    WHERE DATEADD('day', v.cycle_day - 1, s.billing_month)::DATE <= CURRENT_DATE()
),
point_in_time AS (
    -- Point-in-time agent_cnt on the snapshot day (matches vendor invoice).
    SELECT
        c.vendor,
        c.billing_month,
        c.snapshot_date,
        pd.partner_id,
        pd.agent_cnt AS trt_quantity
    FROM cycles c
    JOIN partner_daily pd
      ON pd.vendor  = c.vendor
     AND pd.on_date = c.snapshot_date
),
cycle_avg AS (
    -- Daily avg / max / min across (prev_snapshot, snapshot].
    SELECT
        c.vendor,
        c.billing_month,
        c.snapshot_date,
        pd.partner_id,
        AVG(pd.agent_cnt)          AS avg_api_quantity,
        MAX(pd.agent_cnt)          AS max_api_quantity,
        MIN(pd.agent_cnt)          AS min_api_quantity,
        COUNT(DISTINCT pd.on_date) AS days_reporting
    FROM cycles c
    JOIN partner_daily pd
      ON pd.vendor  = c.vendor
     AND pd.on_date >  c.prev_snapshot_date
     AND pd.on_date <= c.snapshot_date
    GROUP BY 1, 2, 3, 4
),
merged AS (
    SELECT
        COALESCE(p.vendor,        a.vendor)        AS vendor,
        COALESCE(p.partner_id,    a.partner_id)    AS partner_id,
        COALESCE(p.billing_month, a.billing_month) AS billing_month,
        COALESCE(p.snapshot_date, a.snapshot_date) AS snapshot_date,
        p.trt_quantity                             AS trt_quantity,
        a.avg_api_quantity                         AS avg_api_quantity,
        a.max_api_quantity                         AS max_api_quantity,
        a.min_api_quantity                         AS min_api_quantity,
        a.days_reporting                           AS days_reporting
    FROM point_in_time p
    FULL OUTER JOIN cycle_avg a
      ON p.vendor        = a.vendor
     AND p.partner_id    = a.partner_id
     AND p.billing_month = a.billing_month
),
zuora_bridge AS (
    -- Bridge CW numeric partner_id -> SFDC_ACCOUNT_NUMBER using Zuora directly.
    -- ZUORA.ACCOUNT_CONTINUUM_ID is the CW partner_id and SFDC_ACCOUNT_NUMBER is
    -- the salesforce ACT- id we want on every row. This replaces the previous
    -- CORE__RPT_CMS_USAGE fallback which stopped refreshing in March 2023.
    -- Zuora refreshes daily, so this bridge is always current.
    --   * Include ALL vendor invoices (posted) so a partner billed for any
    --     third-party product resolves — not just the vendor being reconciled.
    --   * Prefer ACT- form and the most recent billing_month per partner.
    SELECT partner_id, sf_id
    FROM (
        SELECT
            ACCOUNT_CONTINUUM_ID::VARCHAR AS partner_id,
            SFDC_ACCOUNT_NUMBER            AS sf_id,
            ROW_NUMBER() OVER (
                PARTITION BY ACCOUNT_CONTINUUM_ID
                ORDER BY CASE WHEN SFDC_ACCOUNT_NUMBER ILIKE 'ACT-%' THEN 0 ELSE 1 END,
                         BILLING_MONTH DESC
            ) AS rk
        FROM ANALYTICS_DEV.DBT_NFOLD.ZUORA_THIRD_PARTY_RECON_BASE
        WHERE SFDC_ACCOUNT_NUMBER IS NOT NULL
          AND TRIM(SFDC_ACCOUNT_NUMBER) <> ''
          AND INVOICE_STATUS = 'Posted'
    )
    WHERE rk = 1
)
SELECT
    m.vendor                                                       AS VENDOR,
    CASE
        WHEN am.old_sf_id IS NOT NULL
         AND (am.merge_effective_month IS NULL OR m.billing_month >= am.merge_effective_month)
            THEN am.canonical_sf_id
        ELSE COALESCE(pm.SF_ID, zb.sf_id)
    END                                                            AS sf_id,
    m.partner_id                                                   AS cms_id,
    m.billing_month                                                AS billing_month,
    m.snapshot_date                                                AS snapshot_date,
    COALESCE(m.trt_quantity, 0)::FLOAT                             AS trt_quantity,
    m.avg_api_quantity::FLOAT                                      AS avg_api_quantity,
    m.max_api_quantity::FLOAT                                      AS max_api_quantity,
    m.min_api_quantity::FLOAT                                      AS min_api_quantity,
    m.days_reporting::INT                                          AS days_reporting,
    -- Bridge audit column: which lookup path resolved the SF_ID.
    CASE
        WHEN pm.SF_ID IS NOT NULL
         AND am.old_sf_id IS NOT NULL
         AND am.canonical_source = 'MERGED_ACCOUNT_MAP'
         AND (am.merge_effective_month IS NULL OR m.billing_month >= am.merge_effective_month) THEN 'partner_map_monthly_merged'
        WHEN pm.SF_ID IS NOT NULL
         AND am.old_sf_id IS NOT NULL
         AND am.canonical_source = 'PARENT_ROLLUP'
         AND am.canonical_sf_id <> COALESCE(pm.SF_ID, zb.sf_id) THEN 'partner_map_monthly_parent_rollup'
        WHEN pm.SF_ID IS NOT NULL THEN 'partner_map_monthly'
        WHEN zb.sf_id IS NOT NULL
         AND am.old_sf_id IS NOT NULL
         AND am.canonical_source = 'MERGED_ACCOUNT_MAP'
         AND (am.merge_effective_month IS NULL OR m.billing_month >= am.merge_effective_month) THEN 'zuora_bridge_merged'
        WHEN zb.sf_id IS NOT NULL
         AND am.old_sf_id IS NOT NULL
         AND am.canonical_source = 'PARENT_ROLLUP'
         AND am.canonical_sf_id <> COALESCE(pm.SF_ID, zb.sf_id) THEN 'zuora_bridge_parent_rollup'
        WHEN zb.sf_id IS NOT NULL THEN 'zuora_bridge'
        ELSE 'unresolved'
    END::VARCHAR                                                   AS sf_id_source
FROM merged m
LEFT JOIN RECON_PARTNER_MAP_MONTHLY pm
       ON pm.CMS_ID              = m.partner_id
      AND pm.BILLING_MONTH       = m.billing_month
    -- Partner map is now vendor-agnostic (no VENDOR column).
    -- Resolve by CMS_ID and let vendor context come from TRT stream.
LEFT JOIN zuora_bridge zb
       ON zb.partner_id = m.partner_id
LEFT JOIN RECON_ACCOUNT_MERGE_RESOLVER am
       ON am.old_sf_id = COALESCE(pm.SF_ID, zb.sf_id)
WHERE m.billing_month >= '2026-01-01';

CREATE OR REPLACE TABLE THIRD_PARTY_RECON_SOURCE_ROYALTIES_PROD AS
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

