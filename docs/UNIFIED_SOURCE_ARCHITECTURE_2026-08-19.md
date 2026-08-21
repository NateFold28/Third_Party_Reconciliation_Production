# Unified Source Architecture (2026-08-19)

## Goal
Run all vendor reconciliation pipelines from one shared production source layer.

## Shared production source contracts

1. Usage + maps
- ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD
- ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_PARTNER_MAP_PROD
- ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SKU_MAP_PROD

2. Billing-source layer (shared by all vendor SQL)
- ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
- ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
- ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SOURCE_TRT_PROD
- ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SOURCE_ROYALTIES_PROD

## Execution path in production

1. scripts/run_combined_pipeline.py executes:
- sql/00_unified_source_tables.sql
- sql/01_unified_billing_sources.sql

2. Vendor pipelines execute vendor SQL against shared PROD sources:
- Proofpoint
- SentinelOne
- Webroot
- Acronis
- KeepIT
- Auvik
- Bitdefender
- ESET
- Exium

Vendor execution standard:
- Active production vendor SQL is `00_reference_maps.sql` plus `02_final_reconciliation.sql` and optional `03_trt_crosscheck.sql`.
- Standalone `01_billing_sources.sql` files are no longer part of the active production run path.
- Vendor-specific billing shaping, where still required, is embedded inside `02_final_reconciliation.sql` and reads only from the shared `THIRD_PARTY_RECON_SOURCE_*_PROD` tables.

3. Ingestion topology
- 8 ingestion scripts publish into THIRD_PARTY_RECON_VENDOR_USAGE_PROD.
- Bitdefender is ingestion-free (uses internal Snowflake source data).

## Validation status

- scripts/validate_combined_contract.py: PASS
- scripts/validate_combined_vs_individual_parity.py: PASS for all 9 vendors
- Latest all-vendor run command:
  - python scripts/run_combined_pipeline.py --vendors all --skip-ingest

## Cleanup posture

- Active pipelines are routed through shared PROD source tables only.
- Separate vendor billing-stage SQL has been retired from the active production path.
- Remaining vendor-specific SQL exists only for reference maps, reconciliation logic, and TRT validation where applicable.

