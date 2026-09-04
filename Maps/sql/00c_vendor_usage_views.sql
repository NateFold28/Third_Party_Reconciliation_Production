-- =============================================================================
-- RETIRED: vendor-specific usage shim views
-- =============================================================================
-- Active vendor reconciliation scripts read THIRD_PARTY_RECON_VENDOR_USAGE_PROD
-- directly with an explicit VENDOR filter. These historical shim views are
-- dropped idempotently so no extra component sits between governed ingestion
-- output and vendor reconciliation logic.
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

DROP VIEW IF EXISTS ACRONIS_USAGE;
DROP VIEW IF EXISTS AUVIK_USAGE;
DROP VIEW IF EXISTS BITDEFENDER_USAGE;
DROP VIEW IF EXISTS ESET_USAGE;
DROP VIEW IF EXISTS EXIUM_USAGE;
DROP VIEW IF EXISTS KEEPIT_USAGE;
DROP VIEW IF EXISTS PROOFPOINT_USAGE;
DROP VIEW IF EXISTS SENTINELONE_USAGE;
DROP VIEW IF EXISTS WEBROOT_USAGE;
DROP VIEW IF EXISTS EXIUM_USAGE_RECON_COMPAT;

SELECT 1;
