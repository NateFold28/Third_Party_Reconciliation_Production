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
-- Compat views add a static VENDOR column so vendor SQL files work unchanged.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE RECON_PARTNER_MAP AS
SELECT DISTINCT
    PARTNER_NAME::VARCHAR   AS PARTNER_NAME,
    PARENT_COMPANY::VARCHAR AS PARENT_COMPANY,
    SF_ID::VARCHAR          AS SF_ID,
    CMS_ID::VARCHAR         AS CMS_ID,
    ZUORA_NAME::VARCHAR     AS ZUORA_NAME
FROM THIRD_PARTY_RECON_PARTNER_MAP_PROD
WHERE PARTNER_NAME IS NOT NULL;

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
-- 3) Backward-compat views: one per vendor.
--    PARTNER_MAPPING_V5 views add a static VENDOR column so the vendor
--    Reconciliation_Script_Prod.sql files continue to work unchanged.
--    SKU_MAP_V5 views filter the unified SKU map by VENDOR as before.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW ACRONIS_PARTNER_MAPPING_V5     AS SELECT 'Acronis'::VARCHAR AS VENDOR, * FROM RECON_PARTNER_MAP;
CREATE OR REPLACE VIEW AUVIK_PARTNER_MAPPING_V5       AS SELECT 'Auvik'::VARCHAR AS VENDOR, * FROM RECON_PARTNER_MAP;
CREATE OR REPLACE VIEW BITDEFENDER_PARTNER_MAPPING_V5 AS SELECT 'Bitdefender'::VARCHAR AS VENDOR, * FROM RECON_PARTNER_MAP;
CREATE OR REPLACE VIEW ESET_PARTNER_MAPPING_V5        AS SELECT 'ESET'::VARCHAR AS VENDOR, * FROM RECON_PARTNER_MAP;
CREATE OR REPLACE VIEW EXIUM_PARTNER_MAPPING_V5       AS SELECT 'Exium'::VARCHAR AS VENDOR, * FROM RECON_PARTNER_MAP;
CREATE OR REPLACE VIEW KEEPIT_PARTNER_MAPPING_V5      AS SELECT 'KeepIT'::VARCHAR AS VENDOR, * FROM RECON_PARTNER_MAP;
CREATE OR REPLACE VIEW PROOFPOINT_PARTNER_MAPPING_V5  AS SELECT 'Proofpoint'::VARCHAR AS VENDOR, * FROM RECON_PARTNER_MAP;
CREATE OR REPLACE VIEW SENTINELONE_PARTNER_MAPPING_V5 AS SELECT 'SentinelOne'::VARCHAR AS VENDOR, * FROM RECON_PARTNER_MAP;
CREATE OR REPLACE VIEW WEBROOT_PARTNER_MAPPING_V5     AS SELECT 'Webroot'::VARCHAR AS VENDOR, * FROM RECON_PARTNER_MAP;

CREATE OR REPLACE VIEW ACRONIS_SKU_MAP_V5     AS SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Acronis';
CREATE OR REPLACE VIEW AUVIK_SKU_MAP_V5       AS SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Auvik';
CREATE OR REPLACE VIEW BITDEFENDER_SKU_MAP_V5 AS SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Bitdefender';
CREATE OR REPLACE VIEW ESET_SKU_MAP_V5        AS SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'ESET';
CREATE OR REPLACE VIEW EXIUM_SKU_MAP_V5       AS SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Exium';
CREATE OR REPLACE VIEW KEEPIT_SKU_MAP_V5      AS SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'KeepIT';
CREATE OR REPLACE VIEW PROOFPOINT_SKU_MAP_V5  AS SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Proofpoint';
CREATE OR REPLACE VIEW SENTINELONE_SKU_MAP_V5 AS SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'SentinelOne';
CREATE OR REPLACE VIEW WEBROOT_SKU_MAP_V5     AS SELECT * FROM RECON_SKU_MAP WHERE VENDOR = 'Webroot';
