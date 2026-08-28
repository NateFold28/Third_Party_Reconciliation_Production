# Third Party Vendor Reconciliation Pipeline

Production reconciliation system for 9 third-party vendors against ConnectWise billing.

**Repo:** https://github.com/NateFold28/Third_Party_Reconciliation_Production

## Repository Structure

```
├── Ingestion/          10 ingestion scripts (9 vendor usage + 1 invoice)
├── Reconciliation/     9 vendor recon SQL files + orchestrators
├── Maps/
│   ├── sql/            source-of-truth mapping and source builders
│   ├── seeds/          source-of-truth seed CSV files (partner, SKU)
│   └── tools/          sanctioned map-ingestion tool(s)
├── logs/               run artifacts and troubleshooting logs
└── app/                Streamlit reconciliation dashboard
```

## Canonical Architecture

```
1) Ingestion (10 scripts)
   9 x <Vendor>_Vendor_Usage_Ingestion_Prod.py  -> THIRD_PARTY_RECON_VENDOR_USAGE_PROD
   1 x Netsuite_Invoice_JSON_Ingestion_Prod.py  -> THIRD_PARTY_RECON_VENDOR_INVOICES

2) Unified Maps (single source of truth)
   Maps/seeds + Maps/sql/02_unified_reference_maps.sql ->
     RECON_PARTNER_MAP
     RECON_SKU_MAP
     RECON_ACCOUNT_MERGE_RESOLVER
     RECON_VENDOR_PARTNER_MANUAL_MAP

3) Unified Billing Sources
   Maps/sql/01_unified_billing_sources.sql ->
     THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
     THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
     THIRD_PARTY_RECON_SOURCE_TRT_PROD
   Source policy:
     - THIRD_PARTY_RECON_SOURCE_ZUORA_PROD is built from the live
       ANALYTICS_DEV.DBT_NFOLD.FINAL_TPR_ENGINEERING_ZUORA_SOURCE_V2 source.
     - THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD is built directly from the
       live ANALYTICS.DBO.CARR__ALL_TRANSACTIONS source.
     - Active production recon scripts must not point back to the older
       ANALYTICS_DEV.DBT_NFOLD.ZUORA_THIRD_PARTY_RECON_BASE object.

4) Vendor Reconciliation (9 distinct scripts)
   <Vendor>_Reconciliation_Script_Prod.sql -> <VENDOR>_RECON_DETAIL
   _run_skeleton_pipeline.py -> THIRD_PARTY_RECON_DETAIL_PROD
                           -> THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
   build_third_party_recon_output_prod.py -> THIRD_PARTY_RECON_OUTPUT_PROD
                                         -> THIRD_PARTY_RECON_SUMMARY_PROD

5) App
   app/combined_recon_app.py reads OUTPUT_PROD + SUMMARY_PROD
  and hides any vendor-month where vendor usage files are absent.
```

No production logic is allowed outside this flow.

## Mapping Governance (Required)

