import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection
conn = get_snowflake_connection()
cur = conn.cursor()

# 1. What's in ML_SANDBOX_V5_PREDICTIONS (source of truth)?
cur.execute(
    "SELECT RUN_ID, MAX(PREDICTION_TS) AS TS, COUNT_IF(SPLIT='SCORE') AS SCORED,"
    " ROUND(AVG(CASE WHEN SPLIT='SCORE' THEN FINAL_DOLLARS END)/NULLIF(AVG(CASE WHEN SPLIT='SCORE' THEN ATR END),0)*100,2) AS AVG_CTR_RATE,"
    " ROUND(AVG(CASE WHEN SPLIT='SCORE' THEN FINAL_DOLLARS_PORTFOLIO END)/NULLIF(AVG(CASE WHEN SPLIT='SCORE' THEN ATR END),0)*100,2) AS AVG_PORT_RATE,"
    " COUNT_IF(SPLIT='SCORE' AND FINAL_DOLLARS_PORTFOLIO IS NOT NULL) AS PORT_NOT_NULL"
    " FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS GROUP BY 1 ORDER BY 2 DESC LIMIT 3"
)
print("=== ML_SANDBOX_V5_PREDICTIONS (source of truth) ===")
print(f"{'RUN_ID':<40} {'SCORED':>8} {'CTR%':>8} {'PORT%':>8} {'PORT_NN':>8} CORRECT?")
for r in cur.fetchall():
    ok = "YES" if r[3] and r[4] and float(r[3]) > float(r[4]) else "NO/NULL"
    print(f"{str(r[0]):<40} {r[2]:>8} {str(r[3]):>8} {str(r[4]):>8} {r[5]:>8} {ok}")

# 2. What's in V5_SANDBOX_APP_CONTRACT_DETAIL (FINANCE_FORECAST should be portfolio)?
cur.execute(
    "SELECT RUN_ID, COUNT(*) AS ROW_CNT,"
    " ROUND(SUM(FINANCE_FORECAST)/NULLIF(SUM(ATR),0)*100,2) AS FINANCE_RATE,"
    " ROUND(SUM(ML_FORECAST)/NULLIF(SUM(ATR),0)*100,2) AS ML_RATE,"
    " COUNT_IF(FINANCE_FORECAST IS NOT NULL) AS FIN_NN"
    " FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"
    " WHERE RUN_ID != 'V5_ANCHOR_FALLBACK' AND COHORT = 'FORWARD_OPEN'"
    " GROUP BY 1 ORDER BY MAX(BUILT_AT) DESC LIMIT 3"
)
print("\n=== V5_SANDBOX_APP_CONTRACT_DETAIL (FINANCE_FORECAST should be portfolio / lower) ===")
print(f"{'RUN_ID':<40} {'ROW_CNT':>7} {'FIN_RATE%':>10} {'ML_RATE%':>9} {'FIN_NN':>8}")
for r in cur.fetchall():
    print(f"{str(r[0]):<40} {r[1]:>7} {str(r[2]):>10} {str(r[3]):>9} {r[4]:>8}")

# 3. V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY (CONTRACT_FORECAST_RATE_PCT should be contract / higher)
cur.execute(
    "SELECT RENEWAL_MONTH, CONTRACT_FORECAST_RATE_PCT, CONTRACT_RATE_PCT"
    " FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY"
    " WHERE RENEWAL_MONTH BETWEEN '2026-06-01' AND '2026-09-01' ORDER BY 1"
)
print("\n=== V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY (contract-grain, should be HIGHER than portfolio) ===")
print(f"{'MONTH':<12} {'CTR_FCST%':>10} {'CTR_ACT%':>10}")
for r in cur.fetchall():
    print(f"{str(r[0]):<12} {str(r[1]):>10} {str(r[2]):>10}")

# 4. Monthly comparison: FINANCE_RATE (portfolio, from detail) vs CONTRACT_FORECAST_RATE_PCT
cur.execute(
    "SELECT d.RENEWAL_MONTH,"
    " ROUND(SUM(d.FINANCE_FORECAST)/NULLIF(SUM(d.ATR),0)*100,2) AS PORT_RATE,"
    " c.CONTRACT_FORECAST_RATE_PCT AS CTR_RATE,"
    " ROUND(c.CONTRACT_FORECAST_RATE_PCT - SUM(d.FINANCE_FORECAST)/NULLIF(SUM(d.ATR),0)*100,2) AS GAP_PP"
    " FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d"
    " JOIN STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY c ON c.RENEWAL_MONTH = d.RENEWAL_MONTH"
    " WHERE d.RENEWAL_MONTH BETWEEN '2025-01-01' AND '2026-10-01'"
    " AND d.RUN_ID != 'V5_ANCHOR_FALLBACK'"
    " GROUP BY d.RENEWAL_MONTH, c.CONTRACT_FORECAST_RATE_PCT ORDER BY 1"
)
print("\n=== PORT vs CTR side-by-side (GAP should be positive = CTR > PORT) ===")
print(f"{'MONTH':<12} {'PORT%':>8} {'CTR%':>8} {'GAP_PP':>8} CORRECT?")
for r in cur.fetchall():
    pp = r[3]
    ok = "YES" if pp is not None and float(pp) > 0 else ("--" if pp is None else "INVERTED!")
    print(f"{str(r[0]):<12} {str(r[1]):>8} {str(r[2]):>8} {str(pp):>8} {ok}")

# 5. Backtest table - does it have the new columns?
cur.execute(
    "SELECT COLUMN_NAME FROM STREAMLIT_APPS.INFORMATION_SCHEMA.COLUMNS"
    " WHERE TABLE_SCHEMA = 'DBO' AND TABLE_NAME = 'V5_SANDBOX_APP_BACKTEST'"
    " ORDER BY ORDINAL_POSITION"
)
print("\n=== V5_SANDBOX_APP_BACKTEST columns ===")
cols = [r[0] for r in cur.fetchall()]
print(", ".join(cols))
has_contract = "PREDICTED_RATE_PCT_CONTRACT" in cols
print(f"Has PREDICTED_RATE_PCT_CONTRACT: {has_contract}")

cur.close()
conn.close()
