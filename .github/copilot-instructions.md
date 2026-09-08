# Production repository instructions

## Business metric contract

- All unqualified reconciliation metric requests refer to the app.
- Use `ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SUMMARY_PROD` and `ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD` for app metrics.
- Treat `Reconciliation/canonical_outcomes.py` as the sole authoritative outcome classifier.
- Never report a vendor `*_RECON_DETAIL.OUTCOME_FLAG` result as the app clear rate.
- Native reconciliation flags and quantity-tolerance results are diagnostic evidence only. Label them explicitly as native diagnostics whenever they are requested.
- Calculate YTD and period metrics from loaded vendor-months only: require `DATA_LOAD_STATUS = 'LOADED'` in the canonical summary table or use the equivalent loaded-month set from that table.
- Do not count an absent source month as zero performance.
- Preserve the strict canonical `Clear` definition: vendor amount > 0, CW amount > 0, and CW amount >= vendor amount.
- Rows with zero amount on both sides are audit-only and excluded from app KPI denominators.

## Architecture guardrails

- Preserve the production flow: ingestion -> governed maps -> vendor detail -> shared detail -> canonical output/summary -> app.
- Do not create auxiliary Snowflake tables when an existing governed map, vendor filter, or reconciliation rule can solve the problem.
- Run `Maps/sql/03_master_sf_partner_list.sql` before `Maps/sql/02_unified_reference_maps.sql`.
- Keep vendor-native flags as evidence inputs; do not make them authoritative app classifications.
- Preserve atomic publication behavior in the reconciliation pipeline.
- Do not replace the board-facing custom HTML tables or blue/green seat-trend bars with plain dataframes.

## Repository scope

- This is a production-only repository. Do not create exploratory files, test files, snapshots, benchmark artifacts, audit outputs, generated logs, archived implementations, or one-off reporting utilities here.
- Retain only code, SQL, governed mapping inputs, and documentation directly required to ingest, reconcile, publish, operate, or display production data.
- Put approved mapping maintenance utilities under `Maps/tools/` and required governed seeds under `Maps/seeds/`.
- Runtime logs and generated outputs must remain untracked.
- Derive repository paths from `__file__`; do not add developer-specific repository paths.
- Vendor source locations may use controlled defaults, but preserve their explicit CLI overrides.

## Validation

- Do not launch the long-running complete production refresh from a local terminal unless explicitly requested.
- Validate Python syntax, imports, path references, SQL-file references, and `git diff --check` after changes.
- Use Snowflake read-only checks for business assertions and single controlled DDL/CTAS operations only when required.
- Before reporting a KPI, state whether it is canonical app-facing or vendor-native diagnostic.
