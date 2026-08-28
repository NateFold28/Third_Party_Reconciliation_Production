import sys
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()

print('=== MODEL RUNS ===')
cur.execute("SHOW COLUMNS IN TABLE STREAMLIT_APPS.DBO.ML_SANDBOX_V5_MODEL_RUNS")
run_cols = [r[2] for r in cur.fetchall()]
select_cols = [c for c in ["RUN_ID", "RUN_TS", "BACKTEST_ABS_ERROR_PP", "CHAMPION_GATE_PASSED"] if c in run_cols]
cur.execute(
    f"SELECT {', '.join(select_cols)} FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_MODEL_RUNS ORDER BY RUN_TS DESC LIMIT 5"
)
for r in cur.fetchall():
    print(r)

print('\n=== PREDICTIONS FRESHNESS ===')
cur.execute("""
SELECT MAX(PREDICTION_TS), COUNT(*)
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
""")
print(cur.fetchone())

print('\n=== FEATURE STORE FRESHNESS ===')
cur.execute("""
SELECT MAX(AS_OF_DATE), COUNT(*)
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE
""")
print(cur.fetchone())

print('\n=== APP DETAIL FRESHNESS ===')
cur.execute("""
SELECT MAX(RENEWAL_MONTH), COUNT(*)
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
""")
print(cur.fetchone())

print('\n=== PIPELINE LOG RECENT ===')
cur.execute("""
SELECT TRIGGERED_AT, SOURCE, STATUS, MESSAGE
FROM STREAMLIT_APPS.DBO.V5_PIPELINE_RUN_LOG
ORDER BY TRIGGERED_AT DESC
LIMIT 25
""")
for r in cur.fetchall():
    print(r)
