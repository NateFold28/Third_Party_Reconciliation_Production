-- =============================================================================
-- 02_unified_reference_maps.sql   (idempotent v2)
--
-- Builds:
--   RECON_PARTNER_MAP   (VENDOR, PARTNER_NAME, PARENT_COMPANY, SF_ID, CMS_ID, ZUORA_NAME)
--   RECON_SKU_MAP      (VENDOR, VENDOR_PRODUCT, VENDOR_SKU, CW_SKU, SKU_MATCH_KEY,
--                       MAPPING_NOTES, CONTRACT_COST_RATE, CW_RETAIL_RATE)
--
-- Sources unioned into RECON_PARTNER_MAP:
--   <VENDOR>_PARTNER_MAPPING_V5_LEGACY_20260823   (8 vendor tables, pre-cutover)
--   RECON_MANUAL_SEED_PARTNER_MAP                 (5,874 master partners × Exium/S1/Webroot)
--
-- Sources unioned into RECON_SKU_MAP:
--   <VENDOR>_SKU_MAP_V5_LEGACY_20260823           (7 vendor tables, pre-cutover)
--   RECON_MANUAL_SEED_SKU_MAP                     (Exium/S1/Webroot hand-curated)
--
-- Emits backward-compat views <VENDOR>_PARTNER_MAPPING_V5 and <VENDOR>_SKU_MAP_V5
-- so the 9 vendor Reconciliation_Script_Prod.sql files continue to work unchanged.
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

-- -----------------------------------------------------------------------------
-- 1) PARTNER map  (source of truth: THIRD_PARTY_RECON_PARTNER_MAP_PROD)
-- The VENDOR column is no longer included — removes the granularity that was
-- causing fanout, and allows deduplication across cross-vendor partner aliases.
--
-- 2026-08-26 hardening:
--   1) Build unified ACT-* merged-account resolver from
--      ANALYTICS.DBO.CW_DW__MERGED_ACCOUNT_MAP (recursive chain-aware).
--   2) Persist merge effective timestamp/month for each OLD sf_id.
--   3) Emit RECON_PARTNER_MAP_MONTHLY so vendor SQL can resolve sf_id by
--      BILLING_MONTH: pre-merge months keep RAW_SF_ID; post-merge months use
--      canonical SF_ID.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE RECON_ACCOUNT_MERGE_RESOLVER AS
WITH merge_edges AS (
    SELECT DISTINCT
        TRIM(old_account) AS old_sf_id,
        TRIM(new_account) AS new_sf_id,
        merged_by_date::TIMESTAMP_NTZ AS merged_by_ts
    FROM ANALYTICS.DBO.CW_DW__MERGED_ACCOUNT_MAP
    WHERE mapping_type = 'Salesforce'
      AND old_account ILIKE 'ACT-%'
      AND new_account ILIKE 'ACT-%'
      AND old_account IS NOT NULL
      AND new_account IS NOT NULL
      AND old_account <> new_account
),
parent_edges AS (
        SELECT DISTINCT
                TRIM(c.cws_account_unique_identifier_c) AS old_sf_id,
                TRIM(p.cws_account_unique_identifier_c) AS new_sf_id
        FROM ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT c
        JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT p
            ON p.id = c.parent_id
        WHERE c.is_deleted = FALSE
            AND p.is_deleted = FALSE
            AND c.cws_account_unique_identifier_c ILIKE 'ACT-%'
            AND p.cws_account_unique_identifier_c ILIKE 'ACT-%'
            AND c.cws_account_unique_identifier_c <> p.cws_account_unique_identifier_c
),
walk AS (
    SELECT
        e.old_sf_id AS seed_old_sf_id,
        e.old_sf_id,
        e.new_sf_id,
        e.merged_by_ts,
        1 AS resolver_depth,
        e.old_sf_id || '>' || e.new_sf_id AS path
    FROM merge_edges e

    UNION ALL

    SELECT
        w.seed_old_sf_id,
        w.old_sf_id,
        e.new_sf_id,
        GREATEST(w.merged_by_ts, e.merged_by_ts) AS merged_by_ts,
        w.resolver_depth + 1 AS resolver_depth,
        w.path || '>' || e.new_sf_id AS path
    FROM walk w
    JOIN merge_edges e
      ON e.old_sf_id = w.new_sf_id
    WHERE w.resolver_depth < 20
      AND POSITION(e.new_sf_id IN w.path) = 0
),
resolved AS (
    SELECT
        seed_old_sf_id AS old_sf_id,
        new_sf_id      AS canonical_sf_id,
        merged_by_ts   AS merge_effective_ts,
        DATE_TRUNC('MONTH', merged_by_ts)::DATE AS merge_effective_month,
        resolver_depth
    FROM walk
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY seed_old_sf_id
        ORDER BY resolver_depth DESC, merged_by_ts DESC, new_sf_id
    ) = 1
),
parent_walk AS (
    SELECT
        pe.old_sf_id AS seed_old_sf_id,
        pe.old_sf_id,
        pe.new_sf_id,
        1 AS resolver_depth,
        pe.old_sf_id || '>' || pe.new_sf_id AS path
    FROM parent_edges pe

    UNION ALL

    SELECT
        pw.seed_old_sf_id,
        pw.old_sf_id,
        pe.new_sf_id,
        pw.resolver_depth + 1 AS resolver_depth,
        pw.path || '>' || pe.new_sf_id AS path
    FROM parent_walk pw
    JOIN parent_edges pe
      ON pe.old_sf_id = pw.new_sf_id
    WHERE pw.resolver_depth < 20
      AND POSITION(pe.new_sf_id IN pw.path) = 0
),
resolved_parent AS (
    SELECT
        seed_old_sf_id AS old_sf_id,
        new_sf_id      AS canonical_parent_sf_id,
        resolver_depth
    FROM parent_walk
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY seed_old_sf_id
        ORDER BY resolver_depth DESC, new_sf_id
    ) = 1
),
identity_ids AS (
    SELECT DISTINCT SF_ID AS sf_id
    FROM THIRD_PARTY_RECON_PARTNER_MAP_PROD
    WHERE SF_ID ILIKE 'ACT-%'
)
SELECT
    i.sf_id AS old_sf_id,
    COALESCE(r.canonical_sf_id, rp.canonical_parent_sf_id, i.sf_id) AS canonical_sf_id,
    r.merge_effective_ts,
    r.merge_effective_month,
    COALESCE(r.resolver_depth, rp.resolver_depth) AS resolver_depth,
    CASE
        WHEN r.old_sf_id IS NOT NULL THEN 'MERGED_ACCOUNT_MAP'
        WHEN rp.old_sf_id IS NOT NULL THEN 'PARENT_ROLLUP'
        ELSE 'IDENTITY'
    END AS canonical_source
