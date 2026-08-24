# Handoff — Drive Automated Recon to Manual-Team Parity or Better

**Paste this whole file as the first message in the new chat window.**

Date this was written: 2026-08-23
Target for the new session: match or beat the manual reconciliation team's per-vendor parity on every one of the 9 in-scope vendors.

---

## What is already done (do NOT re-do it)

1. **Skeleton is complete.** All 9 vendors emit LIVE end-to-end. `VENDOR_FALLBACK = {}` in `scripts/_run_skeleton_pipeline.py`. There is no snapshot fallback anywhere.
2. **Schema is app-parity-locked.** `THIRD_PARTY_RECON_OUTPUT_PROD` = 101,938 rows / 9 vendors / 12 EXCEPTION_TYPE buckets / 45 cols. `THIRD_PARTY_RECON_SUMMARY` = 69 rows with 100% row-count parity vs OUTPUT_PROD per vendor. `DATA_LOAD_STATUS` populated (60 LOADED / 8 NOT_LOADED / 1 PARTIAL). Verified 2026-08-23 by `scripts/_verify_app_wiring.py`.
3. **App is wired.** `app/combined_recon_app.py` reads only from OUTPUT_PROD + SUMMARY. Every vendor and every EXCEPTION_TYPE bucket is rendering. Contract-pricing-comparison card is intentionally empty (user request — do NOT wire it).
4. **KeepIT phantom $182M is fixed** (`8e9ca2d`). `KEEPIT_PARTNER_CMS_CROSSWALK_V5` had 16K rows for 681 partner names causing up to 376x row fanout. Solved with `QUALIFY ROW_NUMBER()` dedupe in `partner_bridge`. KeepIT VENDOR_AMOUNT: $182M → $4.2M.
5. **Unified schema is clean.** Zero references to `_LEGACY_20260823`, `_SNAPSHOT_20260823`, or `THIRD_PARTY_STANDALONE_RECON_DETAIL__` in any vendor SQL. Verified by `scripts/_audit_architecture.py`.

## Ground truth for tools + environment

- **Repo:** `C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline`
- **GitHub:** https://github.com/NateFold28/Third_Party_Reconciliation_Production (latest: `e55e721`)
- **Snowflake:** role=`DEVELOPER`, warehouse=`REPORTING_WH`, database=`ANALYTICS_DEV`, schema=`DBT_NFOLD_TRANSFORMATION`
- **Python:** `C:\Users\Nate.Fold\projects\.venv\Scripts\python.exe`
- **Connection helper:** `TEMPLATES.Python.connection.get_snowflake_connection(role, warehouse, database, schema)` (kwargs-only). Every script must do `sys.path.insert(0, r"C:\Users\Nate.Fold\projects")` before importing.

## Current baseline (what you are starting from)

Per-vendor clear rate + exception $ (VENDOR_AMOUNT on non-Clear rows), from `logs/verify_app_wiring.txt`:

| Vendor | Rows | **Current Clear %** | Exception $ | Dominant exception bucket |
|---|---:|---:|---:|---|
| Proofpoint | 5,459 | 96.1% | $38K | Vendor Billing, No CW Billing |
| Bitdefender | 3,385 | 86.7% | $55K | CW Billing, No Vendor Billing |
| Acronis | 17,634 | 82.5% | $509K | Known Discount / Bundle |
| SentinelOne | 19,661 | 78.4% | $1.67M | API Usage Recorded, No CW Billing |
| Exium | 791 | 71.6% | $86K | Vendor SKU, No CW SKU |
| Auvik | 3,503 | 61.7% | $1.55M | CW Billing, No Vendor Billing (SKU map gap) |
| Webroot | 16,246 | 42.0% | $2.32M | Vendor Billing, Insufficient CW Billing |
| KeepIT | 21,787 | 28.4% | $1.78M | Unmapped Partner + Vendor-No-CW |
| ESET | 13,472 | 9.3% | $0 | zero-$ cluster (data-flow issue) |

