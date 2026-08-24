# Production Repository Structure
**Date:** 2026-08-21 | **Status:** Production ready | **Restore point:** this commit

## File tree (minimal production set)

```
Combined_Recon_Prod_Pipeline/
|-- README.md
|-- PRODUCTION_STRUCTURE.md
|-- run_app.bat
|-- .gitignore
|-- .streamlit/                                       (secrets.toml gitignored)
|
|-- app/
|   `-- combined_recon_app.py                         Streamlit dashboard (do not edit outside the app)
|
|-- scripts/
|   |-- _run_reports.py                               Orchestrator (STEP 0-5)
|   |-- build_third_party_recon_output_prod.py        Iter2/Iter3 classifier: OUTPUT_PROD + SUMMARY
|   `-- ingestion/
|       |-- Acronis_Vendor_Usage_Ingestion_Prod.py
|       |-- Auvik_Vendor_Usage_Ingestion_Prod.py
|       |-- Bitdefender_Vendor_Usage_Ingestion_Prod.py  (reads Bitdefender_CW_Royalty Report_*.xlsx)
|       |-- ESET_Vendor_Usage_Ingestion_Prod.py
|       |-- Exium_Vendor_Usage_Ingestion_Prod.py
|       |-- KeepIT_Vendor_Usage_Ingestion_Prod.py
|       |-- Proofpoint_Vendor_Usage_Ingestion_Prod.py
|       |-- SentinelOne_Vendor_Usage_Ingestion_Prod.py
|       `-- Webroot_Vendor_Usage_Ingestion_Prod.py
|
|-- sql/                                              Shared cross-vendor SQL
|   |-- 00b_backfill_invoice_prices.sql               (backfills VENDOR_UNIT_PRICE from THIRD_PARTY_RECON_VENDOR_INVOICES)
|   |-- 00c_vendor_usage_views.sql
|   |-- 01_unified_billing_sources.sql
|   |-- 03_flag_distribution_report.sql
|   `-- 04_manual_recon_gap_audit.sql
|
|-- Vendor_Recon_Pipelines_Prod/                      Authoritative vendor logic (git restore point)
|   |-- Acronis/Acronis_Reconciliation_Script_Prod.sql
|   |-- Auvik/Auvik_Reconciliation_Script_Prod.sql
|   |-- Bitdefender/Bitdefender_Reconciliation_Script_Prod.sql
|   |-- ESET/ESET_Reconciliation_Script_Prod.sql
|   |-- Exium/Exium_Reconciliation_Script_Prod.sql
|   |-- KeepIT/KeepIT_Reconciliation_Script_Prod.sql
|   |-- Proofpoint/Proofpoint_Reconciliation_Script_Prod.sql
|   |-- SentinelOne/SentinelOne_Reconciliation_Script_Prod.sql
|   `-- Webroot/Webroot_Reconciliation_Script_Prod.sql
|
`-- docs/
    |-- CONTRACT.md
    `-- UNIFIED_SOURCE_ARCHITECTURE_2026-08-19.md
```

## Snowflake object map

### App reads (parity-locked)

| Table | Written by |
|---|---|
| `THIRD_PARTY_RECON_OUTPUT_PROD` | `scripts/build_third_party_recon_output_prod.py` |
| `THIRD_PARTY_RECON_SUMMARY` | `scripts/build_third_party_recon_output_prod.py` |
| `THIRD_PARTY_RECON_SUMMARY_PROD` | `scripts/_run_reports.py` STEP 4 |
| `THIRD_PARTY_RECON_DETAIL_PROD` | `scripts/_run_reports.py` STEP 1d TRANSLATIONS |
| `THIRD_PARTY_RECON_VENDOR_USAGE_PROD` | `scripts/ingestion/*_Vendor_Usage_Ingestion_Prod.py` |
| `THIRD_PARTY_RECON_SKU_MAP_PROD` | curated seed |
| `THIRD_PARTY_RECON_PARTNER_MAP_PROD` | curated seed |

### Pipeline-internal intermediates (not read by app)

| Table | Role |
|---|---|
| `THIRD_PARTY_STANDALONE_RECON_DETAIL__<VENDOR>` | 61-col canonical per-vendor intermediate feeding `THIRD_PARTY_RECON_DETAIL_PROD`. **Currently a static snapshot** — see "Known limitation" below. |
| `THIRD_PARTY_RECON_VENDOR_INVOICES` | dynamic invoice-price reference; every ingestion script queries it |
| `THIRD_PARTY_RECON_SOURCE_ZUORA_PROD` / `_MARKETPLACE_PROD` / `_TRT_PROD` / `_ROYALTIES_PROD` | unified billing sources built by `sql/01_unified_billing_sources.sql` |

## Role of `<Vendor>_Reconciliation_Script_Prod.sql`

These 9 files are the **authoritative vendor business logic preserved in git** as the restore point. They are NOT executed by `_run_reports.py` — the orchestrator reads directly from the STANDALONE tables via `standalone_insert()` in STEP 1d.

If the STANDALONE tables ever need to be rebuilt from source, each vendor's script contains all reconciliation logic (SKU mapping, partner mapping, billing joins, outcome flag derivation) in one self-contained SQL file.

## Known limitation: STANDALONE decoupling

The engine that populated `THIRD_PARTY_STANDALONE_RECON_DETAIL__<VENDOR>` predates this repo and is not present here. As a result, running `_run_reports.py` does not refresh those tables — it only re-projects them into `THIRD_PARTY_RECON_DETAIL_PROD` and the app-facing tables. Fresh ingestion into `THIRD_PARTY_RECON_VENDOR_USAGE_PROD` therefore does not automatically appear in the app until the STANDALONE tables are rebuilt.

Signal exposed to the app: `THIRD_PARTY_RECON_SUMMARY.DATA_LOAD_STATUS` reports `NOT_LOADED` / `PARTIAL` / `LOADED` per (vendor, month), so the Streamlit surface can render "No Data Loaded" instead of a misleading reconciliation rate.

Follow-up work: replace the STANDALONE-based path with `sql/02_build_recon_detail_from_usage.sql` — a single SQL that derives `THIRD_PARTY_RECON_DETAIL_PROD` directly from `THIRD_PARTY_RECON_VENDOR_USAGE_PROD` joined to `THIRD_PARTY_RECON_SOURCE_*_PROD` and the map tables. See repo memory `standalone_elimination_spec_2026_08_21.md`.

## Verified run (2026-08-21)

- `THIRD_PARTY_RECON_OUTPUT_PROD`: 93,490 rows / 9 vendors
- Clear: 69,258 rows / $59,628,567
- Overall clear rate: ~74%
