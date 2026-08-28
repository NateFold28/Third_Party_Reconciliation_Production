import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection
conn = get_snowflake_connection()
cur = conn.cursor()

cur.execute(
    "SELECT RENEWAL_MONTH, COUNT(DISTINCT CONTRACT_ID) AS N_CONTRACTS,"
    " ROUND(SUM(ATR)/1e6, 2) AS ATR_M,"
    " ROUND(100*SUM(RENEWAL_FORECAST)/NULLIF(SUM(ATR),0), 2) AS MODEL_RATE_PCT,"
    " ROUND(100*SUM(COALESCE(ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(ATR),0), 2) AS ACTUAL_TO_DATE_PCT"
    " FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"
    " WHERE RENEWAL_MONTH BETWEEN '2026-04-01' AND '2026-10-01'"
    "   AND RUN_ID != 'V5_ANCHOR_FALLBACK'"
    " GROUP BY 1 ORDER BY 1"
)
print("=== Forward months (ATR + Model Rate) ===")
print(f"{'MONTH':<12} {'N_CTR':>8} {'ATR_M':>8} {'MODEL%':>8} {'ACT_TO_DATE%':>14}")
for row in cur.fetchall():
    print(f"{str(row[0]):<12} {row[1]:>8} {row[2]:>8} {row[3]:>8} {row[4]:>14}")

cur.execute(
    "SELECT YEAR(RENEWAL_MONTH) AS YR,"
    " ROUND(SUM(ATR)/1e6, 2) AS ATR_M,"
    " ROUND(100*SUM(COALESCE(ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(ATR),0), 2) AS ACTUAL_RATE_PCT"
    " FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"
    " WHERE MONTH(RENEWAL_MONTH) = 9"
    "   AND IS_MATURED_MONTH = TRUE"
    "   AND RUN_ID != 'V5_ANCHOR_FALLBACK'"
    " GROUP BY 1 ORDER BY 1"
)
print("\n=== Historical September actuals (each year) ===")
print(f"{'YEAR':<8} {'ATR_M':>8} {'ACTUAL%':>10}")
for row in cur.fetchall():
    print(f"{row[0]:<8} {row[1]:>8} {row[2]:>10}")

cur.execute(
    "SELECT MONTH(RENEWAL_MONTH) AS MO,"
    " ROUND(AVG(sub.ATR_M), 2) AS AVG_ATR_M,"
    " ROUND(AVG(sub.ACTUAL_RATE_PCT), 2) AS AVG_RATE_PCT"
    " FROM ("
    "   SELECT RENEWAL_MONTH,"
    "          SUM(ATR)/1e6 AS ATR_M,"
    "          100*SUM(COALESCE(ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(ATR),0) AS ACTUAL_RATE_PCT"
    "   FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"
    "   WHERE IS_MATURED_MONTH = TRUE AND RUN_ID != 'V5_ANCHOR_FALLBACK'"
    "   GROUP BY 1"
    " ) sub"
    " GROUP BY 1 ORDER BY 1"
)
print("\n=== Avg ATR + Actual Rate by calendar month (historical avg across years) ===")
print(f"{'MO':<5} {'AVG_ATR_M':>10} {'AVG_RATE%':>10}")
for row in cur.fetchall():
    print(f"{row[0]:<5} {row[1]:>10} {row[2]:>10}")

cur.close()
conn.close()
