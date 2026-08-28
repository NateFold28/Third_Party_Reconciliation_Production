# Python Templates — Renewal Forecasting Pipeline

This folder contains operational scripts for the Production Renewal Forecasting Pipeline (V5).

## Operational Scripts

| File | Purpose |
|------|---------|
| `connection.py` | Shared Snowflake connection + DataFrame fetch helpers |
| `board_audit.py` | **Full board-readiness audit** — run before any board presentation |
| `retrain_and_validate.py` | Full V5 pipeline retrain + 18-point validation |
| `rebuild_app_tables_and_deploy.py` | Deploys `SP_V5_BUILD_APP_TABLES_V5_SHADOW` proc + rebuilds app tables + redeploys app |
| `deploy_prod_streamlit_v2.py` | Deploys `Production_Forecast_App_V2.py` to Snowflake Streamlit |
| `deploy_calibration_knots.py` | Pushes isotonic calibration knots to `V5_CALIBRATION_KNOTS` |
| `recalibrate_monthly.py` | Runs monthly isotonic calibration refresh |
| `validate_and_monitor_drift.py` | Covariate shift + feature drift diagnostics |
| `production_readiness_check.py` | Quick 18-point production readiness snapshot |
| `golive_orchestrator.py` | Full go-live orchestration (retrain → deploy → validate) |

## Archived (one-off diagnostics)

See `_archive/` for historical diagnostic scripts.

## Standard setup

From repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install snowflake-connector-python[secure-local-storage] pandas lightgbm scikit-learn matplotlib
```

## Board audit

```powershell
cd TEMPLATES\Python
..\..\..\.venv\Scripts\python.exe board_audit.py
```

Expected: `9/9 gates pass | 0 failures — ALL BOARD GATES PASS`

## Fast connection check

```python
from connection import get_snowflake_connection, fetch_dataframe
df = fetch_dataframe("SELECT CURRENT_USER(), CURRENT_ROLE()")
print(df)
```
4. Date range
5. Desired output (table/chart/model)

Copilot can then:
1. Generate/update SQL in one of these templates
2. Run the script
3. Review outputs and data quality
4. Patch code/query and rerun
5. Repeat until output is acceptable
