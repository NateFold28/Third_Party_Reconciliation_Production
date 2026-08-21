# Third Party Reconciliation - Production Pipeline

Authoritative production repository. This is the exact restore point if anything gets deleted again.

**Repo:** https://github.com/NateFold28/Third_Party_Reconciliation_Production

## Vendors (9)
Acronis, Auvik, Bitdefender, ESET, Exium, KeepIT, Proofpoint, SentinelOne, Webroot.

## Architecture

```
scripts/ingestion/<Vendor>_Vendor_Usage_Ingestion_Prod.py  x 9  (Excel/CSV in)
                       |
                       v
   THIRD_PARTY_RECON_VENDOR_USAGE_PROD            (single usage table)
                       +
   THIRD_PARTY_RECON_SKU_MAP_PROD                 (curated)
   THIRD_PARTY_RECON_PARTNER_MAP_PROD             (curated)
   THIRD_PARTY_RECON_SOURCE_*_PROD                (unified billing sources)
                       |
                       v
   THIRD_PARTY_STANDALONE_RECON_DETAIL__<VENDOR>  x 9  (61-col canonical, persisted in Snowflake)
                       |
                       v
   scripts/_run_reports.py STEP 1d TRANSLATIONS insert
                       |
                       v
   THIRD_PARTY_RECON_DETAIL_PROD                  (single unified detail table)
                       |
                       v
   scripts/build_third_party_recon_output_prod.py (Iter2/Iter3 classifier, 14 exception buckets)
                       |
                       v
   THIRD_PARTY_RECON_OUTPUT_PROD  +  THIRD_PARTY_RECON_SUMMARY
                       |
                       v
              app/combined_recon_app.py (Streamlit)
```

## App-facing tables (parity-locked - do not modify without app changes)

| Table | Role |
|---|---|
| `THIRD_PARTY_RECON_OUTPUT_PROD` | Primary flat table the app reads |
| `THIRD_PARTY_RECON_SUMMARY` | Aggregate stats |
| `THIRD_PARTY_RECON_SUMMARY_PROD` | Legacy summary (audit only) |
| `THIRD_PARTY_RECON_DETAIL_PROD` | Row-level detail (drilldown) |
| `THIRD_PARTY_RECON_VENDOR_USAGE_PROD` | Ingested vendor usage |
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
- STANDALONE recon tables in Snowflake are the source of truth for `THIRD_PARTY_RECON_DETAIL_PROD`
- Vendor SQL files (`<Vendor>_Reconciliation_Script_Prod.sql`) are the authoritative logic-of-record preserved for restore; the current orchestrator does not execute them because the STANDALONE tables are already the canonical intermediate
