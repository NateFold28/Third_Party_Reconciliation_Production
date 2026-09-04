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
                                         -> THIRD_PARTY_RECON_BILLING_LINE_DETAIL_PROD
                                         -> THIRD_PARTY_RECON_SUMMARY_PROD

   Grain policy:
     - OUTPUT_PROD remains the stable vendor/account/month/product-family
       reconciliation grain used for classification and clear-rate KPIs.
     - BILLING_LINE_DETAIL_PROD preserves every Zuora invoice line and links it
       to OUTPUT_PROD by CASE_ID when the billed SKU identifies one case.
       Use it for invoice drilldown; do not sum repeated OUTPUT_PROD measures
       after joining one case to several billing lines.

5) App
   app/combined_recon_app.py reads OUTPUT_PROD + SUMMARY_PROD
  and hides any vendor-month where vendor usage files are absent.
```

No production logic is allowed outside this flow.

## Outcome Governance (Required)

- `Reconciliation/canonical_outcomes.py` is the only authoritative outcome
  classifier. Shared detail, app output, summaries, and app filters must use it.
- `Clear` requires vendor amount > 0, CW amount > 0, and CW amount >= vendor
  amount. Quantity tolerances, partner-month offsets, zero-dollar rows, and
  vendor-native labels must never promote a row to `Clear`.
- Rows where vendor amount = 0 and CW amount = 0 remain in shared detail for
  source traceability but are excluded from app output, summaries, exception
  queues, and every Clear-rate denominator because no reconciliation occurred.
- With API usage present and vendor amount > CW amount >= 0, classify as
  `API Usage, Insufficient CW Billing`.
- Without API usage, vendor amount > 0 and CW amount = 0 is
  `Vendor Billing, No CW Billing`; when both are positive and CW is lower, use
  `Vendor Billing, Insufficient CW Billing` with no materiality threshold.
- Duplicate billing remains an informational side flag. Its primary exception
  category is intentionally disabled until the duplicate-source audit is done.
- Vendor-native outcome fields are evidence only. The app must not derive or
  remap outcomes from them.

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

# 1b. Optional, unsafe for freshness guarantees: skip ingestion/invoices when only code signatures are unchanged
.venv\Scripts\python.exe "Reconciliation\_run_full_refresh_pipeline.py" --from-month 2026-01 --enable-smart-skip

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
- `_run_full_refresh_pipeline.py` refreshes ingestion and parsed invoices by default. Code-only smart-skip is opt-in and does not prove source-data freshness.
- `_run_full_refresh_pipeline.py` is the authoritative end-to-end path. It rebuilds ingestion, parsed invoices, invoice-price enrichment, maps, live billing sources, vendor recon detail, invoice control, OUTPUT_PROD, and SUMMARY_PROD together.
- Invoice-price enrichment runs after vendor usage and invoice parsing and before every vendor reconciliation script.
- Publication fails when populated invoice/account identities are missing their NetSuite/Salesforce links.
- A complete run is recorded as authoritative only when Snowflake confirms that every canonical usage, invoice, billing-source, detail, intra-control, output, and summary table was altered after the run began. Partial and smart-skipped runs are labeled separately.
- Use `--full-refresh-now` for a guaranteed one-time full baseline rebuild.
- In `--full-refresh-now` mode, the runner now clears one vendor slice at a time before each ingestion script to prevent shared-table wipeouts if a later step fails.
- Use `--enable-smart-skip` only when upstream source freshness has been verified independently.

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

## Current Pipeline Performance (2026-09-02 strict rebuild)

| Vendor | Clear % | Note |
|---|---|---|
| Proofpoint | 93.0% | strict monetary classifier |
| SentinelOne | 82.1% | zero-versus-zero rows excluded |
| ESET | 78.5% | zero-versus-zero rows excluded |
| Acronis | 66.6% | zero-versus-zero rows excluded; structural audit required |
| Auvik | 65.3% | strict monetary classifier |
| Exium | 61.3% | strict monetary classifier |
| Webroot | 54.2% | zero-versus-zero rows excluded; structural audit required |
| Bitdefender | 53.5% | strict monetary classifier; shortfall audit required |
| KeepIT | 35.4% | structural and financial audit required |

Rates use loaded vendor-months only. Rows with explicit zero vendor amount and
zero CW amount are retained in shared detail for audit but excluded from output
and KPI denominators.

**OUTPUT_PROD (current full rebuild)**: 86,695 rows across 9 vendors.

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
