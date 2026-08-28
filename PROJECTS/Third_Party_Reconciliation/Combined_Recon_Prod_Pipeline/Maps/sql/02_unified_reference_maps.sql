-- =============================================================================
-- 02_unified_reference_maps.sql   (idempotent v2)
--
-- Builds:
--   RECON_PARTNER_MAP   (VENDOR, PARTNER_NAME, PARENT_COMPANY, SF_ID, CMS_ID, ZUORA_NAME)
--   RECON_SKU_MAP      (VENDOR, VENDOR_PRODUCT, VENDOR_SKU, CW_SKU, SKU_MATCH_KEY,
--                       MAPPING_NOTES, CONTRACT_COST_RATE, CW_RETAIL_RATE)
--
-- Sources unioned into RECON_PARTNER_MAP:
--   THIRD_PARTY_RECON_PARTNER_MAP_PROD            (production partner map source of truth)
--   RECON_MANUAL_SEED_PARTNER_MAP                 (manual curated additions)
--
-- Sources unioned into RECON_SKU_MAP:
--   THIRD_PARTY_RECON_SKU_MAP_PROD                (production SKU map source of truth)
--   RECON_MANUAL_SEED_SKU_MAP                     (manual curated additions)
--
-- No vendor-specific V5 compatibility views are emitted by this script.
-- Active reconciliation SQL consumes RECON_PARTNER_MAP and RECON_SKU_MAP directly.
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

