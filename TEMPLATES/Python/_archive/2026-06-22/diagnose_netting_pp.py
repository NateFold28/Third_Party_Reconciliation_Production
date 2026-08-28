"""Diagnose why _get_blended_netting_pp() always returns 1.6."""
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()

print("=== V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY ===")
cur = conn.cursor()
cur.execute("""
    SELECT RENEWAL_MONTH, CONTRACT_RATE_PCT, CONTRACT_ATR, CONTRACT_RENEWED
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
    WHERE RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
    ORDER BY RENEWAL_MONTH DESC
    LIMIT 10
""")
rows = cur.fetchall()
if rows:
    print(f"  {'MONTH':<12}  {'CONTRACT_RATE%':>14}  {'ATR':>12}  {'RENEWED':>12}")
    print("  " + "-"*55)
    for r in rows:
        print(f"  {str(r[0]):<12}  {str(r[1]) if r[1] is not None else 'NULL':>14}  {r[2] if r[2] else 0:>12,.0f}  {r[3] if r[3] else 0:>12,.0f}")
else:
    print("  *** TABLE EMPTY OR NO PAST MONTHS ***")

print()
print("=== V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED ===")
cur.execute("""
    SELECT RENEWAL_MONTH, ATR_PROD, ACTUAL_PROD
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED
    WHERE RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
    ORDER BY RENEWAL_MONTH DESC
    LIMIT 10
""")
rows2 = cur.fetchall()
if rows2:
    print(f"  {'MONTH':<12}  {'ATR_PROD':>12}  {'ACTUAL_PROD':>12}  {'PCT%':>8}")
    print("  " + "-"*50)
    for r in rows2:
        pct = (r[2]/r[1]*100) if r[1] else None
        print(f"  {str(r[0]):<12}  {r[1] if r[1] else 0:>12,.0f}  {r[2] if r[2] else 0:>12,.0f}  {str(round(pct,2)) if pct else 'NULL':>8}")
else:
    print("  *** TABLE EMPTY OR NO PAST MONTHS ***")

print()
print("=== JOIN CHECK (mature months that match) ===")
cur.execute("""
    SELECT
        c.RENEWAL_MONTH,
        c.CONTRACT_RATE_PCT,
        ROUND(p.ACTUAL_PROD / NULLIF(p.ATR_PROD, 0) * 100, 2) AS PORTFOLIO_PCT,
        ROUND(c.CONTRACT_RATE_PCT - (p.ACTUAL_PROD / NULLIF(p.ATR_PROD, 0) * 100), 2) AS GAP_PP
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY c
    JOIN STREAMLIT_APPS.DBO.V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED p
      ON DATE_TRUNC('MONTH', c.RENEWAL_MONTH) = DATE_TRUNC('MONTH', p.RENEWAL_MONTH)
    WHERE c.RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
      AND c.CONTRACT_RATE_PCT IS NOT NULL
      AND p.ACTUAL_PROD IS NOT NULL
    ORDER BY c.RENEWAL_MONTH DESC
    LIMIT 12
""")
rows3 = cur.fetchall()
if rows3:
    print(f"  {'MONTH':<12}  {'CONTRACT%':>10}  {'PORTFOLIO%':>12}  {'GAP (pp)':>10}")
    print("  " + "-"*50)
    for r in rows3:
        print(f"  {str(r[0]):<12}  {str(r[1]) if r[1] is not None else 'NULL':>10}  {str(r[2]) if r[2] is not None else 'NULL':>12}  {str(r[3]) if r[3] is not None else 'NULL':>10}")
    print(f"\n  Mature joined rows: {len(rows3)}")
else:
    print("  *** NO JOINED ROWS — this is why it falls back to 1.6 ***")

conn.close()
