# Production Repository Structure
**Date**: 2026-08-21 | **Status**: Production Ready (slim architecture)

## Architecture (8 + 1 + 9 + 1)

```
8 vendor ingestion scripts
        |
        v
1 shared usage table  ->  unified partner + SKU + billing tables
        |
        v
9 single-file vendor recon SQL scripts (one per vendor, all business logic inline)
        |
        v
1 combined output table (app-facing)
```

## Snowflake Objects (parity-locked with app)

App reads ONLY from these tables:

| Table                                       | Written by                                   |
|---------------------------------------------|----------------------------------------------|
| `THIRD_PARTY_RECON_OUTPUT_PROD`             | `scripts/build_third_party_recon_output_prod.py` |
| `THIRD_PARTY_RECON_SUMMARY`                 | `scripts/build_third_party_recon_output_prod.py` |
| `THIRD_PARTY_RECON_SUMMARY_PROD`            | `scripts/_run_reports.py` (STEP 4)           |
| `THIRD_PARTY_RECON_DETAIL_PROD`             | `scripts/_run_reports.py` (STEP 1d)          |
| `THIRD_PARTY_RECON_VENDOR_USAGE_PROD`       | `scripts/ingestion/*_Vendor_Usage_Ingestion_Prod.py` (all 8) |
| `THIRD_PARTY_RECON_SKU_MAP_PROD`            | curated seed                                 |
| `THIRD_PARTY_RECON_PARTNER_MAP_PROD`        | curated seed                                 |

Supporting persisted intermediates (owned by the pipeline, not read by the app):

| Table                                          | Role                                    |
|------------------------------------------------|-----------------------------------------|
| `THIRD_PARTY_STANDALONE_RECON_DETAIL__<VENDOR>` | canonical 61-col recon per vendor      |
| `THIRD_PARTY_RECON_VENDOR_INVOICES`            | dynamic invoice-price reference        |
| `<VENDOR>_RECON_DETAIL_PROD` / `_SUMMARY_PROD` | staging output of each vendor SQL, dropped in STEP 5 |

## File Layout

```
Combined_Recon_Prod_Pipeline/
|-- README.md
|-- PRODUCTION_STRUCTURE.md
|-- run_app.bat
|-- .gitignore
|
|-- app/
|   `-- combined_recon_app.py                # Streamlit dashboard (do not edit here)
|
|-- scripts/
|   |-- _run_reports.py                      # Orchestrator (STEP 0-5)
|   |-- build_third_party_recon_output_prod.py  # Builds OUTPUT_PROD + SUMMARY (Iter2/Iter3 classifier)
|   `-- ingestion/                           # 8 vendor ingestion scripts (Bitdefender shares one script)
|       |-- Acronis_Vendor_Usage_Ingestion_Prod.py
|       |-- Auvik_Vendor_Usage_Ingestion_Prod.py
|       |-- Bitdefender_Vendor_Usage_Ingestion_Prod.py
|       |-- ESET_Vendor_Usage_Ingestion_Prod.py
|       |-- Exium_Vendor_Usage_Ingestion_Prod.py
|       |-- KeepIT_Vendor_Usage_Ingestion_Prod.py
|       |-- Proofpoint_Vendor_Usage_Ingestion_Prod.py
|       |-- SentinelOne_Vendor_Usage_Ingestion_Prod.py
|       `-- Webroot_Vendor_Usage_Ingestion_Prod.py
|
|-- sql/                                     # Shared SQL utilities
|   |-- 00b_backfill_invoice_prices.sql
|   |-- 00c_vendor_usage_views.sql
|   |-- 01_unified_billing_sources.sql
|   |-- 03_flag_distribution_report.sql
|   `-- 04_manual_recon_gap_audit.sql
|
|-- Vendor_Recon_Pipelines_Prod/             # 9 single-file vendor SQLs
|   |-- Acronis/02_final_reconciliation.sql
|   |-- Auvik/02_final_reconciliation.sql
|   |-- Bitdefender/02_final_reconciliation.sql
|   |-- ESET/02_final_reconciliation.sql
|   |-- Exium/02_final_reconciliation.sql
|   |-- KeepIT/02_final_reconciliation.sql
|   |-- Proofpoint/02_final_reconciliation.sql
|   |-- SentinelOne/02_final_reconciliation.sql
|   `-- Webroot/02_final_reconciliation.sql
|
`-- docs/
    |-- CONTRACT.md
    `-- UNIFIED_SOURCE_ARCHITECTURE_2026-08-19.md
```

## Verified Run Metrics (2026-08-21)

- `THIRD_PARTY_RECON_OUTPUT_PROD`: 93,490 rows across 9 vendors
- Clear rows: 69,258 ($59,628,567)
- Row counts by vendor/month match each `<VENDOR>_RECON_DETAIL_PROD` staging table
- All 9 vendor ingestion scripts reference `THIRD_PARTY_RECON_VENDOR_INVOICES` dynamically (no hardcoded prices)

## Run

```powershell
cd Combined_Recon_Prod_Pipeline
..\..\..\.venv\Scripts\python.exe scripts\_run_reports.py
```
