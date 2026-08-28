import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection
conn = get_snowflake_connection()
cur = conn.cursor()
cur.execute(
    "SELECT RUN_ID, MAX(PREDICTION_TS) AS TS, COUNT(*) AS ROW_COUNT,"
    " COUNT_IF(SPLIT='SCORE') AS SCORED,"
    " ROUND(AVG(CASE WHEN SPLIT='SCORE' THEN PRED_RENEW_RATE_PORTFOLIO END)*100,2) AS AVG_PORTFOLIO_RATE,"
    " ROUND(AVG(CASE WHEN SPLIT='SCORE' THEN PRED_RENEW_RATE_FINAL END)*100,2) AS AVG_CONTRACT_RATE"
    " FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
    " GROUP BY 1 ORDER BY 2 DESC LIMIT 3"
)
print(f"{'RUN_ID':<40} {'ROW_CT':>7} {'SCORED':>7} {'PORT%':>8} {'CTR%':>8} {'INVERTED?':>10}")
for row in cur.fetchall():
    port = row[4]
    ctr = row[5]
    inverted = "YES" if (port is not None and ctr is not None and port > ctr) else ("N/A" if port is None else "NO")
    print(f"{str(row[0]):<40} {row[2]:>7} {row[3]:>7} {str(port):>8} {str(ctr):>8} {inverted:>10}")
cur.close()
conn.close()
