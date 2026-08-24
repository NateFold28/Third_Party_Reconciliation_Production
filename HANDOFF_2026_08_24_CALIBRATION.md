# Handoff — Per-Vendor Recon Calibration to Manual-Team Parity

**Paste this file as the first message of the new chat window.**

Written: 2026-08-23 (end of day). Latest commit before this handoff: `git log --oneline -1` in the repo.
Goal for the next session: **hit or beat manual-team parity on every one of the 9 vendors, one vendor per work-block.**

---

## 0. What the ground truth is right now

`THIRD_PARTY_RECON_OUTPUT_PROD` (101,938 rows, 9 vendors, 12 buckets, 45 cols) drives the app.
`THIRD_PARTY_RECON_SUMMARY_PROD` (69 rows) is the per-vendor-month rollup. **The old non-`_PROD` `THIRD_PARTY_RECON_SUMMARY` has been dropped.** Do not reintroduce it.

Current parity vs the manual team's published targets (from `cowork_output/output/ENGINEERING_MONTHLY_INGESTION_MANIFEST.md`):

| # | Vendor | Clear % | Vendor QTY | CW QTY | **Abs QTY Δ** | QTY parity | Vendor $ | CW $ | **Abs $ Δ** | $ parity | Manual QTY target | Priority |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | Proofpoint | 96.1% | 2,365,233 | 2,351,478 | 39,681 | 100.6% | $3,648K | $4,976K | $1,408K | 73.3% | 95.4% | **BEAT** — refine $ side |
| 2 | Bitdefender | 86.7% | 2,469,057 | 2,449,716 | 529,679 | 100.8% | $1,391K | $3,087K | $1,782K | 45.1% | 93.3% | **BEAT** — huge $ gap remains |
| 3 | Acronis | 82.5% | 95.5M | 79.8M | 32.9M | 119.8% | $3,202K | $5,886K | $3,840K | 54.4% | 98.1% | close QTY, close $ |
| 4 | Exium | 71.6% | 40,383 | 30,108 | 70,491 | 134.1% | $262K | $278K | $540K | 94.1% | 100% exact | small vendor, quick fix |
| 5 | Auvik | 61.7% | 702,589 | 568,417 | 417,502 | 123.6% | $3,781K | $4,267K | $3,192K | 88.6% | 100.3% | **SKU map $30M gap** |
| 6 | SentinelOne | 78.4% | 12.8M | 10.0M | 3.76M | 128.4% | $10,169K | $34,453K | $28,508K | 29.5% | 101.1% | API-usage bucket + $ side |
| 7 | Webroot | 42.0% | 7.63M | 2.94M | 5.61M | **259.6%** | $3,978K | $2,936K | $2,565K | **135.5%** | 100.3% | double-counting |
| 8 | KeepIT | 28.4% | 3.39M | 4.38M | 5.85M | 77.5% | $2,677K | $5,062K | $5,981K | 52.9% | complex | product-level SKU work |
| 9 | ESET | 9.3% | 117.3M | 4.11M | 114.1M | **2854.3%** | **$0** | $5,172K | **$0** | 0.0% | 98.4% | **broken: vendor $ is 0, qty exploding** |

