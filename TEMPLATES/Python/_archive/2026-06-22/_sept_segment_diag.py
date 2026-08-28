import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection
conn = get_snowflake_connection()
cur = conn.cursor()

# September 2026 segment breakdown to explain the dip
cur.execute(
    "SELECT SEGMENT,"
    " COUNT(DISTINCT CONTRACT_ID) AS N_CONTRACTS,"
    " ROUND(SUM(ATR)/1e6, 2) AS ATR_M,"
    " ROUND(100*SUM(RENEWAL_FORECAST)/NULLIF(SUM(ATR),0), 2) AS MODEL_RATE_PCT,"
    " ROUND(AVG(CASE WHEN CONTRACT_RISK_SCORE IS NOT NULL THEN CONTRACT_RISK_SCORE END), 1) AS AVG_RISK_SCORE"
    " FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"
    " WHERE RENEWAL_MONTH = '2026-09-01'"
    "   AND RUN_ID != 'V5_ANCHOR_FALLBACK'"
    " GROUP BY 1 ORDER BY ATR_M DESC"
)
print("=== Sept 2026 by segment ===")
print(f"{'SEGMENT':<20} {'N_CTR':>7} {'ATR_M':>7} {'MODEL%':>8} {'AVG_RISK':>10}")
for row in cur.fetchall():
    print(f"{str(row[0]):<20} {row[1]:>7} {row[2]:>7} {row[3]:>8} {str(row[4]):>10}")

# Compare same breakdown for Aug and Oct (adjacent months) to isolate Sept
for mo, label in [('2026-08-01', 'Aug 2026'), ('2026-10-01', 'Oct 2026')]:
    cur.execute(
        "SELECT SEGMENT,"
        " ROUND(100*SUM(RENEWAL_FORECAST)/NULLIF(SUM(ATR),0), 2) AS MODEL_RATE_PCT"
        f" FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"
        f" WHERE RENEWAL_MONTH = '{mo}'"
        "   AND RUN_ID != 'V5_ANCHOR_FALLBACK'"
        " GROUP BY 1 ORDER BY 2"
    )
    print(f"\n=== {label} segment rates ===")
    for row in cur.fetchall():
        print(f"  {str(row[0]):<20} {row[1]:>8}%")

# Pipeline status check
cur.execute(
    "SELECT RUN_ID, MAX(PREDICTION_TS) AS TS,"
    " COUNT(*) AS ROWS, COUNT_IF(SPLIT='SCORE') AS SCORED,"
    " ROUND(AVG(CASE WHEN SPLIT='SCORE' THEN PRED_RENEW_RATE_PORTFOLIO END)*100,2) AS AVG_PORTFOLIO_RATE,"
    " ROUND(AVG(CASE WHEN SPLIT='SCORE' THEN PRED_RENEW_RATE_FINAL END)*100,2) AS AVG_CONTRACT_RATE"
    " FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
    " GROUP BY 1 ORDER BY 2 DESC LIMIT 3"
)
print("\n=== Latest pipeline runs (dual-grain check) ===")
print(f"{'RUN_ID':<40} {'TS':>22} {'ROWS':>6} {'SCORED':>7} {'PORT%':>8} {'CTR%':>8}")
for row in cur.fetchall():
    print(f"{str(row[0]):<40} {str(row[1]):>22} {row[2]:>6} {row[3]:>7} {str(row[4]):>8} {str(row[5]):>8}")

cur.close()
conn.close()