FROM identity_ids i
LEFT JOIN resolved r
  ON r.old_sf_id = i.sf_id
LEFT JOIN resolved_parent rp
  ON rp.old_sf_id = i.sf_id;

CREATE OR REPLACE TABLE RECON_PARTNER_MAP AS
WITH manual_partner_overrides AS (
    SELECT *
    FROM (
        SELECT 'Gurusis Inc'::VARCHAR AS PARTNER_NAME, NULL::VARCHAR AS PARENT_COMPANY, 'ACT-00383480'::VARCHAR AS RAW_SF_ID, NULL::VARCHAR AS CMS_ID, NULL::VARCHAR AS ZUORA_NAME
        UNION ALL SELECT 'Kimmit Ltd'::VARCHAR, NULL::VARCHAR, 'ACT-00287424'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Riviera Networks Limited'::VARCHAR, NULL::VARCHAR, 'ACT-00242673'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'JEVSUPPORT, LLC'::VARCHAR, NULL::VARCHAR, 'ACT-00292117'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'CHS Networks'::VARCHAR, NULL::VARCHAR, 'ACT-00084560'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'CHS Networks Limited (CA tenant)'::VARCHAR, NULL::VARCHAR, 'ACT-00084560'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Elevity'::VARCHAR, NULL::VARCHAR, 'ACT-00238028'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Elevity IT'::VARCHAR, NULL::VARCHAR, 'ACT-00238028'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'FLR Spectron Ltd'::VARCHAR, NULL::VARCHAR, 'ACT-00012675'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'FLR Spectron'::VARCHAR, NULL::VARCHAR, 'ACT-00012675'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'OneCom Ltd'::VARCHAR, NULL::VARCHAR, 'ACT-00224155'::VARCHAR, '24180'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'OneCom'::VARCHAR, NULL::VARCHAR, 'ACT-00224155'::VARCHAR, '24180'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Bulletproof InfoTech Inc.'::VARCHAR, NULL::VARCHAR, 'ACT-00239634'::VARCHAR, '21368'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Circle Technologies, Ltd'::VARCHAR, NULL::VARCHAR, 'ACT-00246156'::VARCHAR, '27039'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Extech Ltd (1010936)'::VARCHAR, NULL::VARCHAR, 'ACT-00095923'::VARCHAR, '500000207'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Staley Technologies â€“ Cyber & Managed IT Services (HoganTaylor)'::VARCHAR, NULL::VARCHAR, 'ACT-00175494'::VARCHAR, '24798'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Staley Technologies ? Cyber & Managed IT Services'::VARCHAR, NULL::VARCHAR, 'ACT-00175494'::VARCHAR, '24798'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'ScotiaComp Technologies'::VARCHAR, NULL::VARCHAR, 'ACT-00065309'::VARCHAR, '29456'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Commercial Networks LTD'::VARCHAR, NULL::VARCHAR, 'ACT-00107189'::VARCHAR, '500001162'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'CWL Systems Ltd'::VARCHAR, NULL::VARCHAR, 'ACT-00184685'::VARCHAR, '15052'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'OfficeAnyplace Ltd'::VARCHAR, NULL::VARCHAR, 'ACT-00011794'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'CMIT South Brevard 179'::VARCHAR, NULL::VARCHAR, 'ACT-00245679'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'CMIT Solutions of Oak Park, Hinsdale and Oak Brook - 887,107'::VARCHAR, NULL::VARCHAR, 'ACT-00240756'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
    ) o
),
base_src AS (
    SELECT DISTINCT
        PARTNER_NAME::VARCHAR   AS PARTNER_NAME,
        PARENT_COMPANY::VARCHAR AS PARENT_COMPANY,
        SF_ID::VARCHAR          AS RAW_SF_ID,
        CMS_ID::VARCHAR         AS CMS_ID,
        ZUORA_NAME::VARCHAR     AS ZUORA_NAME
    FROM THIRD_PARTY_RECON_PARTNER_MAP_PROD
    WHERE PARTNER_NAME IS NOT NULL
),
src AS (
    SELECT b.*
    FROM base_src b
    WHERE TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(b.partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) NOT IN (
        SELECT TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(m.partner_name), '[^a-z0-9]+', ' '), '\\s+', ' '))
        FROM manual_partner_overrides m
    )

    UNION ALL

    SELECT * FROM manual_partner_overrides
)
SELECT
    s.PARTNER_NAME,
    s.PARENT_COMPANY,
    COALESCE(r.canonical_sf_id, s.RAW_SF_ID) AS SF_ID,
    s.CMS_ID,
    s.ZUORA_NAME,
    s.RAW_SF_ID,
    IFF(r.old_sf_id IS NOT NULL, 'MERGED_ACCOUNT_MAP', 'SOURCE') AS SF_ID_SOURCE,
    r.merge_effective_ts,
    r.merge_effective_month