**Overall today:** 54.1% clear, vendor $29.1M vs CW $66.1M (44.0% $ parity, target ~100%). Vendor QTY 242.2M vs CW QTY 106.6M (227% — driven by ESET's qty explosion + Webroot double-count).

## 1. The exact architecture (unchanged, but here it is again for context)

```
┌───────────────────────────────────────────────────────────────────────┐
│ INGEST (Python, one script per vendor)                                │
│ scripts/ingestion/<Vendor>_Vendor_Usage_Ingestion_Prod.py x 9         │
│   Excel/CSV → <VENDOR>_USAGE_PROD (raw) → <VENDOR>_USAGE (compat view)│
│   All 9 also union into THIRD_PARTY_RECON_VENDOR_USAGE_PROD           │
└───────────────────────────────────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────────┐
│ UNIFY BILLING SOURCES                                                 │
│ sql/01_unified_billing_sources.sql (idempotent, run any time)         │
│   → THIRD_PARTY_RECON_SOURCE_ZUORA_PROD          (77,341)             │
│   → THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD    (80,947)             │
│   → THIRD_PARTY_RECON_SOURCE_TRT_PROD            (27,119)             │
│   → THIRD_PARTY_RECON_SOURCE_ROYALTIES_PROD      (115,695)            │
└───────────────────────────────────┬───────────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────────┐
│ UNIFY REFERENCE MAPS                                                  │
│ sql/02_unified_reference_maps.sql (idempotent)                        │
│   base: RECON_PARTNER_MAP  (25,893 rows)                              │
│         RECON_SKU_MAP      (1,223 rows)                               │
│   built from:                                                         │
│     - RECON_MANUAL_SEED_PARTNER_MAP (17,622)  ◄─ where you add rows   │
│     - RECON_MANUAL_SEED_SKU_MAP     (55)      ◄─ where you add rows   │
│     - per-vendor _LEGACY_20260823 partner + sku tables (historical)   │
│   compat views: <V>_PARTNER_MAPPING_V5, <V>_SKU_MAP_V5 (x 18)         │
└───────────────────────────────────┬───────────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────────┐
│ COMPAT VIEWS FOR DEAD OBJECTS                                         │
│ sql/03_compat_dead_object_views.sql (idempotent)                      │
│   SENTINELONE_CHARGE_TO_GROUP, WEBROOT_TRT_USAGE_MONTHLY,             │
│   EXIUM_USAGE_RECON_COMPAT, EXIUM_CONTRACT_RATES, ...                 │
└───────────────────────────────────┬───────────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────────┐
│ VENDOR RECON (per-vendor SQL, this is where you'll spend tomorrow)    │
│ Vendor_Recon_Pipelines_Prod/<VENDOR>/<VENDOR>_Reconciliation_Script_Prod.sql │
│                                                                       │
│   INPUTS   : <V>_USAGE, sources_*_PROD, <V>_PARTNER_MAPPING_V5,       │
│              <V>_SKU_MAP_V5, THIRD_PARTY_RECON_VENDOR_INVOICES        │
│                                                                       │
│   OUTPUT   : <VENDOR>_RECON_DETAIL   (per-vendor canonical, ~34 cols) │
│              (KeepIT also emits KEEPIT_VENDOR_USAGE_MASTER)           │
│                                                                       │
│   x 9 files: Acronis, Auvik, Bitdefender, ESET, Exium, KeepIT,        │
│              Proofpoint, SentinelOne, Webroot                         │
└───────────────────────────────────┬───────────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────────┐
│ ORCHESTRATION + EMIT                                                  │
│ scripts/_run_skeleton_pipeline.py                                     │
│   STEP 1a: runs each vendor SQL file (live, no fallback)              │
│   STEP 1b: live_emit_block() maps each <V>_RECON_DETAIL to the        │
│            canonical 34-col shape, appends to DETAIL_PROD             │
│            (per-vendor column overrides for Auvik/Exium/Webroot/KeepIT│
│             where local column names differ)                          │
│   STEP 2 : re-materialize THIRD_PARTY_RECON_DETAIL_PROD (102,050 rows)│
│   STEP 3 : invoke build_third_party_recon_output_prod.py              │
└───────────────────────────────────┬───────────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────────┐
│ CLASSIFIER (LOCKED — do not modify)                                   │
│ scripts/build_third_party_recon_output_prod.py                        │
│   reads THIRD_PARTY_RECON_DETAIL_PROD                                 │
│   → THIRD_PARTY_RECON_OUTPUT_PROD  (101,938 / 45 cols / 12 buckets)   │
│   → THIRD_PARTY_RECON_SUMMARY_PROD (69 rows / DATA_LOAD_STATUS)       │
└───────────────────────────────────┬───────────────────────────────────┘
                                    │
┌───────────────────────────────────▼───────────────────────────────────┐
│ APP                                                                   │
│ app/combined_recon_app.py (Streamlit)                                 │
│   reads ONLY: THIRD_PARTY_RECON_OUTPUT_PROD, THIRD_PARTY_RECON_SUMMARY_PROD │
│   launch: run_app.bat                                                 │
└───────────────────────────────────────────────────────────────────────┘
```

## 2. Environment

- Repo: `C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline`
- Snowflake: `role=DEVELOPER, warehouse=REPORTING_WH, database=ANALYTICS_DEV, schema=DBT_NFOLD_TRANSFORMATION`
- Python: `C:\Users\Nate.Fold\projects\.venv\Scripts\python.exe`
- Connection helper: `TEMPLATES.Python.connection.get_snowflake_connection(role, warehouse, database, schema)` (kwargs-only). Every script starts with `sys.path.insert(0, r"C:\Users\Nate.Fold\projects")` before importing.

## 3. Absolute do-nots (unchanged)

1. **Do NOT modify `scripts/build_third_party_recon_output_prod.py`.** Classifier is locked.
2. **Do NOT reintroduce `VENDOR_FALLBACK` entries.** Live-only.
3. **Do NOT reference `_LEGACY_20260823`, `_SNAPSHOT_20260823`, or `THIRD_PARTY_STANDALONE_RECON_DETAIL__*`** in any new SQL. The `_LEGACY_20260823` partner/sku tables are OK as sql/02 inputs but nowhere else.
4. **Do NOT touch `THIRD_PARTY_RECON_OUTPUT_PROD` schema.** 45 cols is app parity contract.
5. **Do NOT wire the contract-pricing-comparison card in the app.** Deferred.
6. **Do NOT recreate `THIRD_PARTY_RECON_SUMMARY` (no `_PROD`).** It's been dropped intentionally.

## 4. Priority-0 hygiene task (30 minutes, do FIRST)

The naming audit (`logs/audit_naming_and_parity.txt`) found **204 pipeline-related objects without `_PROD` suffix** vs 37 with. Most are legacy / seed / diagnostic tables that can stay, but these 9 core pipeline outputs should be renamed to `_PROD` for hygiene and to prevent future split-brain (like the SUMMARY one we just fixed):

| Object | Current | Target |
|---|---|---|
| Per-vendor detail | `<V>_RECON_DETAIL` (9 tables) | `<V>_RECON_DETAIL_PROD` |
| Per-vendor summary | `<V>_RECON_SUMMARY` (9 tables) | `<V>_RECON_SUMMARY_PROD` |
| Unified maps | `RECON_PARTNER_MAP`, `RECON_SKU_MAP` | `RECON_PARTNER_MAP_PROD`, `RECON_SKU_MAP_PROD` |
| Manual seeds | `RECON_MANUAL_SEED_PARTNER_MAP`, `RECON_MANUAL_SEED_SKU_MAP` | `..._PROD` |

Compat views (`<V>_PARTNER_MAPPING_V5`, `<V>_SKU_MAP_V5`, `<V>_USAGE`) can stay unnamed — they exist *because* legacy vendor SQL references those exact names. Renaming them defeats their purpose.

Rename procedure per object:
1. Rename in vendor SQL file (`Vendor_Recon_Pipelines_Prod/<V>/<V>_Reconciliation_Script_Prod.sql`).
2. Update the emit-block in `scripts/_run_skeleton_pipeline.py` (the `PER_VENDOR_EMIT_OVERRIDES` dict).
3. Update the sql/02 file if map name changed.
4. Re-run `python scripts\_run_skeleton_pipeline.py`.
5. Confirm with `python scripts\_audit_naming_and_parity.py`.
6. Drop the old non-`_PROD` copy in Snowflake.

## 5. Per-vendor calibration playbook (repeat 9 times, one per work-block)

The order-of-operations that will move the needle for every vendor:

### Step 1 — Diagnose (5 min)

```powershell
cd C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline

# Baseline: parity + naming
python -u scripts\_audit_naming_and_parity.py       # gives you the target table
python -u scripts\_diag_exception_buckets.py        # top 4 buckets + $ per vendor
python -u scripts\_audit_cw_sku_universe.py         # top 10 unmapped CW SKUs per vendor
```

Read `logs/audit_naming_and_parity.txt` for QTY parity (the manual-team metric) and abs Δ, then decide:

- **QTY parity far above 100%** → double-counting. Fanout in a LEFT JOIN. Look for missing `QUALIFY ROW_NUMBER()` dedupe (KeepIT-style fix).
- **QTY parity far below 100%** → missing rows. Usage-side filter is too aggressive, or a SKU map row is dropping vendor lines.
- **$ parity but QTY parity ok** → unit-price / rate resolution is wrong. Check `THIRD_PARTY_RECON_VENDOR_INVOICES` join and `<V>_CONTRACT_RATES`.
- **Vendor $ = 0 but QTY exploding** (ESET's condition) → the vendor SQL is generating rows without joining to `THIRD_PARTY_RECON_VENDOR_INVOICES` at all. Fix the price join.

### Step 2 — Read the vendor SQL

Open `Vendor_Recon_Pipelines_Prod/<VENDOR>/<VENDOR>_Reconciliation_Script_Prod.sql`. Every file follows the same shape:
```
1. one CTE per input source (usage, zuora, marketplace, trt if applicable)
2. one CTE that resolves partner (via <V>_PARTNER_MAPPING_V5)
3. one CTE that resolves SKU (via <V>_SKU_MAP_V5)
4. one CTE that resolves rate (via THIRD_PARTY_RECON_VENDOR_INVOICES)
5. FULL OUTER JOIN of vendor-side and CW-side per (partner, sku, month)
6. final CREATE OR REPLACE TABLE <VENDOR>_RECON_DETAIL
```
Proofpoint's file is the reference implementation. Any vendor whose parity is off — compare to Proofpoint's pattern.

### Step 3 — Fix at the right layer

**If SKU map is missing rows** → add to `RECON_MANUAL_SEED_SKU_MAP` in Snowflake (INSERT), then re-run `sql/02`. Top unmapped SKUs per vendor are in `logs/audit_cw_sku_universe.txt`:

- Auvik: `CMS-UMM-SAAS-RMM-UMM-SRMMANM` ($12.4M), `CULCSAS100710001A250` ($2.8M), `CULCSAS100708001A100` ($1.5M) — Network Monitoring family
- Bitdefender: `DL17107A00D-EN-D` ATS&EDR ($2.8M), Secure Extra/Plus/PHASR
- SentinelOne: `M2MSEROTHRFFEPSECPRM` CW MDR Premium ($6.5M)

**If partner map is missing rows** → same but into `RECON_MANUAL_SEED_PARTNER_MAP`.

**If SQL logic is wrong** → edit the vendor SQL file directly. Common fixes:
- Add `QUALIFY ROW_NUMBER() OVER (PARTITION BY <key> ORDER BY <evidence>) = 1` to any LEFT JOIN whose right side has multiple rows per key. (This is the KeepIT fix pattern; copy from `Vendor_Recon_Pipelines_Prod/KeepIT/KeepIT_Reconciliation_Script_Prod.sql:partner_bridge`.)
- Make sure the vendor-side price join isn't dropped: `LEFT JOIN THIRD_PARTY_RECON_VENDOR_INVOICES i ON i.VENDOR = '<Vendor>' AND i.SKU = ...` should produce `VENDOR_UNIT_PRICE`; if it's null everywhere, ESET's zero-$ situation reappears.

### Step 4 — Re-run + verify (5 min)

```powershell
# Full skeleton, ~3-4 min
python -u scripts\_run_skeleton_pipeline.py

# Verify parity move (compare against previous)
python -u scripts\_audit_naming_and_parity.py
```

Success criterion: **QTY parity within ±2% of manual target AND clear rate ≥ manual clear equivalent AND abs $ Δ < 5% of CW $**.

### Step 5 — Commit + move on

```powershell
git add Vendor_Recon_Pipelines_Prod\<VENDOR>\<VENDOR>_Reconciliation_Script_Prod.sql
# (plus any seed SQL / dbt seed file used to load RECON_MANUAL_SEED_*)
git commit -m "<Vendor>: parity <before>% -> <after>%, abs $ delta <before> -> <after>"
```

Do NOT batch — one vendor per commit so we can bisect if anything regresses.

## 6. Recommended attack order for tomorrow

By diminishing returns (biggest gap first, but also isolating the easiest wins):

1. **ESET** — the zero-$ vendor amount is a broken price join in the vendor SQL. Fix that first because it's blocking everything downstream. Also QTY parity is 2854% (units bug: likely counting seats × months × regions instead of just seats-in-scope).
2. **Webroot** — QTY parity 260% is a straight double-count. Look for a UNION ALL where a DISTINCT / dedupe is missing, or an unfiltered CW_TRT vs CW_ZUORA join. Same fix pattern as KeepIT.
3. **Auvik** — QTY 124%, $ 89%. SKU map gap ($30M unmapped ARR) will fix both. Add 20-40 rows to `RECON_MANUAL_SEED_SKU_MAP` for the CMS-UMM-SAAS-RMM family.
4. **SentinelOne** — $ parity is worst at 29.5% because of the huge unbilled API-usage bucket ($1.65M). Two-fold fix: add the 5 missing SKUs (~$8.6M), then decide whether the API-usage-no-CW-billing rows should be classified as leakage or timing (this may need vendor SQL logic tweaks, not the classifier).
5. **Acronis** — QTY 120%, $ 54%. Investigate `Known Discount / Bundle` bucket ($715K) — probably a discount SKU getting counted as a full-price line.
6. **KeepIT** — QTY 77%, $ 53%. Partner side is fixed but SKU-level product mapping (M365 vs Azure vs D365 vs Google vs SFDC) still needs manual seeding. This is the "complex" one — expect the longest work-block.
7. **Exium** — 134% QTY parity but only 791 rows and $540K abs Δ. Small vendor, quick 30-min fix. Look at `EXIUM_USAGE_RECON_COMPAT` view for the count.
8. **Bitdefender** — QTY 100.8% (beats target) but $ 45.1% (misses badly). Rate resolution issue. Look at `BITDEFENDER_CONTRACT_RATE_TIERS`.
9. **Proofpoint** — already beating QTY target. Just clean up the $1.4M abs $ Δ.

## 7. First-actions checklist for the new chat

1. Read this file end-to-end.
2. Read the top of [README.md](README.md).
3. Read `logs/audit_naming_and_parity.txt` (current parity + naming audit).
4. Read `logs/audit_cw_sku_universe.txt` (top unmapped CW SKUs).
5. Read `logs/diag_exception_buckets.txt` (top 4 exception buckets per vendor).
6. Read `cowork_output/output/ENGINEERING_MONTHLY_INGESTION_MANIFEST.md` sections for the target vendor.
7. Ask the user: *"Attack order recommended is ESET → Webroot → Auvik → SentinelOne → Acronis → KeepIT → Exium → Bitdefender → Proofpoint. Confirm or override, and which vendor first?"*
8. Once confirmed, follow §5 for that vendor.

## 8. Files you will edit tomorrow

Per vendor (the only files that should change):
- `Vendor_Recon_Pipelines_Prod/<VENDOR>/<VENDOR>_Reconciliation_Script_Prod.sql` — the logic
- Snowflake `RECON_MANUAL_SEED_SKU_MAP` / `RECON_MANUAL_SEED_PARTNER_MAP` — seed additions (via a small INSERT script committed to `scripts/seeds/`)

Never edit:
- `scripts/build_third_party_recon_output_prod.py`
- Any table with `_PROD` suffix directly
- `THIRD_PARTY_RECON_OUTPUT_PROD` schema

## 9. Session memory to load

- `/memories/repo/skeleton_complete_and_keepit_fix_2026_08_23.md`
- `/memories/repo/production_consolidation_complete_2026_08_20.md`
- `/memories/repo/unified_recon_schema_2026_08_19.md`
- `/memories/repo/pipeline_architecture_tables_2026_08_20.md`
