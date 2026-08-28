import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection
conn = get_snowflake_connection()
cur = conn.cursor()
cur.execute(
    "SELECT RENEWAL_MONTH, CONTRACT_FORECAST_RATE_PCT, IS_ANCHOR_FALLBACK"
    " FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY"
    " WHERE RENEWAL_MONTH BETWEEN '2026-06-01' AND '2027-06-01' ORDER BY 1"
)
print("MONTH          CTR_FCST%  FALLBACK?")
for r in cur.fetchall():
    print(f"  {str(r[0]):<14} {str(r[1]):>9}  {str(r[2])}")
cur.close()
conn.close()
