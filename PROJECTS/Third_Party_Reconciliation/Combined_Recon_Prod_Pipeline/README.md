# Third Party Vendor Reconciliation Pipeline

Production reconciliation system for 9 third-party vendors against ConnectWise billing.

**Repo:** https://github.com/NateFold28/Third_Party_Reconciliation_Production

## Repository Structure

```
├── Ingestion/          10 ingestion scripts (9 vendor usage + 1 invoice)
├── Reconciliation/     9 vendor recon SQL files + orchestrators
├── Maps/
│   ├── sql/            unified billing + unified reference maps
│   └── seeds/          2 active seed CSV files (partner map, SKU map)
├── logs/               latest troubleshooting logs only
└── app/                Streamlit reconciliation dashboard
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
                                           -> each vendor script reads only core layers:
                                              THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
                                              THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
                                              (and THIRD_PARTY_RECON_SOURCE_TRT_PROD where applicable)
  _run_skeleton_pipeline.py                →  THIRD_PARTY_RECON_DETAIL_PROD (unified)
  build_third_party_recon_output_prod.py   →  THIRD_PARTY_RECON_OUTPUT_PROD (LOCKED)
                                           →  THIRD_PARTY_RECON_SUMMARY_PROD

App
  app/combined_recon_app.py reads OUTPUT_PROD + SUMMARY_PROD  (Streamlit)
```

## Dependency Policy

- No legacy matched/resolved billing tables are used by active vendor recon scripts.
- Vendor recon logic is housed in each vendor's script under Reconciliation/.
- Improvement levers are limited to:
  1) unified partner map, 2) unified SKU map, 3) vendor recon SQL logic.
- Any new mapping correction for Acronis must be implemented through Maps/sql/02 + Acronis_Reconciliation_Script_Prod.sql.

## Running the Pipeline

```powershell
# 1. Ingest vendor usage files (run when new monthly files arrive)
.venv\Scripts\python.exe "Ingestion\<Vendor>_Vendor_Usage_Ingestion_Prod.py" --all-months

# 2. Ingest Netsuite invoices (run after Engineering updates PARSED_VENDOR_DATA)
.venv\Scripts\python.exe "Ingestion\Netsuite_Invoice_JSON_Ingestion_Prod.py" --from 2026-01

# 3. Run the full reconciliation pipeline
.venv\Scripts\python.exe "Reconciliation\_run_skeleton_pipeline.py"

# 4. Launch the app
streamlit run "app\combined_recon_app.py"
```

## The Two Levers for Improvement

1. **Partner Map** — Edit `THIRD_PARTY_RECON_PARTNER_MAP_PROD` in Snowflake → re-run `Maps/sql/02_unified_reference_maps.sql` → re-run pipeline
2. **SKU Map** — Edit `THIRD_PARTY_RECON_SKU_MAP_PROD` in Snowflake → re-run `Maps/sql/02_unified_reference_maps.sql` → re-run pipeline

## Current Pipeline Performance (2026-08-26, June billing month)

| Vendor | Clear % | Note |
|---|---|---|
| Auvik | **94.3%** | parity tuning pass |
| Proofpoint | 93.5% | July behavior gated to loaded billing months |
| Bitdefender | 91.5% | |
| Acronis | 90.1% | source-driven + mapping hardening |
| Exium | 83.2% | |
| SentinelOne | 83.1% | |
| ESET | 63.9% | |
| Webroot | 54.5% | |
| KeepIT | 41.0% | needs continued calibration |

**OUTPUT_PROD (current full rebuild)**: 116,380 rows across 9 vendors.

## Snowflake Environment

- Role: `DEVELOPER`
- Warehouse: `REPORTING_WH`
- Database: `ANALYTICS_DEV`
- Schema: `DBT_NFOLD_TRANSFORMATION`

## Do-Nots

- Do NOT modify `Reconciliation/build_third_party_recon_output_prod.py` (locked classifier)
- Do NOT reintroduce `VENDOR_FALLBACK` entries to the exception taxonomy
- Do NOT create per-vendor staging tables beyond `<VENDOR>_RECON_DETAIL`
- `_LEGACY_20260823` tables are historical snapshots only; do not route active pipeline logic through them
- Do NOT recreate legacy matched/resolved billing tables; vendor recon scripts are source-driven from unified billing sources
