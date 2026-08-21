# Third Party Reconciliation - Production Pipeline

Compact production pipeline that reconciles third-party vendor usage against ConnectWise billing for 9 vendors: Acronis, Auvik, Bitdefender, ESET, Exium, KeepIT, Proofpoint, SentinelOne, Webroot.

## Architecture at a glance

```
8 ingestion scripts (scripts/ingestion/)
        v
THIRD_PARTY_RECON_VENDOR_USAGE_PROD  +  unified billing / SKU / partner tables
        v
9 single-file recon SQL (Vendor_Recon_Pipelines_Prod/<VENDOR>/02_final_reconciliation.sql)
        v
THIRD_PARTY_RECON_OUTPUT_PROD  +  THIRD_PARTY_RECON_SUMMARY (app-facing)
```

The Streamlit app in `app/combined_recon_app.py` reads only the parity-locked table set (see `PRODUCTION_STRUCTURE.md`).

## Run

```powershell
cd Combined_Recon_Prod_Pipeline
..\..\..\.venv\Scripts\python.exe scripts\_run_reports.py
```

The orchestrator:
1. Backfills invoice prices in `VENDOR_USAGE_PROD`
2. Runs each of the 9 vendor SQL files, promoting to `<VENDOR>_RECON_DETAIL_PROD`
3. Inserts into unified `THIRD_PARTY_RECON_DETAIL_PROD`
4. Builds `THIRD_PARTY_RECON_OUTPUT_PROD` + `THIRD_PARTY_RECON_SUMMARY` via `build_third_party_recon_output_prod.py` (Iter2/Iter3 classifier: 14 canonical exception buckets, minor-drift Rule 12, both-zero Rule 13)
5. Drops per-vendor recon table sprawl

## Launch the app

```powershell
run_app.bat
```

## Repo layout

See `PRODUCTION_STRUCTURE.md` for the full file tree and Snowflake object map.

## Key invariants

- Exactly one usage table, one output table, one summary table drive the app
- Every vendor ingestion script references `THIRD_PARTY_RECON_VENDOR_INVOICES` (no hardcoded invoice prices)
- Each vendor pipeline is a single self-describing SQL file - no per-vendor `00_reference_maps.sql` / `01_billing_sources.sql` sprawl
- `THIRD_PARTY_STANDALONE_RECON_DETAIL__<VENDOR>` in Snowflake is the canonical intermediate; orchestrator can fall back to it if a vendor SQL fails
