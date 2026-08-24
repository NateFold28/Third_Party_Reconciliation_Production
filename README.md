# Third Party Vendor Reconciliation Pipeline

Production reconciliation system for 9 third-party vendors against ConnectWise billing.

**Repo:** https://github.com/NateFold28/Third_Party_Reconciliation_Production

## Repository Structure

```
├── Ingestion/          10 ingestion scripts (9 vendor usage + 1 invoice)
├── Reconciliation/     9 vendor recon SQL files + 2 pipeline orchestrators
├── Maps/
│   ├── sql/            5 SQL files for billing sources, reference maps, compat views
│   └── seeds/          3 seed CSV files (partner map, SKU map, SentinelOne rates)
└── App/                Streamlit reconciliation dashboard
```

## Architecture

```
Ingestion (10 scripts)
  9 × <Vendor>_Vendor_Usage_Ingestion_Prod.py  →  THIRD_PARTY_RECON_VENDOR_USAGE_PROD
  1 × Netsuite_Invoice_JSON_Ingestion_Prod.py  →  THIRD_PARTY_RECON_VENDOR_INVOICES

Maps (Engineering manages directly in Snowflake)
  THIRD_PARTY_RECON_PARTNER_MAP_PROD  (seed: Maps/seeds/RECON_PARTNER_MAP.csv)
  THIRD_PARTY_RECON_SKU_MAP_PROD      (seed: Maps/seeds/RECON_SKU_MAP.csv)
  Maps/sql/02 → RECON_PARTNER_MAP + RECON_SKU_MAP (working copies)

Billing Sources (from analytics DB)
  Maps/sql/01 → THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
              → THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
              → THIRD_PARTY_RECON_SOURCE_TRT_PROD

Reconciliation (9 SQL files + orchestrators)
  <Vendor>_Reconciliation_Script_Prod.sql  →  <VENDOR>_RECON_DETAIL  (staging per vendor)
  _run_skeleton_pipeline.py                →  THIRD_PARTY_RECON_DETAIL_PROD (unified)
  build_third_party_recon_output_prod.py   →  THIRD_PARTY_RECON_OUTPUT_PROD (LOCKED)
                                           →  THIRD_PARTY_RECON_SUMMARY_PROD

App
  App/combined_recon_app.py reads OUTPUT_PROD + SUMMARY_PROD  (Streamlit)
```

## Running the Pipeline

```powershell
# 1. Ingest vendor usage files (run when new monthly files arrive)
.venv\Scripts\python.exe "Ingestion\<Vendor>_Vendor_Usage_Ingestion_Prod.py" --all-months

# 2. Ingest Netsuite invoices (run after Engineering updates PARSED_VENDOR_DATA)
.venv\Scripts\python.exe "Ingestion\Netsuite_Invoice_JSON_Ingestion_Prod.py" --from 2026-01

# 3. Run the full reconciliation pipeline
.venv\Scripts\python.exe "Reconciliation\_run_skeleton_pipeline.py"

# 4. Launch the app
streamlit run "App\combined_recon_app.py"
```

## The Two Levers for Improvement

1. **Partner Map** — Edit `THIRD_PARTY_RECON_PARTNER_MAP_PROD` in Snowflake → re-run `Maps/sql/02_unified_reference_maps.sql` → re-run pipeline
2. **SKU Map** — Edit `THIRD_PARTY_RECON_SKU_MAP_PROD` in Snowflake → re-run `Maps/sql/02_unified_reference_maps.sql` → re-run pipeline

## Current Pipeline Performance (2026-08-24)

| Vendor | Clear % | Note |
|---|---|---|
| Proofpoint | **95.2%** ✅ | Gold standard |
| Bitdefender | 86.7% | |
| Acronis | 82.3% | SKU map gaps |
| SentinelOne | 79.5% | Add-on SKUs |
| Exium | 72.2% | Small vendor |
| Auvik | 63.8% | SKU map gaps |
| Webroot | 47.1% | Pricing calibration |
| KeepIT | 28.4% | Product SKU mapping |
| ESET | 13.4% | SKU/rate calibration |

**OUTPUT_PROD**: 109,689 rows across 9 vendors, 12 EXCEPTION_TYPE buckets, 45 columns

## Snowflake Environment

- Role: `DEVELOPER`
- Warehouse: `REPORTING_WH`
- Database: `ANALYTICS_DEV`
- Schema: `DBT_NFOLD_TRANSFORMATION`

## Do-Nots

- Do NOT modify `Reconciliation/build_third_party_recon_output_prod.py` (locked classifier)
- Do NOT reintroduce `VENDOR_FALLBACK` entries to the exception taxonomy
- Do NOT create per-vendor staging tables beyond `<VENDOR>_RECON_DETAIL`
- All `_LEGACY_20260823` tables and V5 compat views have been dropped — do not reference them