- Manual partner or SKU mapping must live in Maps sources only:
  - Maps/seeds/*
  - Maps/sql/02_unified_reference_maps.sql
  - RECON_VENDOR_PARTNER_MANUAL_MAP populated from mapping SQL/tools
- Manual mappings must not be hardcoded inside vendor reconciliation scripts.
- Vendor scripts may consume mapping tables, but must not embed one-off partner/SKU overrides.

## Dependency Policy

- No legacy matched/resolved billing tables are used by active vendor recon scripts.
- No active production path may read the older `ANALYTICS_DEV.DBT_NFOLD.ZUORA_THIRD_PARTY_RECON_BASE` object.
- Vendor recon logic is housed in each vendor's script under Reconciliation/.
- Improvement levers are limited to:
  1) unified partner map, 2) unified SKU map, 3) vendor recon SQL logic.
- Mapping fixes are applied as one-time Snowflake DML updates (for example MERGE/UPDATE)
  against source-of-truth mapping tables, then re-run Maps/sql/02.
  Do not hardcode mapping overrides inside vendor reconciliation SQL files.

## Running the Pipeline

```powershell
# 1. Full refresh runner (recommended): ingestion + invoices + maps + sources + all 9 recon scripts
.venv\Scripts\python.exe "Reconciliation\_run_full_refresh_pipeline.py" --from-month 2026-01

# 1a. One-time baseline refresh (vendor-sliced replacement in shared usage table, then rerun all ingestion scripts)
.venv\Scripts\python.exe "Reconciliation\_run_full_refresh_pipeline.py" --from-month 2026-01 --full-refresh-now

# 1b. Optional: force ingestion/invoices without destructive reset
.venv\Scripts\python.exe "Reconciliation\_run_full_refresh_pipeline.py" --from-month 2026-01 --force-ingestion --force-invoices

# 2. Recon-only rebuild (fast path, no ingestion/invoice refresh)
.venv\Scripts\python.exe "Reconciliation\_run_skeleton_pipeline.py"

# 3. Launch the app
streamlit run "app\combined_recon_app.py"
```

The full reconciliation pipeline rebuilds the invoice-vs-raw-usage intra control
as a core invoice gate before app-facing output is rebuilt. Vendor invoices are
the source of truth for charged quantity and amount; vendor raw usage must tie to
that invoice control before recon/app metrics are trusted.

Important:
- `_run_skeleton_pipeline.py` does not run ingestion or invoice parsing.
- `_run_full_refresh_pipeline.py` default behavior is incremental/idempotent with smart-skip when unchanged.
- `_run_full_refresh_pipeline.py` is the canonical refresh path when vendor usage changes, because it rebuilds ingestion, maps, live billing sources, vendor recon detail, OUTPUT_PROD, and SUMMARY_PROD together.
- Use `--full-refresh-now` for a guaranteed one-time full baseline rebuild.
- In `--full-refresh-now` mode, the runner now clears one vendor slice at a time before each ingestion script to prevent shared-table wipeouts if a later step fails.
- Use `--force-ingestion --force-invoices` to bypass smart-skip without forcing destructive reset semantics.

## Proofpoint Validation Note (Important)

For raw usage inspection, do not use GROUP BY ALL unless you intentionally want
deduplicated row signatures. It collapses duplicate records and can understate
total quantity.

Example:
- Applied Network Solutions, Proofpoint, 2026-05 Professional
  - Raw true total (SUM(quantity)): 585
  - GROUP BY ALL signature total: 513
  - Difference: 72 seats from duplicate raw rows collapsed by GROUP BY ALL

Use this query to validate true raw totals:

```sql
SELECT
  vendor_partner_name,
  vendor_product_sku,
  SUM(quantity) AS qty,
  SUM(amount)   AS amt
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD
WHERE vendor = 'Proofpoint'
  AND billing_month = '2026-05-01'
  AND vendor_partner_name ILIKE '%applied%'
GROUP BY 1,2
ORDER BY 1,2;
```

## The Two Levers for Improvement

1. **Partner Map** — Edit `THIRD_PARTY_RECON_PARTNER_MAP_PROD` in Snowflake → re-run `Maps/sql/02_unified_reference_maps.sql` → re-run pipeline
2. **SKU Map** — Edit `THIRD_PARTY_RECON_SKU_MAP_PROD` in Snowflake → re-run `Maps/sql/02_unified_reference_maps.sql` → re-run pipeline

## Current Pipeline Performance (2026-08-27 rebuild)

| Vendor | Clear % | Note |
|---|---|---|
| Proofpoint | 96.2% | product-scoped API overlay live |
| Bitdefender | 86.7% | |
| Acronis | 81.1% | |
| ESET | 78.3% | |
| Exium | 77.1% | |
| SentinelOne | 73.3% | |
| Auvik | 67.1% | |
| Webroot | 52.3% | |
| KeepIT | 49.2% | continued calibration |

**OUTPUT_PROD (current full rebuild)**: 75,034 rows across 9 vendors.

## Snowflake Environment

- Role: `DEVELOPER`
- Warehouse: `REPORTING_WH`
- Database: `ANALYTICS_DEV`
- Schema: `DBT_NFOLD_TRANSFORMATION`

## Do-Nots

- Do NOT bypass maps by hardcoding manual partner/SKU overrides in recon SQL.
- Do NOT reintroduce any snapshot fallback path into the production orchestrators
- Do NOT create per-vendor staging tables beyond `<VENDOR>_RECON_DETAIL`
- `_LEGACY_20260823` tables are historical snapshots only; do not route active pipeline logic through them
- Do NOT recreate legacy matched/resolved billing tables; vendor recon scripts are source-driven from unified billing sources
- Do NOT route app-visible months through CW-only future billing periods when vendor usage files do not exist for that vendor
