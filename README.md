# Third-Party Reconciliation Production Pipeline

Production ingestion, reconciliation, canonical publication, and Streamlit presentation for Acronis, Auvik, Bitdefender, ESET, Exium, KeepIT, Proofpoint, SentinelOne, and Webroot.

This repository is intentionally production-only. Tests, benchmark artifacts, snapshots, ad hoc audits, retired SQL, and exploratory outputs do not belong here.

## Board-facing source of truth

The Streamlit app is the business-facing contract. Unless a request explicitly says otherwise, every reconciliation metric refers to the value shown by the app.

Authoritative app tables:

- `ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD`
- `ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SUMMARY_PROD`

Authoritative classifier:

- `Reconciliation/canonical_outcomes.py`

Vendor detail tables and native `OUTCOME_FLAG` values are diagnostic evidence only. They must never be presented as the app clear rate. For example, a vendor-native quantity-tolerance result is not interchangeable with the canonical monetary KPI.

### Canonical clear rate

For the selected vendor and loaded period:

`Clear rate = rows where OUTCOME_FLAG = 'Clear' / all published reconciliation rows`

A row is `Clear` only when vendor amount is positive, CW billed amount is positive, and CW billed amount is greater than or equal to vendor amount. Rows with zero amount on both sides are retained in shared detail for audit but excluded from published output and KPI denominators.

### Loaded-month rule

Board-facing totals and YTD metrics include only rows in `THIRD_PARTY_RECON_SUMMARY_PROD` where `DATA_LOAD_STATUS = 'LOADED'`. A vendor-month is loaded when its source usage row count is greater than zero. Never treat missing months as zero-performance months.

### Canonical outcome taxonomy

1. `Marketplace Billing Delay`
2. `Clear`
3. `Unmapped Partner`
4. `API Usage, Insufficient CW Billing`
5. `Vendor Billing, No CW Billing`
6. `Vendor Billing, Insufficient CW Billing`
7. `CW Billing, No Vendor Billing`

`Duplicated CW Invoice` remains a side flag and is not an enabled primary outcome.

## Production architecture

```text
Vendor files / Snowflake sources
        |
        v
Ingestion/*_Prod.py and production source SQL
        |
        v
THIRD_PARTY_RECON_VENDOR_USAGE_PROD
THIRD_PARTY_RECON_VENDOR_INVOICES
        |
        +--> Maps/sql/03_master_sf_partner_list.sql
        +--> Maps/sql/02_unified_reference_maps.sql
        |
        v
Reconciliation/<Vendor>_Reconciliation_Script_Prod.sql
        |
        v
THIRD_PARTY_RECON_<VENDOR>_DETAIL
THIRD_PARTY_RECON_SHARED_DETAIL
        |
        v
Reconciliation/build_third_party_recon_output_prod.py
        |
        v
THIRD_PARTY_RECON_OUTPUT_PROD
THIRD_PARTY_RECON_SUMMARY_PROD
        |
        v
App/combined_recon_app.py
```

## Production entry points

### Complete refresh

Run `Reconciliation/_run_full_refresh_pipeline.py` for the authoritative end-to-end refresh. It:

1. ingests NetSuite invoice and vendor source data;
2. backfills canonical invoice rates;
3. validates source freshness;
4. rebuilds the master Salesforce partner directory;
5. rebuilds governed partner and SKU reference views;
6. executes the reconciliation pipeline;
7. validates canonical publication and app dependencies.

The runner stops on any failed required step. `--skip-ingestion` and `--skip-maps` are controlled operational overrides, not the default production path.

### Reconciliation-only refresh

Run `Reconciliation/_run_skeleton_pipeline.py` only when source tables are already current. It rebuilds maps, vendor details, shared detail, and the canonical app tables without re-reading source files.

### Application

The production UI entry point is `App/combined_recon_app.py`. It reads the two canonical app tables directly and uses Snowflake table freshness to invalidate cached data.

## Runtime requirements

- Python 3.12 or a compatible supported version.
- Packages used by the retained source, including Streamlit, pandas, NumPy, Altair, openpyxl, and the Snowflake connector.
- Workspace-level `TEMPLATES.Python.connection` available from the workspace root.
- Snowflake access to:
  - role `DEVELOPER`
  - warehouse `REPORTING_WH`
  - database `ANALYTICS_DEV`
  - schema `DBT_NFOLD_TRANSFORMATION`
- Synced source workbooks at the ingestion defaults, or explicit source-path CLI overrides supported by each ingestion script.

Repository paths are derived from each entry point and are not tied to one clone location. Vendor workbook defaults remain operator-specific because they identify the controlled production source folders.

## Governed mapping maintenance

Production mapping state lives in Snowflake, not in local snapshots.

- Partner source table: `THIRD_PARTY_RECON_PARTNER_MAP_PROD`
- Manual overlay table: `THIRD_PARTY_RECON_PARTNER_MAP_MANUAL`
- Master Salesforce directory: `MASTER_SF_PARTNER_LIST`
- Governed partner views: `RECON_PARTNER_MAP`, `RECON_PARTNER_MAP_MONTHLY`
- Governed SKU table/view: `RECON_SKU_MAP`

Retained maintenance utilities:

- `Maps/tools/sync_sentinelone_partner_mapping.py` loads approved SentinelOne partner mappings.
- `Maps/tools/load_webroot_sku_map.py` transactionally replaces and validates the Webroot SKU slice from `Maps/seeds/WEBROOT_RECON_SKU_MAP.csv`.

After a mapping change, run the reconciliation-only pipeline or the complete refresh. The pipeline always creates `MASTER_SF_PARTNER_LIST` before rebuilding the unified references.

## Release checklist

Before a board demonstration or production promotion:

1. Confirm all expected source months are present.
2. Run the complete refresh through the governed production execution environment.
3. Confirm every required ingestion, mapping, vendor reconciliation, shared-detail, and publication step succeeds.
4. Verify only loaded vendor-months contribute to YTD metrics.
5. Confirm app headline, month table, exception queue, and vendor detail totals agree with the canonical tables.
6. Label any native vendor diagnostic explicitly; never substitute it for the app KPI.
7. Confirm no test, snapshot, benchmark, archive, audit output, or generated log was added to the repository.

## Repository layout

- `App/` — production Streamlit application.
- `Ingestion/` — production Python ingestion and invoice-rate enrichment.
- `Maps/sql/` — production governed reference SQL.
- `Maps/tools/` — approved transactional mapping loaders.
- `Maps/seeds/` — input required by the Webroot mapping loader.
- `Reconciliation/` — production vendor SQL, shared-detail SQL, orchestrators, canonical classifier, and canonical publisher.
- `.github/copilot-instructions.md` — repository governance for automated changes.

Runtime logs and generated outputs are intentionally ignored and regenerated outside version control.

## Naming conventions

- Production source directories use PascalCase: `App`, `Ingestion`, `Maps`, and `Reconciliation`.
- Vendor ingestion entry points use `<Vendor>_Vendor_Usage_Ingestion_Prod.py`.
- Vendor reconciliation entry points use `<Vendor>_Reconciliation_Script_Prod.sql`.
- Ordered shared SQL uses a two-digit execution prefix followed by lowercase snake case.
- Internal Python modules and orchestration scripts use lowercase snake case.
- Governed seed filenames mirror their Snowflake object names in uppercase snake case.
- Repository-relative paths use forward slashes and are resolved with `pathlib.Path`.
