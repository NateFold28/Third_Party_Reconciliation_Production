-- =============================================================================
-- 03_compat_dead_object_views.sql
--
-- Backward-compat views for dead objects still referenced by
-- Vendor_Recon_Pipelines_Prod/<VENDOR>/<VENDOR>_Reconciliation_Script_Prod.sql
--
-- Rather than rewrite the working vendor SQL files, we re-emit the same
-- object names as views over the current live schema. This lets the vendor
-- SQL files execute unchanged while sitting on top of the post-cutover data.
--
-- These are LIVE compat views. When we eventually cut the vendor SQL files
-- over to the unified schema, this file can be dropped.
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

-- -----------------------------------------------------------------------------
-- SENTINELONE_CHARGE_TO_GROUP
--
-- Used by SentinelOne_Reconciliation_Script_Prod.sql to identify which CW
-- SKUs are MDR bundles vs standard. Fabricated from RECON_SKU_MAP.
-- Contract: (product_sku VARCHAR, billing_category VARCHAR)
--   billing_category IN ('MDR_BUNDLE','STANDARD')
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW SENTINELONE_CHARGE_TO_GROUP AS
SELECT DISTINCT
    UPPER(TRIM(CW_SKU)) AS product_sku,
    CASE
        WHEN UPPER(SKU_MATCH_KEY) LIKE '%MDR%' THEN 'MDR_BUNDLE'
        WHEN UPPER(MAPPING_NOTES) LIKE '%MDR%' THEN 'MDR_BUNDLE'
        ELSE 'STANDARD'
    END AS billing_category
FROM RECON_SKU_MAP
WHERE VENDOR = 'SentinelOne'
  AND CW_SKU IS NOT NULL;

-- -----------------------------------------------------------------------------
-- WEBROOT_TRT_USAGE_MONTHLY
--
-- Used by Webroot_Reconciliation_Script_Prod.sql for TRT-side validation.
-- Contract: (sf_id, billing_month, sku_match_group, recon_stream, trt_agent_days)
--   sku_match_group IN ('GSM','DNS','SAT')
--   recon_stream    IN ('CMS','CW')
--
-- Source: THIRD_PARTY_RECON_SOURCE_TRT_PROD filtered to Webroot.
-- The unified TRT source is not stream-partitioned (no CW/CMS split), so we
-- project each sf_id/month onto BOTH streams; the recon SQL joins on
-- (sf_id, billing_month, sku_match_group, recon_stream) and will only find
-- overlap where the Webroot USAGE side actually has that stream. TRT_QUANTITY
-- is the count of agent-months, which the recon SQL treats as trt_agent_days.
--
-- Product group: TRT data is agent-day counts and doesn't distinguish
-- GSM/DNS/SAT natively for Webroot. We tag every row as 'GSM' because that's
-- the only Webroot SKU group with TRT-side validation (per business contract
-- in the vendor SQL comments).
--
-- All columns emitted (superset of what the recon SQL SELECTs):
--   sf_id, cms_id, billing_month, sku_match_group, recon_stream,
--   trt_product_skus, trt_charge_skus,
--   trt_first_usage_date, trt_last_usage_date, trt_usage_days,
--   trt_quantity_avg_daily, trt_quantity_max_daily, trt_agent_days
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW WEBROOT_TRT_USAGE_MONTHLY AS
SELECT
    SF_ID                                                     AS sf_id,
    CMS_ID                                                    AS cms_id,
    BILLING_MONTH                                             AS billing_month,
    'GSM'::VARCHAR                                            AS sku_match_group,
    stream::VARCHAR                                           AS recon_stream,
    NULL::VARCHAR                                             AS trt_product_skus,
    NULL::VARCHAR                                             AS trt_charge_skus,
    NULL::DATE                                                AS trt_first_usage_date,
    NULL::DATE                                                AS trt_last_usage_date,
    DAYS_REPORTING                                            AS trt_usage_days,
    AVG_API_QUANTITY                                          AS trt_quantity_avg_daily,
    MAX_API_QUANTITY                                          AS trt_quantity_max_daily,
    TRT_QUANTITY                                              AS trt_agent_days
