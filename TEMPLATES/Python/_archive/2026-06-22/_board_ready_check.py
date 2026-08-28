import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection
conn = get_snowflake_connection()
cur = conn.cursor()

# Board gate metrics from latest run
cur.execute(
    "SELECT TRIGGERED_AT, STATUS, MESSAGE"
    " FROM STREAMLIT_APPS.DBO.V5_PIPELINE_RUN_LOG"
    " ORDER BY TRIGGERED_AT DESC LIMIT 3"
)
print("=== Pipeline run log (latest) ===")
for r in cur.fetchall():
    print(f"  {r[0]} | {r[1]} | {str(r[2])[:200]}")

# Check grain inversion invariant for forward months in the latest run
cur.execute(
    "SELECT COUNT(*) AS TOTAL_FWD,"
    " COUNT_IF(PRED_RENEW_RATE_FINAL >= PRED_RENEW_RATE_PORTFOLIO) AS CTR_GE_PORT,"
    " COUNT_IF(PRED_RENEW_RATE_FINAL < PRED_RENEW_RATE_PORTFOLIO) AS CTR_LT_PORT_VIOLATIONS,"
    " ROUND(AVG(PRED_RENEW_RATE_FINAL)*100,2) AS AVG_CTR_PCT,"
    " ROUND(AVG(PRED_RENEW_RATE_PORTFOLIO)*100,2) AS AVG_PORT_PCT,"
    " ROUND((AVG(PRED_RENEW_RATE_FINAL) - AVG(PRED_RENEW_RATE_PORTFOLIO))*100,2) AS GAP_PP"
    " FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
    " WHERE SPLIT = 'SCORE'"
    " AND RUN_ID = (SELECT RUN_ID FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
    "              GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1)"
)
print("\n=== Dual-Grain Invariant Check (SCORE split, latest run) ===")
print(f"{'TOTAL':>8} {'CTR>=PORT':>10} {'VIOLATIONS':>11} {'AVG_CTR%':>10} {'AVG_PORT%':>10} {'GAP_PP':>8}")
for r in cur.fetchall():
    print(f"{r[0]:>8} {r[1]:>10} {r[2]:>11} {str(r[3]):>10} {str(r[4]):>10} {str(r[5]):>8}")

# Per-month forward forecast sanity (contract vs portfolio)
cur.execute(
    "SELECT d.RENEWAL_MONTH,"
    " ROUND(SUM(d.FINANCE_FORECAST)/NULLIF(SUM(d.ATR),0)*100,2) AS PORT_RATE,"
    " c.CONTRACT_FORECAST_RATE_PCT AS CTR_RATE,"
    " ROUND(c.CONTRACT_FORECAST_RATE_PCT - SUM(d.FINANCE_FORECAST)/NULLIF(SUM(d.ATR),0)*100, 2) AS GAP_PP"
    " FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d"
    " JOIN STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY c ON c.RENEWAL_MONTH = d.RENEWAL_MONTH"
    " WHERE d.RENEWAL_MONTH >= '2026-06-01'"
    " AND d.RUN_ID != 'V5_ANCHOR_FALLBACK'"
    " GROUP BY d.RENEWAL_MONTH, c.CONTRACT_FORECAST_RATE_PCT ORDER BY 1"
)
print("\n=== Forward Month Grain Comparison (CTR should be > PORT) ===")
print(f"{'MONTH':<12} {'PORT%':>8} {'CTR%':>8} {'GAP_PP':>8} STATUS")
for r in cur.fetchall():
    pp = r[3]
    status = "OK contract>portfolio" if pp and float(pp) > 0 else "INVERTED"
    print(f"{str(r[0]):<12} {str(r[1]):>8} {str(r[2]):>8} {str(pp):>8} {status}")

cur.close()
conn.close()
