"""Show live contract-grain vs portfolio-grain gap per forward month."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur  = conn.cursor()
cur.execute("""
    SELECT
        RENEWAL_MONTH,
        ROUND(SUM(ML_FORECAST)      / NULLIF(SUM(ATR),0) * 100, 2) AS CONTRACT_RATE,
        ROUND(SUM(FINANCE_FORECAST) / NULLIF(SUM(ATR),0) * 100, 2) AS PORTFOLIO_RATE,
        ROUND((SUM(ML_FORECAST) - SUM(FINANCE_FORECAST)) / NULLIF(SUM(ATR),0) * 100, 2) AS GAP_PP,
        COUNT(DISTINCT CONTRACT_ID) AS CONTRACTS
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
      AND RENEWAL_MONTH <= DATEADD('MONTH', 5, DATE_TRUNC('MONTH', CURRENT_DATE()))
      AND COHORT = 'FORWARD_OPEN'
    GROUP BY RENEWAL_MONTH
    ORDER BY RENEWAL_MONTH
""")
rows = cur.fetchall()
print(f"\n  {'MONTH':<12}  {'CONTRACT%':>10}  {'PORTFOLIO%':>12}  {'GAP (pp)':>10}  {'CONTRACTS':>10}")
print("  " + "-" * 62)
for r in rows:
    print(f"  {str(r[0]):<12}  {r[1]:>10.2f}  {r[2]:>12.2f}  {r[3]:>10.2f}  {r[4]:>10,}")
print()
conn.close()