FROM THIRD_PARTY_RECON_SOURCE_TRT_PROD
CROSS JOIN (SELECT column1 AS stream FROM VALUES ('CMS'),('CW')) s
WHERE VENDOR = 'Webroot'
  AND SF_ID IS NOT NULL
  AND TRT_QUANTITY IS NOT NULL;

-- -----------------------------------------------------------------------------
-- WEBROOT_TRT_ENDPOINT_RMM_DISCOUNT_MONTHLY
--
-- Used by Webroot_Reconciliation_Script_Prod.sql to enrich the CMS/GSM row
-- with RMM discount features. All columns are LEFT JOIN, so we emit an empty
-- shell (0 rows) with the required column list. Downstream MAX(...) calls
-- return NULL, which is what the RMM discount overlay does when no data
-- exists. This preserves the SQL's ability to run; RMM discount enrichment
-- is a fine-tuning task, not part of skeleton wire-up.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW WEBROOT_TRT_ENDPOINT_RMM_DISCOUNT_MONTHLY AS
SELECT
    NULL::VARCHAR AS sf_id,
    NULL::DATE    AS billing_month,
    NULL::VARCHAR AS rmm_partner_types,
    NULL::FLOAT   AS webroot_desktop_endpoint_pit,
    NULL::FLOAT   AS webroot_server_endpoint_pit,
    NULL::FLOAT   AS webroot_endpoint_qty_pit,
    NULL::FLOAT   AS rmm_desktop_pit,
    NULL::FLOAT   AS rmm_server_pit,
    NULL::FLOAT   AS rmm_endpoint_qty_pit,
    NULL::FLOAT   AS rmm_free_license_qty_pit,
    NULL::FLOAT   AS webroot_endpoint_to_bill_pit,
    NULL::FLOAT   AS rmm_discount_qty_pit,
    NULL::FLOAT   AS rolling_usage_days,
    NULL::FLOAT   AS avg_webroot_endpoint_qty,
    NULL::FLOAT   AS avg_rmm_endpoint_qty,
    NULL::FLOAT   AS avg_rmm_free_license_qty,
    NULL::FLOAT   AS avg_webroot_endpoint_to_bill,
    NULL::FLOAT   AS avg_vs_19th_raw_endpoint_qty_delta,
    NULL::FLOAT   AS avg_vs_19th_billable_endpoint_qty_delta
WHERE 1 = 0;

-- =============================================================================
-- EXIUM COMPAT VIEWS
-- =============================================================================
-- The Exium recon SQL predates the unified schema and references four objects
-- that never had a legacy V5 counterpart (Exium was skipped in the 2026-08-23
-- rename). We rebuild each here off the current live schema:
--   EXIUM_SKU_MAP_V5              (enriched view over RECON_SKU_MAP)
--   EXIUM_USAGE_RECON_COMPAT      (thin rename over EXIUM_USAGE)
--   EXIUM_CONTRACT_RATES          (derived from RECON_SKU_MAP)
--   EXIUM_BILLING_MATCHED         (Zuora source joined to Exium SKU map)
--   EXIUM_MARKETPLACE_BILLING_MATCHED (empty; no Exium Marketplace activity)
-- =============================================================================

-- Enriched EXIUM_SKU_MAP_V5 that includes the columns the recon SQL references.
-- Overrides the plain filter view emitted by 02_unified_reference_maps.sql.
CREATE OR REPLACE VIEW EXIUM_PARTNER_MAPPING_V5 AS
SELECT
    'Exium'::VARCHAR AS VENDOR,
    m.PARTNER_NAME,
    m.PARENT_COMPANY,
    m.SF_ID,
    m.CMS_ID,
    m.ZUORA_NAME,
    'MANUAL_SEED_20260823'::VARCHAR AS mapping_source
FROM RECON_PARTNER_MAP m
;

