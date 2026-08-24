# Third Party Reconciliation - Production Pipeline

Authoritative production repository.

**Repo:** https://github.com/NateFold28/Third_Party_Reconciliation_Production

## Status (2026-08-23)

**Skeleton complete: all 9 vendors emit LIVE end-to-end.** No snapshot fallback. `THIRD_PARTY_RECON_OUTPUT_PROD` = 101,938 rows across 12 valid `EXCEPTION_TYPE` buckets. Schema matches the app expectation (45 cols).

Remaining work is per-vendor business-logic fine-tuning (see "Fine-tuning priorities" below).

## Vendors (9)
Acronis, Auvik, Bitdefender, ESET, Exium, KeepIT, Proofpoint, SentinelOne, Webroot.

## Architecture (current, verified 2026-08-23)

```
scripts/ingestion/<Vendor>_Vendor_Usage_Ingestion_Prod.py  x 9    (Excel/CSV in)
                       |
                       v
   <VENDOR>_USAGE                                             (per-vendor raw usage)
   THIRD_PARTY_RECON_VENDOR_USAGE_PROD                        (unified copy)
                       +
   sql/01_unified_billing_sources.sql
                       |
                       v
   THIRD_PARTY_RECON_SOURCE_ZUORA_PROD                        (unified Zuora billing)
   THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD                  (unified marketplace)
   THIRD_PARTY_RECON_SOURCE_TRT_PROD                          (unified TRT / API telemetry)
                       +
   sql/02_unified_reference_maps.sql
                       |
                       v
   RECON_PARTNER_MAP  (25,893 rows, union of 8 vendor _LEGACY_20260823 tables
                       + RECON_MANUAL_SEED_PARTNER_MAP)
   RECON_SKU_MAP      (1,223 rows, union of 7 vendor _LEGACY_20260823 tables
                       + RECON_MANUAL_SEED_SKU_MAP)
   <VENDOR>_PARTNER_MAPPING_V5     x 9 backward-compat views over RECON_PARTNER_MAP
   <VENDOR>_SKU_MAP_V5             x 9 backward-compat views over RECON_SKU_MAP
                       +
   sql/03_compat_dead_object_views.sql
                       |
                       v
   Extra vendor-specific compat views:
     SENTINELONE_CHARGE_TO_GROUP
     WEBROOT_TRT_USAGE_MONTHLY
     WEBROOT_TRT_ENDPOINT_RMM_DISCOUNT_MONTHLY
     EXIUM_PARTNER_MAPPING_V5 (enriched)
     EXIUM_SKU_MAP_V5 (enriched)
     EXIUM_USAGE_RECON_COMPAT
     EXIUM_CONTRACT_RATES
     EXIUM_BILLING_MATCHED
     EXIUM_MARKETPLACE_BILLING_MATCHED
                       |
                       v
   Vendor_Recon_Pipelines_Prod/<VENDOR>/<VENDOR>_Reconciliation_Script_Prod.sql  x 9
                       |
                       v
   <VENDOR>_RECON_DETAIL   x 9  (per-vendor canonical detail)
                       |
                       v  scripts/_run_skeleton_pipeline.py  STEP 1b: live_emit_block()
                       v
   THIRD_PARTY_RECON_DETAIL_PROD   (single unified detail table, 34-col canonical)
                       |
                       v  scripts/build_third_party_recon_output_prod.py  STEP 3
                       v  (12-bucket classifier)
   THIRD_PARTY_RECON_OUTPUT_PROD    +  THIRD_PARTY_RECON_SUMMARY
                       |
                       v
              app/combined_recon_app.py (Streamlit)
```

**Key architectural invariants:**
- Every vendor SQL file runs against the current live schema. **Zero** references to `_LEGACY_20260823`, `_SNAPSHOT_20260823`, or `THIRD_PARTY_STANDALONE_RECON_DETAIL__` tables anywhere (verified by `scripts/_audit_architecture.py`).
- `VENDOR_FALLBACK = {}` in `_run_skeleton_pipeline.py` so any live-path regression fails loudly instead of silently reverting to the 2026-08-23 snapshot.

## App-facing tables (parity-locked - do not modify without app changes)

| Table | Role |
|---|---|
| `THIRD_PARTY_RECON_OUTPUT_PROD` | Primary flat table the app reads (45 cols) |
| `THIRD_PARTY_RECON_SUMMARY` | Per-(vendor, month) aggregates + `DATA_LOAD_STATUS` |
| `THIRD_PARTY_RECON_DETAIL_PROD` | Row-level detail (drilldown, 34 cols) |
| `THIRD_PARTY_RECON_VENDOR_USAGE_PROD` | Ingested vendor usage |
| `RECON_PARTNER_MAP` | Unified partner mapping (25,893 rows) |
| `RECON_SKU_MAP` | Unified SKU mapping (1,223 rows) |

## Run

Prerequisites (idempotent, run any time the compat contract changes):
1. Apply `sql/02_unified_reference_maps.sql` to Snowflake.
2. Apply `sql/03_compat_dead_object_views.sql` to Snowflake.

Full pipeline:
```powershell
cd Combined_Recon_Prod_Pipeline
..\..\..\.venv\Scripts\python.exe scripts\_run_skeleton_pipeline.py
```

Launch app:
```powershell
run_app.bat
```

## Verified metrics (2026-08-23, post-KeepIT-fix)

