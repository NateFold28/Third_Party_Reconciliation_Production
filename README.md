# Third Party Reconciliation - Production Pipeline

Authoritative production repository. This is the exact restore point if anything gets deleted again.

**Repo:** https://github.com/NateFold28/Third_Party_Reconciliation_Production

## Vendors (9)
Acronis, Auvik, Bitdefender, ESET, Exium, KeepIT, Proofpoint, SentinelOne, Webroot.

## Architecture

Data flow (verified against Snowflake 2026-08-21):

```
scripts/ingestion/<Vendor>_Vendor_Usage_Ingestion_Prod.py  x 9  (Excel/CSV in)
                       |
                       v
   THIRD_PARTY_RECON_VENDOR_USAGE_PROD            (single unified usage table;
                                                    all 9 vendors write here
                                                    except Bitdefender which
                                                    also writes to its
                                                    BITDEFENDER_ROYALTY_REPORT_RAW_PROD)
                       +
   THIRD_PARTY_RECON_SKU_MAP_PROD                 (curated)
   THIRD_PARTY_RECON_PARTNER_MAP_PROD             (curated)
   THIRD_PARTY_RECON_SOURCE_*_PROD                (Zuora / Marketplace / TRT
                                                    unified via sql/01_unified_billing_sources.sql)
                       |
                       v [SEE NOTE 1]
   THIRD_PARTY_STANDALONE_RECON_DETAIL__<VENDOR>  x 9  (61-col canonical intermediate;
                                                        currently STATIC snapshots)
                       |
                       v  scripts/_run_reports.py STEP 1d TRANSLATIONS insert
                       v
   THIRD_PARTY_RECON_DETAIL_PROD                  (single unified detail table)
                       |
                       v  scripts/build_third_party_recon_output_prod.py
                       v  (14-bucket classifier + DATA_LOAD_STATUS signal)
   THIRD_PARTY_RECON_OUTPUT_PROD  +  THIRD_PARTY_RECON_SUMMARY
                       |
                       v
              app/combined_recon_app.py (Streamlit)
```

**NOTE 1 - IMPORTANT:** the arrow from `VENDOR_USAGE_PROD` to `STANDALONE_RECON_DETAIL__<VENDOR>` is not currently executed by any script in this repo. The STANDALONE tables were populated by a legacy per-vendor engine that predates this restore point. Ingesting fresh usage does not update them until that rebuild step is added back. See `docs/architecture_gap_2026_08_21.md` and the "Known limitations" section below.

## App-facing tables (parity-locked - do not modify without app changes)

| Table | Role |
|---|---|
| `THIRD_PARTY_RECON_OUTPUT_PROD` | Primary flat table the app reads |
| `THIRD_PARTY_RECON_SUMMARY` | Per-(vendor, month) aggregates + `DATA_LOAD_STATUS` (LOADED / PARTIAL / NOT_LOADED) |
| `THIRD_PARTY_RECON_SUMMARY_PROD` | Legacy summary (audit only) |
| `THIRD_PARTY_RECON_DETAIL_PROD` | Row-level detail (drilldown) |
| `THIRD_PARTY_RECON_VENDOR_USAGE_PROD` | Ingested vendor usage (fresh) |
| `THIRD_PARTY_RECON_SKU_MAP_PROD` | SKU mapping seed |
| `THIRD_PARTY_RECON_PARTNER_MAP_PROD` | Partner mapping seed |

## Vendor input files

| Vendor | Source file pattern |
|---|---|
| Acronis | `Acronis_*` usage exports |
| Auvik | Auvik CW usage exports |
| Bitdefender | **`Bitdefender_CW_Royalty Report_*.xlsx`** (Portal exportMyUsage files are intentionally ignored - not billable) |
| ESET | ESET usage exports |
| Exium | Exium usage exports |
| KeepIT | KeepIT usage exports + support usage |
| Proofpoint | Proofpoint usage exports |
| SentinelOne | SentinelOne usage exports |
| Webroot | Webroot usage exports |

## Run

```powershell
cd Combined_Recon_Prod_Pipeline
..\..\..\.venv\Scripts\python.exe scripts\_run_reports.py
```

Launch app:
```powershell
run_app.bat
```

## Verified Metrics (2026-08-21)

- `THIRD_PARTY_RECON_OUTPUT_PROD`: **93,490 rows / 9 vendors**
- Clear: **69,258 rows / $59,628,567**

## Repo layout

See `PRODUCTION_STRUCTURE.md` for the full file tree, Snowflake object map, and the role of each `<Vendor>_Reconciliation_Script_Prod.sql` file.

## Key invariants

- Only ONE unified output table drives the app: `THIRD_PARTY_RECON_OUTPUT_PROD`
- Every ingestion script references `THIRD_PARTY_RECON_VENDOR_INVOICES` dynamically (no hardcoded invoice prices)
- `THIRD_PARTY_RECON_VENDOR_USAGE_PROD` is the single canonical usage table for 8 of 9 vendors (Bitdefender additionally has a Royalty-Report-specific raw table)
- Vendor SQL files (`<Vendor>_Reconciliation_Script_Prod.sql`) are the authoritative logic-of-record preserved for restore; the current orchestrator does not execute them because the STANDALONE tables are already the canonical intermediate

## Known limitations

- **STANDALONE tables are decoupled from live ingestion.** Fresh vendor usage in `THIRD_PARTY_RECON_VENDOR_USAGE_PROD` does not automatically flow into `THIRD_PARTY_STANDALONE_RECON_DETAIL__<VENDOR>`. This means specific (vendor, month) tuples may look outdated in the app even when their underlying source files have been ingested. Verified 2026-08-21: KeepIT usage table has all 7 months (Jan-Jul), but KeepIT STANDALONE only has May/Jun/Jul.
- **Signal to detect this in the app:** `THIRD_PARTY_RECON_SUMMARY.DATA_LOAD_STATUS` now reports `NOT_LOADED` for (vendor, month) tuples with zero usage rows and `PARTIAL` when the row count is under 30% of that vendor's median. The Streamlit app can render "No Data Loaded" tiles instead of a poor reconciliation rate for these months.
- **Bitdefender source policy:** Only the Royalty Report is billable and ingested. Portal `exportMyUsage*.csv` files are intentionally rejected (see `scripts/ingestion/Bitdefender_Vendor_Usage_Ingestion_Prod.py`).

## Planned next iteration

Replace the static STANDALONE tables with a single canonical SQL (`sql/02_build_recon_detail_from_usage.sql`) that generates `THIRD_PARTY_RECON_DETAIL_PROD` directly from `THIRD_PARTY_RECON_VENDOR_USAGE_PROD` + the map tables + `THIRD_PARTY_RECON_SOURCE_*_PROD`. This will make the ingest -> app data flow truly end-to-end and eliminate the drift described above. Scoped as a follow-up because it requires vendor-by-vendor parity verification against the current 93,490-row baseline.