CREATE OR REPLACE VIEW EXIUM_SKU_MAP_V5 AS
SELECT
    m.VENDOR,
    m.VENDOR_PRODUCT,
    m.VENDOR_SKU,
    m.CW_SKU,
    m.SKU_MATCH_KEY,
    m.MAPPING_NOTES,
    m.CONTRACT_COST_RATE,
    m.CW_RETAIL_RATE,
    -- Exium-specific fabricated columns:
    entity.vendor_entity                                       AS vendor_entity,
    m.SKU_MATCH_KEY                                            AS sku_match_group,
    CASE
        WHEN UPPER(m.VENDOR_PRODUCT) LIKE 'EX-SIA%' THEN 'SIA'
        WHEN UPPER(m.VENDOR_PRODUCT) LIKE 'EX-SPA%' THEN 'SPA'
        WHEN UPPER(m.VENDOR_PRODUCT) LIKE 'EX-CGW%' THEN 'CGW'
        WHEN UPPER(m.VENDOR_PRODUCT) LIKE 'EX-SASE-PRO%' THEN 'SASE_PRO'
        WHEN UPPER(m.VENDOR_PRODUCT) LIKE 'EX-SASE-ESSENTIALS%' THEN 'SASE_ESSENTIALS'
        WHEN UPPER(m.VENDOR_PRODUCT) LIKE 'EX-XDR%' THEN 'XDR'
        WHEN UPPER(m.VENDOR_PRODUCT) LIKE '%ASM%' THEN 'ASM'
        ELSE UPPER(REGEXP_REPLACE(m.VENDOR_PRODUCT, '^EX-', ''))
    END                                                        AS exium_product_family,
    'MANUAL_SEED_20260823'::VARCHAR                            AS mapping_source,
    TRUE                                                       AS is_active
FROM RECON_SKU_MAP m
CROSS JOIN (SELECT column1 AS vendor_entity FROM VALUES ('CMS'),('CW')) entity
WHERE m.VENDOR = 'Exium';

-- Thin rename view: EXIUM_USAGE -> EXIUM_USAGE_RECON_COMPAT.
-- Exposes vendor_entity, vendor_sku_or_product, overage_quantity as expected
-- by the recon SQL.
CREATE OR REPLACE VIEW EXIUM_USAGE_RECON_COMPAT AS
SELECT
    BILLING_MONTH                                              AS billing_month,
    VENDOR_PARTNER_NAME                                        AS vendor_partner_name,
    VENDOR_PRODUCT_SKU                                         AS vendor_sku_or_product,
    MODIFIER                                                   AS vendor_entity,
    QUANTITY                                                   AS quantity,
    NULL::FLOAT                                                AS overage_quantity,
    UNIT_PRICE                                                 AS unit_price,
    AMOUNT                                                     AS amount,
    CURRENCY                                                   AS currency
FROM EXIUM_USAGE;

-- Contract rates: derived from the same SKU map. Every Exium product family
-- gets one row per currency (assume USD for skeleton).
CREATE OR REPLACE VIEW EXIUM_CONTRACT_RATES AS
SELECT
    exium_product_family,
    'USD'::VARCHAR                                             AS currency,
    CONTRACT_COST_RATE                                         AS contract_cost_rate,
    '2026-01-01'::DATE                                         AS valid_from,
    '2099-12-31'::DATE                                         AS valid_to,
    'RECON_SKU_MAP:MANUAL_SEED_20260823'::VARCHAR              AS source_doc
FROM (
    SELECT
        CASE
            WHEN UPPER(VENDOR_PRODUCT) LIKE 'EX-SIA%' THEN 'SIA'
            WHEN UPPER(VENDOR_PRODUCT) LIKE 'EX-SPA%' THEN 'SPA'
            WHEN UPPER(VENDOR_PRODUCT) LIKE 'EX-CGW%' THEN 'CGW'
            WHEN UPPER(VENDOR_PRODUCT) LIKE 'EX-SASE-PRO%' THEN 'SASE_PRO'
            WHEN UPPER(VENDOR_PRODUCT) LIKE 'EX-SASE-ESSENTIALS%' THEN 'SASE_ESSENTIALS'
            WHEN UPPER(VENDOR_PRODUCT) LIKE 'EX-XDR%' THEN 'XDR'
            ELSE UPPER(REGEXP_REPLACE(VENDOR_PRODUCT, '^EX-', ''))
        END AS exium_product_family,
        CONTRACT_COST_RATE
    FROM RECON_SKU_MAP
    WHERE VENDOR = 'Exium' AND CONTRACT_COST_RATE IS NOT NULL AND CONTRACT_COST_RATE > 0
)
QUALIFY ROW_NUMBER() OVER (PARTITION BY exium_product_family ORDER BY contract_cost_rate DESC) = 1;