CREATE TABLE IF NOT EXISTS RECON_VENDOR_PARTNER_MANUAL_MAP (
    VENDOR VARCHAR,
    PARTNER_NAME VARCHAR,
    SF_ID VARCHAR,
    CMS_ID VARCHAR,
    ZUORA_NAME VARCHAR,
    PARENT_COMPANY VARCHAR,
    SOURCE_TAG VARCHAR,
    UPDATED_AT TIMESTAMP_NTZ
);

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
        UNION ALL SELECT 'Advantech IT Solutions'::VARCHAR, NULL::VARCHAR, 'ACT-00133809'::VARCHAR, '27439'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Advanced Systems'::VARCHAR, NULL::VARCHAR, 'ACT-00195588'::VARCHAR, '21135'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Desert IT Solutions /1'::VARCHAR, NULL::VARCHAR, 'ACT-00057174'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Dexcore LLC'::VARCHAR, NULL::VARCHAR, 'ACT-00275427'::VARCHAR, '29568'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Pro4ia'::VARCHAR, NULL::VARCHAR, 'ACT-00203148'::VARCHAR, '18515'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Pro4ia, LLC'::VARCHAR, NULL::VARCHAR, 'ACT-00203148'::VARCHAR, '18515'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Sterling Technology Solutions'::VARCHAR, NULL::VARCHAR, 'ACT-00131379'::VARCHAR, '30165'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Resonant Technology Partners LLC'::VARCHAR, NULL::VARCHAR, 'ACT-00171734'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Netbox Managed IT Services'::VARCHAR, NULL::VARCHAR, 'ACT-00079016'::VARCHAR, '500000659'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'NETBOX'::VARCHAR, NULL::VARCHAR, 'ACT-00079016'::VARCHAR, '500000659'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Netbox Digital Ltd'::VARCHAR, NULL::VARCHAR, 'ACT-00079016'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'iDiscovery Solutions, Inc.'::VARCHAR, NULL::VARCHAR, 'ACT-00245864'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'iDiscovery Solutions, Inc'::VARCHAR, NULL::VARCHAR, 'ACT-00245864'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'VIcom Virginia Integrated Communications'::VARCHAR, NULL::VARCHAR, 'ACT-00187986'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Netsource One, Inc.'::VARCHAR, NULL::VARCHAR, 'ACT-00037128'::VARCHAR, '27795'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Netsource One Inc'::VARCHAR, NULL::VARCHAR, 'ACT-00037128'::VARCHAR, '27795'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Netsource One Inc /1'::VARCHAR, NULL::VARCHAR, 'ACT-00037128'::VARCHAR, '27795'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'N1 Discovery, LLC'::VARCHAR, NULL::VARCHAR, 'ACT-00146003'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'N1Discovery LLC'::VARCHAR, NULL::VARCHAR, 'ACT-00146003'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Flexxa di Andrea Monguzzi'::VARCHAR, NULL::VARCHAR, 'ACT-00098437'::VARCHAR, '500000459'::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'The Learning Exchange'::VARCHAR, NULL::VARCHAR, 'ACT-00239688'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Vermeer Heartland'::VARCHAR, NULL::VARCHAR, 'ACT-00239679'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'AMEOT'::VARCHAR, NULL::VARCHAR, 'ACT-00275831'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'US_Master AMEOT Disabled ACT-00275831'::VARCHAR, NULL::VARCHAR, 'ACT-00275831'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Medicus IT'::VARCHAR, NULL::VARCHAR, 'ACT-00095260'::VARCHAR, '20308'::VARCHAR, 'Medicus IT LLC'::VARCHAR
        UNION ALL SELECT 'MEDICUSIT'::VARCHAR, NULL::VARCHAR, 'ACT-00095260'::VARCHAR, '20308'::VARCHAR, 'Medicus IT LLC'::VARCHAR
        UNION ALL SELECT 'Medicus IT LLC'::VARCHAR, NULL::VARCHAR, 'ACT-00095260'::VARCHAR, '20308'::VARCHAR, 'Medicus IT LLC'::VARCHAR
        UNION ALL SELECT 'PDS LLC'::VARCHAR, NULL::VARCHAR, 'ACT-00058353'::VARCHAR, '23066'::VARCHAR, 'PDS Consulting'::VARCHAR
        UNION ALL SELECT 'PDS Consulting'::VARCHAR, NULL::VARCHAR, 'ACT-00058353'::VARCHAR, '23066'::VARCHAR, 'PDS Consulting'::VARCHAR
        UNION ALL SELECT 'PDS Consulting LLC'::VARCHAR, NULL::VARCHAR, 'ACT-00058353'::VARCHAR, '23066'::VARCHAR, 'PDS Consulting'::VARCHAR
        UNION ALL SELECT 'PDSCONSULTING'::VARCHAR, NULL::VARCHAR, 'ACT-00058353'::VARCHAR, '23066'::VARCHAR, 'PDS Consulting'::VARCHAR
        UNION ALL SELECT 'G G Computer Inc'::VARCHAR, NULL::VARCHAR, 'ACT-00240157'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Pro Per IT'::VARCHAR, NULL::VARCHAR, 'ACT-00006386'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Pro Per IT LLC'::VARCHAR, NULL::VARCHAR, 'ACT-00006386'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Advantech IT Olution'::VARCHAR, NULL::VARCHAR, 'ACT-00133809'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Advanced Y Tem'::VARCHAR, NULL::VARCHAR, 'ACT-00195588'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'De Ert IT Olution 1'::VARCHAR, NULL::VARCHAR, 'ACT-00057174'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Terling Technology Olution'::VARCHAR, NULL::VARCHAR, 'ACT-00131379'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Re Onant Technology Partner LLC'::VARCHAR, NULL::VARCHAR, 'ACT-00171734'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Resolution IT'::VARCHAR, NULL::VARCHAR, 'ACT-00020131'::VARCHAR, NULL::VARCHAR, NULL::VARCHAR
        UNION ALL SELECT 'Founders Innovative Technology'::VARCHAR, NULL::VARCHAR, 'ACT-00257034'::VARCHAR, '27653'::VARCHAR, 'Founders Innovative Technology (FIT)'::VARCHAR
        UNION ALL SELECT 'Founders IT Group'::VARCHAR, NULL::VARCHAR, 'ACT-00431740'::VARCHAR, '31329'::VARCHAR, 'Founders IT Group'::VARCHAR
        UNION ALL SELECT 'Meritech'::VARCHAR, NULL::VARCHAR, 'ACT-00245304'::VARCHAR, '15302'::VARCHAR, 'DEX Imaging (formerly North American)'::VARCHAR
        UNION ALL SELECT 'Merit Technologies - Customer Management'::VARCHAR, NULL::VARCHAR, 'ACT-00057478'::VARCHAR, '19653'::VARCHAR, 'Merit Technologies, LLC'::VARCHAR
    ) o
),
manual_child_sfid_lock AS (
    SELECT TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS pn_norm
    FROM (
        SELECT 'Advantech IT Solutions'::VARCHAR AS partner_name
        UNION ALL SELECT 'Advanced Systems'::VARCHAR
        UNION ALL SELECT 'Desert IT Solutions /1'::VARCHAR
        UNION ALL SELECT 'Dexcore LLC'::VARCHAR
        UNION ALL SELECT 'Pro4ia'::VARCHAR
        UNION ALL SELECT 'Sterling Technology Solutions'::VARCHAR
        UNION ALL SELECT 'Resonant Technology Partners LLC'::VARCHAR
        UNION ALL SELECT 'F8 Tech LLC'::VARCHAR
        UNION ALL SELECT 'GSG Computers, Inc.'::VARCHAR
        UNION ALL SELECT 'In-Telecom'::VARCHAR
        UNION ALL SELECT 'Lietz Development'::VARCHAR
        UNION ALL SELECT 'Prosper IT'::VARCHAR
        UNION ALL SELECT 'Prosper IT, LLC'::VARCHAR
        UNION ALL SELECT 'Vulcan Business Solutions LLC'::VARCHAR
        UNION ALL SELECT 'Wired! Technology Partners'::VARCHAR
        UNION ALL SELECT 'Netbox Managed IT Services'::VARCHAR
        UNION ALL SELECT 'NETBOX'::VARCHAR
        UNION ALL SELECT 'Netbox Digital Ltd'::VARCHAR
        UNION ALL SELECT 'iDiscovery Solutions, Inc.'::VARCHAR
        UNION ALL SELECT 'iDiscovery Solutions, Inc'::VARCHAR
        UNION ALL SELECT 'VIcom Virginia Integrated Communications'::VARCHAR
        UNION ALL SELECT 'Netsource One, Inc.'::VARCHAR
        UNION ALL SELECT 'Netsource One Inc'::VARCHAR
        UNION ALL SELECT 'Netsource One Inc /1'::VARCHAR
        UNION ALL SELECT 'N1 Discovery, LLC'::VARCHAR
        UNION ALL SELECT 'N1Discovery LLC'::VARCHAR
        UNION ALL SELECT 'Flexxa di Andrea Monguzzi'::VARCHAR
        UNION ALL SELECT 'The Learning Exchange'::VARCHAR
        UNION ALL SELECT 'Vermeer Heartland'::VARCHAR
        UNION ALL SELECT 'AMEOT'::VARCHAR
        UNION ALL SELECT 'US_Master AMEOT Disabled ACT-00275831'::VARCHAR
        UNION ALL SELECT 'Medicus IT'::VARCHAR
        UNION ALL SELECT 'MEDICUSIT'::VARCHAR
        UNION ALL SELECT 'Medicus IT LLC'::VARCHAR
        UNION ALL SELECT 'PDS LLC'::VARCHAR
        UNION ALL SELECT 'PDS Consulting'::VARCHAR
        UNION ALL SELECT 'PDS Consulting LLC'::VARCHAR
        UNION ALL SELECT 'PDSCONSULTING'::VARCHAR
        UNION ALL SELECT 'G G Computer Inc'::VARCHAR
        UNION ALL SELECT 'Pro Per IT'::VARCHAR
        UNION ALL SELECT 'Pro Per IT LLC'::VARCHAR
        UNION ALL SELECT 'Advantech IT Olution'::VARCHAR
        UNION ALL SELECT 'Advanced Y Tem'::VARCHAR
        UNION ALL SELECT 'De Ert IT Olution 1'::VARCHAR
        UNION ALL SELECT 'Terling Technology Olution'::VARCHAR
        UNION ALL SELECT 'Re Onant Technology Partner LLC'::VARCHAR
        UNION ALL SELECT 'Resolution IT'::VARCHAR
        UNION ALL SELECT 'Founders Innovative Technology'::VARCHAR
        UNION ALL SELECT 'Founders IT Group'::VARCHAR
        UNION ALL SELECT 'Meritech'::VARCHAR
        UNION ALL SELECT 'Merit Technologies - Customer Management'::VARCHAR
    )
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
    SELECT
        b.PARTNER_NAME,
        b.PARENT_COMPANY,
        b.RAW_SF_ID,
        b.CMS_ID,
        b.ZUORA_NAME,
        1 AS source_priority
    FROM base_src b
    WHERE TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(b.partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) NOT IN (
        SELECT TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(m.partner_name), '[^a-z0-9]+', ' '), '\\s+', ' '))
        FROM manual_partner_overrides m
    )

    UNION ALL

    SELECT
        m.PARTNER_NAME,
        m.PARENT_COMPANY,
        m.RAW_SF_ID,
        m.CMS_ID,
        m.ZUORA_NAME,
        0 AS source_priority
    FROM manual_partner_overrides m
),
resolved_candidates AS (
    SELECT
        s.PARTNER_NAME,
        s.PARENT_COMPANY,
        CASE
            WHEN TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(s.partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) IN (SELECT pn_norm FROM manual_child_sfid_lock)
                THEN s.RAW_SF_ID
            ELSE COALESCE(r.canonical_sf_id, s.RAW_SF_ID)
        END AS SF_ID,
        s.CMS_ID,
        s.ZUORA_NAME,
        s.RAW_SF_ID,
        CASE
            WHEN TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(s.partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) IN (SELECT pn_norm FROM manual_child_sfid_lock)
                THEN 'MANUAL_CHILD_LOCK'
            WHEN r.old_sf_id IS NOT NULL
                THEN 'MERGED_ACCOUNT_MAP'
            ELSE 'SOURCE'
        END AS SF_ID_SOURCE,
        r.merge_effective_ts,
        r.merge_effective_month,
        s.source_priority,
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(s.partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS pn_norm,
        IFF(s.CMS_ID IS NULL OR TRIM(s.CMS_ID) IN ('', '-'), 0, 1) AS has_cms_id,
        IFF(s.ZUORA_NAME IS NULL OR TRIM(s.ZUORA_NAME) IN ('', '-'), 0, 1) AS has_zuora_name
    FROM src s
    LEFT JOIN RECON_ACCOUNT_MERGE_RESOLVER r
      ON r.old_sf_id = s.RAW_SF_ID
)
SELECT
    PARTNER_NAME,
    PARENT_COMPANY,
    SF_ID,
    CMS_ID,
    ZUORA_NAME,
    RAW_SF_ID,
    SF_ID_SOURCE,
    merge_effective_ts,
    merge_effective_month
FROM resolved_candidates
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY pn_norm
    ORDER BY source_priority ASC,
             has_cms_id DESC,
             has_zuora_name DESC,
             PARTNER_NAME,
             SF_ID
) = 1;

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
WITH sku_map_seed AS (
    SELECT DISTINCT
        VENDOR::VARCHAR             AS VENDOR,
        VENDOR_PRODUCT::VARCHAR     AS VENDOR_PRODUCT,
        VENDOR_SKU::VARCHAR         AS VENDOR_SKU,
        CW_SKU::VARCHAR             AS CW_SKU,
        SKU_MATCH_KEY::VARCHAR      AS SKU_MATCH_KEY,
        MAPPING_NOTES::VARCHAR      AS MAPPING_NOTES,
        CONTRACT_COST_RATE::FLOAT   AS CONTRACT_COST_RATE,
        CW_RETAIL_RATE::FLOAT       AS CW_RETAIL_RATE
    FROM THIRD_PARTY_RECON_SKU_MAP_PROD
)
SELECT
    VENDOR,
    VENDOR_PRODUCT,
    VENDOR_SKU,
    CASE
        WHEN VENDOR = 'Acronis' THEN
            CASE
                -- Explicit bad values observed in production seed exports.
                WHEN TRIM(COALESCE(CW_SKU, '')) = '' THEN 'UNMATCHED'
                WHEN REGEXP_LIKE(TRIM(CW_SKU), '^[0-9]+(\.[0-9]+)?$') THEN 'UNMATCHED'
                WHEN UPPER(TRIM(CW_SKU)) IN ('ST5AMSENS') THEN 'UNMATCHED'
                WHEN UPPER(TRIM(COALESCE(SKU_MATCH_KEY, ''))) = 'SPEAMSENS'
                     AND UPPER(TRIM(CW_SKU)) IN ('SPFAMSENS', 'SPGAMSENS') THEN 'UNMATCHED'
                WHEN UPPER(TRIM(COALESCE(SKU_MATCH_KEY, ''))) = 'SRIAMSENS'
                     AND UPPER(TRIM(CW_SKU)) = 'SP4BMSENS' THEN 'UNMATCHED'
                WHEN UPPER(TRIM(COALESCE(SKU_MATCH_KEY, ''))) = 'SPGAMSENS'
                     AND UPPER(TRIM(CW_SKU)) = 'SP4BMSENS' THEN 'UNMATCHED'
                WHEN UPPER(TRIM(COALESCE(SKU_MATCH_KEY, ''))) = 'SPDAMSENS'
                     AND UPPER(TRIM(CW_SKU)) = 'SPIAMSENS' THEN 'UNMATCHED'
                ELSE CW_SKU
            END
        ELSE CW_SKU
    END AS CW_SKU,
    SKU_MATCH_KEY,
    MAPPING_NOTES,
    CONTRACT_COST_RATE,
    CW_RETAIL_RATE
FROM sku_map_seed;

-- -----------------------------------------------------------------------------
-- NOTE: Vendor-specific _PARTNER_MAPPING_V5 and _SKU_MAP_V5 compat views have been
-- removed. All 9 Reconciliation_Script_Prod.sql files now reference RECON_PARTNER_MAP
-- and RECON_SKU_MAP directly. There are no per-vendor shim layers.
