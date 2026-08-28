"""Validate trailing-12-month netting value."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()
cur.execute("""
    SELECT
        c.RENEWAL_MONTH,
        c.CONTRACT_RATE_PCT,
        ROUND(p.ACTUAL_PROD / NULLIF(p.ATR_PROD, 0) * 100, 4) AS PORT_PCT,
        ROUND(c.CONTRACT_RATE_PCT - (p.ACTUAL_PROD / NULLIF(p.ATR_PROD, 0) * 100), 4) AS GAP_PP
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY c
    JOIN STREAMLIT_APPS.DBO.V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED p
      ON DATE_TRUNC('MONTH', c.RENEWAL_MONTH) = DATE_TRUNC('MONTH', p.RENEWAL_MONTH)
    WHERE c.RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
      AND c.RENEWAL_MONTH >= DATEADD('MONTH', -12, DATE_TRUNC('MONTH', CURRENT_DATE()))
      AND c.CONTRACT_RATE_PCT IS NOT NULL
    ORDER BY c.RENEWAL_MONTH
""")
rows = cur.fetchall()
gaps = [r[3] for r in rows if r[3] is not None]
print(f"\n  Trailing-12mo window: {len(gaps)} months")
print(f"  {'MONTH':<12}  {'CONTRACT%':>10}  {'PORTFOLIO%':>12}  {'GAP (pp)':>10}")
print("  " + "-"*50)
for r in rows:
    print(f"  {str(r[0]):<12}  {r[1]:>10.2f}  {r[2]:>12.2f}  {r[3]:>+10.2f}")
print()
print(f"  New netting (12-mo mean): {sum(gaps)/len(gaps):.4f} pp")
print(f"  Old netting (64-mo trimmed mean): ~1.60 pp")
conn.close()