-- Zuora billing matched to Exium SKU groups. Sources
-- THIRD_PARTY_RECON_SOURCE_ZUORA_PROD filtered to Exium and joins to Exium's
-- SKU map on PRODUCT_SKU = CW_SKU (case-insensitive).
CREATE OR REPLACE VIEW EXIUM_BILLING_MATCHED AS
WITH exium_sku AS (
    SELECT DISTINCT
        UPPER(TRIM(CW_SKU))                                    AS cw_sku_key,
        SKU_MATCH_KEY                                          AS sku_match_group,
        CASE
            WHEN UPPER(VENDOR_PRODUCT) LIKE 'EX-SIA%' THEN 'SIA'
            WHEN UPPER(VENDOR_PRODUCT) LIKE 'EX-SPA%' THEN 'SPA'
            WHEN UPPER(VENDOR_PRODUCT) LIKE 'EX-CGW%' THEN 'CGW'
            WHEN UPPER(VENDOR_PRODUCT) LIKE 'EX-SASE-PRO%' THEN 'SASE_PRO'
            WHEN UPPER(VENDOR_PRODUCT) LIKE 'EX-SASE-ESSENTIALS%' THEN 'SASE_ESSENTIALS'
            WHEN UPPER(VENDOR_PRODUCT) LIKE 'EX-XDR%' THEN 'XDR'
            ELSE UPPER(REGEXP_REPLACE(VENDOR_PRODUCT, '^EX-', ''))
        END                                                    AS exium_product_family
    FROM RECON_SKU_MAP
    WHERE VENDOR = 'Exium' AND CW_SKU IS NOT NULL
)
SELECT
    z.SF_ID                                                    AS sf_id,
    z.BILLING_MONTH                                            AS billing_month,
    m.sku_match_group                                          AS sku_match_group,
    m.exium_product_family                                     AS exium_product_family,
    z.PRODUCT_SKU                                              AS product_sku,
    z.CHARGE_NAME                                              AS charge_names,
    'License'::VARCHAR                                         AS billing_unit_types,
    1::FLOAT                                                   AS billing_qty_multiplier,
    z.QTY                                                      AS zuora_native_quantity,
    z.QTY                                                      AS zuora_quantity,
    z.UNIT_PRICE_USD                                           AS zuora_unit_price,
    z.CHARGE_AMOUNT_USD                                        AS zuora_charge_amount,
    1::NUMBER                                                  AS billing_row_count
FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD z
JOIN exium_sku m
    ON m.cw_sku_key = UPPER(TRIM(z.PRODUCT_SKU))
WHERE z.VENDOR = 'Exium'
  AND z.SF_ID IS NOT NULL;

-- Marketplace billing matched (Exium has no marketplace activity in source;
-- emit an empty shell so the LEFT JOIN in the recon SQL succeeds).
CREATE OR REPLACE VIEW EXIUM_MARKETPLACE_BILLING_MATCHED AS
SELECT
    NULL::VARCHAR AS sf_id,
    NULL::DATE    AS billing_month,
    NULL::VARCHAR AS sku_match_group,
    NULL::VARCHAR AS exium_product_family,
    NULL::VARCHAR AS product_sku,
    NULL::FLOAT   AS marketplace_quantity,
    NULL::FLOAT   AS marketplace_amount,
    NULL::NUMBER  AS marketplace_row_count,
    NULL::VARCHAR AS marketplace_transaction_sources
WHERE 1 = 0;
