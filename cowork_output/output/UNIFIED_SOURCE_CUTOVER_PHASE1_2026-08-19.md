# Unified Source Cutover Phase 1 (2026-08-19)

## Objective
Start cutover to a singular shared source model while preserving exact combined-vs-individual output parity.

## Changes applied
- Updated shared source builder in sql/00_unified_source_tables.sql:
  - THIRD_PARTY_RECON_SKU_MAP_PROD now uses UNION ALL only (no dedupe).
  - THIRD_PARTY_RECON_PARTNER_MAP_PROD now uses UNION ALL only (no dedupe).
  - THIRD_PARTY_RECON_VENDOR_USAGE_PROD now unions from the current ingestion writer tables:
    - PROOFPOINT_USAGE, SENTINELONE_USAGE, ACRONIS_USAGE, KEEPIT_USAGE, WEBROOT_USAGE, AUVIK_USAGE, ESET_USAGE
    - EXIUM_USAGE_PROD, BITDEFENDER_USAGE_PROD
- Added compatibility fanout in the same SQL so vendor pipeline source contracts remain stable while shared tables act as source-of-truth inputs:
  - Rebuilds *_USAGE_PROD, *_SKU_MAP_SEED_PROD, *_PARTNER_MAP_SEED_PROD from shared tables by vendor slice.
  - Rebuilds legacy *_USAGE tables (except BITDEFENDER_USAGE which remains a view in current design) from shared usage by vendor slice.

## Validation run
Command:
- python scripts/run_combined_pipeline.py --vendors all --skip-ingest

Result:
- Full run completed successfully.
- Combined contract validation passed.
- Combined-vs-individual parity validation passed for all 9 vendors.

## Important observation
- KeepIT parity pass now reports months 2026-05 through 2026-07 in this run context.
- This is still parity-consistent in-run (combined equals individual for the same source state), but historical month coverage should be confirmed before final production cutover.

## Current state after Phase 1
- Yes, the shared tables can now drive the vendor pipelines through compatibility fanout.
- Yes, exact parity check passes in this configuration.
- No, ingestion scripts are not yet writing directly into THIRD_PARTY_RECON_VENDOR_USAGE_PROD.
- No, vendor usage table retirement has not been performed yet.

## Phase 2 recommendation (direct-ingestion cutover)
1. Add a shared ingestion writer utility that upserts each vendor payload directly into THIRD_PARTY_RECON_VENDOR_USAGE_PROD using vendor + billing month keys.
2. Patch each ingestion script to publish to shared usage (and optionally keep a compatibility output switch during transition).
3. Replace physical vendor usage tables with vendor-filtered views over shared usage.
4. Re-run full parity and month-coverage validation before dropping legacy vendor usage tables.