- `THIRD_PARTY_RECON_OUTPUT_PROD`: **101,938 rows / 9 vendors LIVE / no fallback / 12 EXCEPTION_TYPE buckets / 45 cols** (matches app parity contract).
- `THIRD_PARTY_RECON_SUMMARY`: 69 rows, per-vendor row parity 100% vs OUTPUT_PROD (verified 2026-08-23 via `scripts/_verify_app_wiring.py`).
- `DATA_LOAD_STATUS`: 60 LOADED / 8 NOT_LOADED / 1 PARTIAL (per vendor-month).
- Overall clear rate: **54.1%** (55,135 / 101,938).
- Exception dollar mass (vendor_amount on non-Clear rows): **$8.4M total**.

### Per-vendor clear rate + exception $ impact

| Vendor | Rows | Clear % | Exception $ (VENDOR_AMOUNT) | Notes |
|---|---:|---:|---:|---|
| Proofpoint | 5,459 | 96.1% | $38K | production-ready |
| Bitdefender | 3,385 | 86.7% | $55K | production-ready |
| Acronis | 17,634 | 82.5% | $509K | small tuning left |
| SentinelOne | 19,661 | 78.4% | $1.67M | API-usage bucket dominates |
| Exium | 791 | 71.6% | $86K | small vendor |
| Auvik | 3,503 | 61.7% | $1.55M | SKU map gap |
| Webroot | 16,246 | 42.0% | $2.32M | pricing calibration |
| KeepIT | 21,787 | 28.4% | $1.78M | Unmapped Partner + Vendor-No-CW |
| ESET | 13,472 | 9.3% | $0 | zero-$ cluster / data flow |

### EXCEPTION_TYPE distribution

| Bucket | Rows | Vendor $ |
|---|---:|---:|
| Clear | 55,135 | $21.1M |
| CW Billing, No Vendor Billing | 14,510 | $0 |
| Known Discount / Bundle | 11,065 | $715K |
| Vendor Billing, No CW Billing | 7,208 | $2.60M |
| API Usage Recorded, No CW Billing | 5,651 | $1.65M |
| Duplicated CW Invoice | 4,734 | $580K |
| Vendor Billing, Insufficient CW Billing | 2,964 | $1.81M |
| Unmapped Partner | 351 | $573K |
| Marketplace Billing Delay | 224 | $40K |
| Vendor SKU, No CW SKU | 90 | $40K |
| Other Issue | 4 | $0 |
| CW SKU, No Vendor SKU | 2 | $0 |

## Fine-tuning priorities (2026-08-24 backlog)

Prioritized by CW-side SKU mapping gap (`scripts/_audit_cw_sku_universe.py`), not clear-rate:

| Vendor | Unmapped SKUs | Unmapped Annual ARR |
|---|---:|---:|
| **Auvik** | 284 | **$30.0M** |
| **Bitdefender** | 170 | **$14.9M** |
| **SentinelOne** | 44 | **$8.6M** |
| ESET | 2,903 | $1.9M |
| Acronis | 354 | $1.0M |
| Proofpoint | 48 | $11K |
| Exium | 8 | $0 |
| KeepIT | 0 | $0 |
| Webroot | 0 | $0 |

Auvik / Bitdefender / SentinelOne SKU map gaps are the single biggest lift available: add the missing rows to `RECON_MANUAL_SEED_SKU_MAP` and the clear rate + investigation queue should self-correct.

## Recent history

- **2026-08-23** — KeepIT partner fanout fix (`8e9ca2d`). `KEEPIT_PARTNER_CMS_CROSSWALK_V5` had 16K rows for 681 partner names, joining by name caused up to 376x row fanout. Fix added a `QUALIFY ROW_NUMBER()` dedupe in `partner_bridge` CTE. VENDOR_AMOUNT went $182M -> $4.2M. Clear rate 7.6% -> 28.4%.
- **2026-08-23** — Skeleton complete (`0739570`). All 9 vendors LIVE, fallback removed, per-vendor emit-block overrides for column-name differences (Auvik/Exium `VENDOR_PRODUCT`, Webroot `CW_SKUS`, KeepIT `MARKETPLACE_SKUS`). Compat views bridged 8 dead objects to the unified schema.
- **2026-08-23** — Unified reference maps consolidation (`732ef9e`). Manual seeds added: 17,622 partner rows + 55 SKU rows.

## Diagnostics / audits

Read-only helpers in `scripts/`:
- `_audit_architecture.py` — schema check + banned-table scan.
- `_diag_exception_buckets.py` — top exception per vendor + $ impact.
- `_diag_keepit_duplication.py` — partner-name fanout detector.
- `_audit_cw_sku_universe.py` — CW-side SKU coverage vs `RECON_SKU_MAP`.

## Key files

- `sql/01_unified_billing_sources.sql` — Zuora / Marketplace / TRT unification.
- `sql/02_unified_reference_maps.sql` — `RECON_PARTNER_MAP` + `RECON_SKU_MAP` build. Idempotent.
- `sql/03_compat_dead_object_views.sql` — compat views over the unified schema for objects vendor SQL still references. Idempotent.
- `scripts/_run_skeleton_pipeline.py` — orchestrator. Runs 9 vendor SQLs, emits into `DETAIL_PROD`, invokes classifier.
- `scripts/build_third_party_recon_output_prod.py` — **LOCKED** classifier. Do not modify.