FROM src s
LEFT JOIN RECON_ACCOUNT_MERGE_RESOLVER r
  ON r.old_sf_id = s.RAW_SF_ID;

CREATE OR REPLACE TABLE RECON_PARTNER_MAP_MONTHLY AS
WITH month_spine AS (
    SELECT DATEADD('MONTH', SEQ4(), '2020-01-01'::DATE)::DATE AS billing_month
    FROM TABLE(GENERATOR(ROWCOUNT => 240))
)
SELECT
    m.billing_month,
    p.PARTNER_NAME,
    p.PARENT_COMPANY,
    CASE
        WHEN p.SF_ID_SOURCE = 'MERGED_ACCOUNT_MAP'
         AND p.merge_effective_month IS NOT NULL
         AND m.billing_month < p.merge_effective_month
            THEN p.RAW_SF_ID
        ELSE p.SF_ID
    END AS SF_ID,
    p.CMS_ID,
    p.ZUORA_NAME,
    p.RAW_SF_ID,
    p.SF_ID_SOURCE,
    p.merge_effective_ts,
    p.merge_effective_month
FROM RECON_PARTNER_MAP p
CROSS JOIN month_spine m;

-- -----------------------------------------------------------------------------
-- 2) SKU map  (source of truth: THIRD_PARTY_RECON_SKU_MAP_PROD)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE RECON_SKU_MAP AS
SELECT DISTINCT
    VENDOR::VARCHAR             AS VENDOR,
    VENDOR_PRODUCT::VARCHAR     AS VENDOR_PRODUCT,
    VENDOR_SKU::VARCHAR         AS VENDOR_SKU,
    CW_SKU::VARCHAR             AS CW_SKU,
    SKU_MATCH_KEY::VARCHAR      AS SKU_MATCH_KEY,
    MAPPING_NOTES::VARCHAR      AS MAPPING_NOTES,
    CONTRACT_COST_RATE::FLOAT   AS CONTRACT_COST_RATE,
    CW_RETAIL_RATE::FLOAT       AS CW_RETAIL_RATE
FROM THIRD_PARTY_RECON_SKU_MAP_PROD;

-- -----------------------------------------------------------------------------
-- NOTE: Vendor-specific _PARTNER_MAPPING_V5 and _SKU_MAP_V5 compat views have been
-- removed. All 9 Reconciliation_Script_Prod.sql files now reference RECON_PARTNER_MAP
-- and RECON_SKU_MAP directly. There are no per-vendor shim layers.
