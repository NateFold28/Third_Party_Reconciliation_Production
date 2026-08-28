import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection
conn = get_snowflake_connection()
cur = conn.cursor()

# 1. What does the monthly rollup show? (EFFECTIVE_FORECAST = portfolio series in app)
cur.execute(
    "SELECT RENEWAL_MONTH,"
    " ROUND(SUM(EFFECTIVE_FORECAST)/NULLIF(SUM(ATR),0)*100, 2) AS PORTFOLIO_RATE_PCT,"
    " ROUND(SUM(MODEL_FORECAST)/NULLIF(SUM(ATR),0)*100, 2)     AS MODEL_RATE_PCT,"
    " ROUND(SUM(ACTUAL_RENEWAL_ARR)/NULLIF(SUM(ATR),0)*100, 2) AS ACTUAL_PCT"
    " FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_MONTHLY_ROLLUP"
    " WHERE RENEWAL_MONTH BETWEEN '2023-01-01' AND '2026-10-01'"
    " GROUP BY 1 ORDER BY 1"
)
print("=== V5_SANDBOX_APP_MONTHLY_ROLLUP (EFFECTIVE_FORECAST = 'portfolio' series) ===")
print(f"{'MONTH':<12} {'PORT_PCT':>10} {'MODEL_PCT':>10} {'ACTUAL_PCT':>10}")
for row in cur.fetchall():
    print(f"{str(row[0]):<12} {str(row[1]):>10} {str(row[2]):>10} {str(row[3]):>10}")

# 2. What does contract LVL monthly show?
cur.execute(
    "SELECT RENEWAL_MONTH,"
    " CONTRACT_FORECAST_RATE_PCT,"
    " CONTRACT_RATE_PCT"
    " FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY"
    " WHERE RENEWAL_MONTH BETWEEN '2023-01-01' AND '2026-10-01'"
    " ORDER BY 1"
)
print("\n=== V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY (CONTRACT series) ===")
print(f"{'MONTH':<12} {'CTR_FCST_PCT':>14} {'CTR_ACT_PCT':>12}")
for row in cur.fetchall():
    print(f"{str(row[0]):<12} {str(row[1]):>14} {str(row[2]):>12}")

# 3. Side-by-side comparison: portfolio (rollup) vs contract (LVL monthly)
cur.execute(
    "SELECT r.RENEWAL_MONTH,"
    " ROUND(SUM(r.EFFECTIVE_FORECAST)/NULLIF(SUM(r.ATR),0)*100,2) AS PORT_RATE,"
    " c.CONTRACT_FORECAST_RATE_PCT AS CTR_RATE,"
    " ROUND(c.CONTRACT_FORECAST_RATE_PCT - SUM(r.EFFECTIVE_FORECAST)/NULLIF(SUM(r.ATR),0)*100, 2) AS CTR_MINUS_PORT_PP"
    " FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_MONTHLY_ROLLUP r"
    " JOIN STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY c"
    "   ON c.RENEWAL_MONTH = r.RENEWAL_MONTH"
    " WHERE r.RENEWAL_MONTH BETWEEN '2024-01-01' AND '2026-10-01'"
    " GROUP BY r.RENEWAL_MONTH, c.CONTRACT_FORECAST_RATE_PCT"
    " ORDER BY 1"
)
print("\n=== Side-by-side PORT vs CTR (positive = CTR higher = CORRECT) ===")
print(f"{'MONTH':<12} {'PORT%':>8} {'CTR%':>8} {'CTR-PORT pp':>12} {'OK?':>6}")
for row in cur.fetchall():
    pp = row[3]
    ok = "YES" if pp is not None and pp >= 0 else ("???" if pp is None else "INVERTED")
    print(f"{str(row[0]):<12} {str(row[1]):>8} {str(row[2]):>8} {str(pp):>12} {ok:>6}")

# 4. Check what the v5 predictions table has (are dual-grain cols populated?)
cur.execute(
    "SELECT RUN_ID, MAX(PREDICTION_TS) AS TS,"
    " COUNT_IF(SPLIT='SCORE' AND PRED_RENEW_RATE_PORTFOLIO IS NOT NULL) AS SCORED_WITH_PORT,"
    " COUNT_IF(SPLIT='SCORE') AS SCORED_TOTAL,"
    " ROUND(AVG(CASE WHEN SPLIT='SCORE' THEN PRED_RENEW_RATE_PORTFOLIO END)*100,2) AS AVG_PORT_RATE,"
    " ROUND(AVG(CASE WHEN SPLIT='SCORE' THEN PRED_RENEW_RATE_FINAL END)*100,2) AS AVG_CTR_RATE"
    " FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
    " GROUP BY 1 ORDER BY 2 DESC LIMIT 3"
)
print("\n=== Latest runs in ML_SANDBOX_V5_PREDICTIONS (source of truth) ===")
print(f"{'RUN_ID':<40} {'PORT_W_DATA':>12} {'SCORED_TOT':>10} {'AVG_PORT%':>10} {'AVG_CTR%':>10}")
for row in cur.fetchall():
    print(f"{str(row[0]):<40} {row[2]:>12} {row[3]:>10} {str(row[4]):>10} {str(row[5]):>10}")

cur.close()
conn.close()
