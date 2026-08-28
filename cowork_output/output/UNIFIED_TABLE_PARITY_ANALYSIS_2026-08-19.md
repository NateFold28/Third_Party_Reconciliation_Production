# Unified Table Parity Analysis (2026-08-19)

## Scope
Assess whether vendor pipelines would preserve exact parity if they consumed unified source tables instead of vendor-native source tables.

## Baseline parity status (current production path)
Command run:
- python scripts/validate_combined_vs_individual_parity.py --vendors all

Result:
- PASS for all 9 vendors at detail + summary parity.
- Optional exceptions check is currently not available in this environment (table/column mismatch in validator assumptions), but this does not affect detail/summary parity status.

## Unified source construction behavior
From sql/00_unified_source_tables.sql:
- THIRD_PARTY_RECON_VENDOR_USAGE_PROD is built by UNION ALL SELECT * across vendor *_USAGE_PROD tables (no projection, no dedupe).
- THIRD_PARTY_RECON_SKU_MAP_PROD is built from vendor *_SKU_MAP_SEED_PROD and then deduped with QUALIFY ROW_NUMBER() over a key subset.
- THIRD_PARTY_RECON_PARTNER_MAP_PROD is built from vendor *_PARTNER_MAP_SEED_PROD and then deduped with QUALIFY ROW_NUMBER() over a key subset.

Implication:
- Usage should be row-preserving by construction.
- SKU map and partner map are not guaranteed row-preserving due dedupe and constrained column projection.

## Measured source-level mismatches (native vs unified vendor slice)
A source diff script was run against Snowflake to compare vendor-native source tables and unified vendor slices.

SKU map mismatches:
- PROOFPOINT: native_minus_unified=33, unified_minus_native=33 (row count equal at 33; rows differ after normalization).
- SENTINELONE: native_minus_unified=71, unified_minus_native=71 (row count equal at 78; rows differ materially).
- WEBROOT: native_minus_unified=38, unified_minus_native=38 (row count equal at 227; rows differ materially).
- BITDEFENDER: native_rows=64 vs unified_rows=63, native_minus_unified=55, unified_minus_native=54.
- ACRONIS, KEEPIT, AUVIK, ESET, EXIUM: no SKU seed set-diff detected.

Partner map mismatches:
- PROOFPOINT: native_rows=574 vs unified_rows=553 (21 rows dropped in unified).
- ACRONIS: native_rows=625 vs unified_rows=607 (18 rows dropped in unified).
- BITDEFENDER: native_rows=1203 vs unified_rows=1195 (8 rows dropped in unified).
- SENTINELONE, WEBROOT, KEEPIT, AUVIK, ESET, EXIUM: no partner seed set-diff detected.

## Pipeline dependency gaps (why direct switch is not parity-safe)
Several vendor pipelines still depend on vendor-specific curated objects and fields that are not direct reads of unified seed tables.

Examples:
- SentinelOne pipeline reads SENTINELONE_SKU_MAP_NORMALIZED and SENTINELONE_PARTNER_MAPPING_V5, including fields like vendor_invoice_unit_price and vendor_invoice_rate_source used in vendor_amount logic.
- KeepIT pipeline uses KEEPIT_USAGE and KEEPIT_SKU_MAP / KEEPIT_PARTNER_CMS_CROSSWALK_V5, not direct THIRD_PARTY_RECON_*_PROD sources.
- Webroot pipeline uses WEBROOT_USAGE and WEBROOT_SKU_MAP_V5 / WEBROOT_PARTNER_MAPPING_V5.

Because of these vendor-curated dependencies, changing only table names to unified tables would not preserve behavior.

## Parity verdict
Not parity-safe yet for an "exact" migration to unified-source-only inputs.

Current state:
- Combined output parity is green.
- Source-level parity is not exact for key vendors, especially SKU and partner map paths.
- Vendor-specific curated mapping logic still exists and is behavior-critical.

## What needs to change in unified tables (and wrappers) to reach exact parity
1) Preserve row fidelity for map tables
- Remove or relax current dedupe in unified SKU/partner map tables, or create parity-mode tables/views that keep all source rows.
- If dedupe remains, match vendor-specific dedupe rules exactly per vendor, not one generic global key.

2) Expand unified schemas to include vendor-critical fields
- Add any fields used by vendor logic but absent in unified seeds (for example invoice-price source fields used by SentinelOne rate derivation).
- Keep vendor-specific provenance columns so downstream rule logic remains deterministic.

3) Build vendor-compatible shim views over unified tables
- Create per-vendor compatibility views that reproduce each vendor pipeline's expected contracts (column names, canonicalization, and filtering semantics).
- Migrate pipelines to those views first, then replace internals progressively.

4) Run two-stage parity gate before cutover
- Stage A: source parity (native vs unified-view) by vendor and table type.
- Stage B: output parity by rerunning each vendor reconciliation with unified-view inputs and diffing RECON_DETAIL and RECON_SUMMARY.
- Require zero mismatches (or documented tolerance list) before production switch.

## Artifacts created in this session
- scripts/analyze_unified_source_parity.py
- scripts/report_unified_schema_gaps.py

These are helper analyzers for repeatable migration-parity checks.