**Overall clear rate: 54.1%. Overall exception $ mass: $8.4M.**

## The bar you are chasing

The manual reconciliation team's documented parity per vendor (from `cowork_output/output/ENGINEERING_MONTHLY_INGESTION_MANIFEST.md`, dated 2026-07-15):

| Vendor | **Manual-team parity target** | Meaning |
|---|---|---|
| Exium | **100.0% EXACT** | vendor total = CW total exactly |
| Auvik CW | **100.3% qty / 100.1% amt** | round-trip exact |
| Webroot CW | **100.3% qty / 100.1% amt** | round-trip exact |
| Acronis | **98.1% qty** | 2% variance ok |
| ESET | **98.4% qty / 101.2% amt** | multi-region normalized |
| SentinelOne | **101.1%** | API export used |
| Proofpoint | **95.4% qty** | NA + APAC normalized |
| Bitdefender | **93.3% qty** | scope-gap being closed by manual team |
| Auvik CMS | **94% amt** | CMS-side pattern-mapped |
| KeepIT | **complex/multi-product** | product-level SKU map |
| Webroot CMS | **100% amt / qty needs pkg normalization** | structural |

Manual team runs in aggregate: for each vendor, they compute (vendor_total_$ / cw_total_$) and target 95-101%. Our pipeline reports row-level classification (clear vs 11 exception buckets). **The equivalence you need to hit: exception $ / total $ ≤ 5% per vendor** (i.e. total dollar delta between vendor billings and CW billings within the manual team's parity band).

That translates to these concrete per-vendor targets for the automated pipeline:

| Vendor | Current Clear % | **New target Clear %** | Max acceptable exception $ |
|---|---:|---:|---:|
| Proofpoint | 96.1% | **≥ 96%** | ≤ $50K |
| Bitdefender | 86.7% | **≥ 93%** | ≤ $50K |
| Exium | 71.6% | **≥ 99%** | ≤ $10K |
| Acronis | 82.5% | **≥ 96%** | ≤ $150K |
| ESET | 9.3% | **≥ 95%** | resolve $0-mass first, then clear rate |
| SentinelOne | 78.4% | **≥ 95%** | ≤ $500K (API-usage bucket needs threshold tune) |
| Auvik | 61.7% | **≥ 95%** | ≤ $200K |
| KeepIT | 28.4% | **≥ 90%** | ≤ $250K |
| Webroot | 42.0% | **≥ 95%** | ≤ $300K |

## Attack order (biggest leverage first)

The `scripts/_audit_cw_sku_universe.py` output (`logs/audit_cw_sku_universe.txt`) shows the CW-side SKU catalog with revenue weighting and which SKUs are NOT in `RECON_SKU_MAP`. **Every SKU map fix is a direct clear-rate lift on both sides of the join.** Prioritized:

1. **Auvik — $30.0M unmapped ARR / 284 unmapped CW SKUs.**
   Top misses: `CMS-UMM-SAAS-RMM-UMM-SRMMANM` ($12.4M), `CULCSAS100710001A250` ($2.8M), `CULCSAS100708001A100` ($1.5M). Whole ConnectWise-RMM Network Monitoring family + Auvik CW usage-billed skus are absent.
2. **Bitdefender — $14.9M unmapped ARR / 170 unmapped CW SKUs.**
   Top misses: `DL17107A00D-EN-D` ATS&EDR family ($2.8M), Secure Extra/Plus, PHASR, Cloud Encryption, Patch Management. Clear rate is already 87% so this mostly reduces "CW SKU/No Vendor SKU" volume.
3. **SentinelOne — $8.6M unmapped ARR / 44 unmapped CW SKUs.**
   Top miss: `M2MSEROTHRFFEPSECPRM` CW MDR Premium ($6.5M) — one row. Also `TA-CW-MDR-SENTINELONE` ($1.2M), `CMS-S1-CYBR-SOLP-SAAS-IDDETRESP` ($470K).
4. **ESET — 2,903 unmapped CW SKUs / $1.9M ARR.**
   Long tail, but ESET's real problem is the $0 exception cluster: 13K rows classified as exceptions but with VENDOR_AMOUNT = 0. That is a data-flow defect in `Vendor_Recon_Pipelines_Prod/ESET/ESET_Reconciliation_Script_Prod.sql` (usage rows joining without price, most likely). Diagnose FIRST, then map.
5. **KeepIT partner-side.** `Unmapped Partner` bucket still $573K on 351 rows. Audit `RECON_PARTNER_MAP` for `KeepIT` vendor with `LEFT JOIN … WHERE cw_key IS NULL` against the partner names in `KEEPIT_RECON_DETAIL` exceptions.
6. **Webroot pricing calibration.** 42% clear rate, top bucket "Vendor Billing, Insufficient CW Billing" ($1.55M of the $2.32M). SKU map is complete. This is a unit-rate / discount calibration issue in the Webroot vendor SQL.
7. **Acronis Known-Discount/Bundle** ($509K, 82.5% clear) — small residue. Likely just a couple of Advanced Backup line-items where `NET_UNIT_PRICE` isn't being resolved.

## The playbook to hit target (repeat per vendor)

For each vendor in priority order:

```
# 1. Diagnose
python -u scripts\_diag_exception_buckets.py         # top 4 buckets per vendor + $
python -u scripts\_audit_cw_sku_universe.py          # top 10 unmapped CW SKUs per vendor
                                                     # + partner map spot-check via ad-hoc SQL

# 2. Add missing rows to the two seed tables (Snowflake INSERT statements or
#    a small Python loader):
#      - DBT_NFOLD_TRANSFORMATION.RECON_MANUAL_SEED_SKU_MAP
#      - DBT_NFOLD_TRANSFORMATION.RECON_MANUAL_SEED_PARTNER_MAP
#    Columns: VENDOR, VENDOR_SKU (or PARTNER_NAME/GUID), CW_SKU (or CMS_ID/SF_ID), REVIEW_FLAG, MAPPING_SOURCE

# 3. Re-bake the unified maps (idempotent, 30 sec):
#    Run sql/02_unified_reference_maps.sql in Snowsight (or via a small Python
#    runner that reads and executes it split on ;;).

# 4. Re-run the pipeline (all 9 vendors, ~3-4 min):
python -u scripts\_run_skeleton_pipeline.py

# 5. Re-verify:
python -u scripts\_verify_app_wiring.py
python -u scripts\_diag_exception_buckets.py

# 6. Commit ONE vendor at a time.
git add DBT_NFOLD_TRANSFORMATION_seeds/...   Vendor_Recon_Pipelines_Prod/<V>/...
git commit -m "<Vendor>: SKU/partner map coverage - clear rate X% -> Y%, exception $ -> $Z"
```

## Absolute do-nots (yesterday's constraints, still binding)

1. **Do NOT modify `scripts/build_third_party_recon_output_prod.py`.** The 12-bucket classifier is locked. Fix vendor SQL upstream if a bucket looks wrong.
2. **Do NOT re-introduce `VENDOR_FALLBACK` entries.** Live-only.
3. **Do NOT reference `_LEGACY_20260823`, `_SNAPSHOT_20260823`, or `THIRD_PARTY_STANDALONE_RECON_DETAIL__*`.** Compat views + unified maps only.
4. **Do NOT wire the contract-pricing-comparison card in the app.** User explicitly deferred it.
5. **Do NOT touch `THIRD_PARTY_RECON_OUTPUT_PROD` schema.** 45 cols is the app parity contract.
6. **Do NOT bulk-edit map tables.** Every seed addition must include a MAPPING_SOURCE audit trail so we can back it out.

## Snowflake gotcha (bit me twice yesterday)

Correlated `NOT IN (SELECT ...)` throws `Unsupported subquery type cannot be evaluated`. Always rewrite as `LEFT JOIN ... WHERE key IS NULL`. See `scripts/_audit_cw_sku_universe.py` for the pattern.

Correlated aggregates over VIEW-of-view chains sometimes fail with `SQL compilation error: unsupported ...`. In those cases, materialize the intermediate into a `TEMP TABLE` or CTE and re-join.

## Where things live

- **Vendor SQL** (edit-heavy): `Vendor_Recon_Pipelines_Prod/<VENDOR>/<VENDOR>_Reconciliation_Script_Prod.sql`. All 9 files. The KeepIT `QUALIFY ROW_NUMBER()` dedupe pattern is copy-pasteable to any other vendor showing partner fanout.
- **Manual seed tables** (edit-heavy): Snowflake `RECON_MANUAL_SEED_PARTNER_MAP`, `RECON_MANUAL_SEED_SKU_MAP`. Union'd into `RECON_PARTNER_MAP` / `RECON_SKU_MAP` by `sql/02_unified_reference_maps.sql`.
- **Compat views**: `sql/03_compat_dead_object_views.sql`. Idempotent. Add here if a vendor SQL needs to reference an object that got consolidated.
- **CW SKU catalog** (read-only, for prioritization): `analytics.dbo_transformation.seed__product_categorization` × `analytics.dbo.carr__all_transactions` × `analytics.dbo_base_salesforce.base_salesforce__product`.
- **Manual-team recon references** (read-only truth): `cowork_output/output/ENGINEERING_MONTHLY_INGESTION_MANIFEST.md`, `cowork_output/output/00_MASTER_REFERENCE_Third_Party_Recon_Mappings.md`, `cowork_output/output/GAP_CLOSURE_KEEPIT_AUVIK_KASEYA_2026-07-15.md`.
- **App**: `app/combined_recon_app.py`. Locked schema contract. Launch: `run_app.bat`.

## Runbook (copy-paste)

```powershell
cd C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline

# Full pipeline (~3-4 min, 9/9 LIVE)
..\..\..\.venv\Scripts\python.exe -u scripts\_run_skeleton_pipeline.py

# Sanity + audits (read-only)
..\..\..\.venv\Scripts\python.exe -u scripts\_verify_app_wiring.py
..\..\..\.venv\Scripts\python.exe -u scripts\_audit_architecture.py
..\..\..\.venv\Scripts\python.exe -u scripts\_diag_exception_buckets.py
..\..\..\.venv\Scripts\python.exe -u scripts\_audit_cw_sku_universe.py

# App
run_app.bat
```

## First actions for the new session (in this exact order)

1. Read this file end-to-end.
2. Read `README.md` at repo root.
3. Read `logs/verify_app_wiring.txt` (current-truth baseline).
4. Read `logs/audit_cw_sku_universe.txt` (top unmapped SKUs).
5. Read `cowork_output/output/ENGINEERING_MONTHLY_INGESTION_MANIFEST.md` for manual-team parity targets.
6. Ask the user: *"Confirm attack order: (1) Auvik SKU map [$30M gap], (2) Bitdefender SKU map [$15M gap], (3) SentinelOne SKU map [$8.6M gap + API-usage threshold tune], (4) ESET zero-dollar defect, (5) KeepIT partner tail, (6) Webroot pricing calibration, (7) Acronis discount residue. Or override?"*
7. Once confirmed, start with vendor #1. Do not batch — one vendor, one commit, one re-verify pass.

## Session memory to load first

- `/memories/repo/skeleton_complete_and_keepit_fix_2026_08_23.md`
- `/memories/repo/production_consolidation_complete_2026_08_20.md`
- `/memories/repo/unified_recon_schema_2026_08_19.md`
- `/memories/repo/hardcoding_audit_completion_2026_08_20.md`
- `/memories/repo/pipeline_architecture_tables_2026_08_20.md`
