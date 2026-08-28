"""
Calibration state probe — reads V5_CALIBRATION_POLICY and V5_APP_BACKTEST
to understand current offsets and what the backtest table contains.
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection(); cur = conn.cursor()
for s in ["USE ROLE STREAMLIT_USER","USE WAREHOUSE REPORTING_WH",
          "USE DATABASE STREAMLIT_APPS","USE SCHEMA DBO"]:
    cur.execute(s)

print("=== V5_CALIBRATION_POLICY (active rows) ===")
try:
    cur.execute("""
        SELECT SEGMENT, OFFSET_PP, EFFECTIVE_DATE, EXPIRY_DATE
        FROM V5_CALIBRATION_POLICY
        WHERE CURRENT_DATE() BETWEEN EFFECTIVE_DATE AND COALESCE(EXPIRY_DATE, '9999-12-31')
        ORDER BY SEGMENT
    """)
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"  {r[0]:<25} offset={r[1]:>+.2f}pp  eff={r[2]}  exp={r[3]}")
    else:
        print("  (no active policy rows — calibration offsets = 0 for all segments)")
except Exception as e:
    print(f"  ERROR: {e}")

print()
print("=== V5_APP_BACKTEST ===")
try:
    cur.execute("SELECT COUNT(*), MIN(RENEWAL_MONTH), MAX(RENEWAL_MONTH) FROM V5_APP_BACKTEST")
    n, mn, mx = cur.fetchone()
    print(f"  {n:,} rows  |  {str(mn)[:7]} → {str(mx)[:7]}")

    cur.execute("""
        SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'DBO' AND TABLE_NAME = 'V5_APP_BACKTEST'
        ORDER BY ORDINAL_POSITION
    """)
    cols = [r[0] for r in cur.fetchall()]
    print(f"  Columns: {cols}")
except Exception as e:
    print(f"  ERROR: {e}")

print()
print("=== V5_CALIBRATION_PROPOSALS (most recent) ===")
try:
    cur.execute("""
        SELECT PROPOSAL_ID, SEGMENT, FLAT_BIAS_PP, RECENCY_WEIGHTED_BIAS_PP,
               PROPOSED_OFFSET_PP, CURRENT_OFFSET_PP, OFFSET_CHANGE_PP, DRIFT_FLAG
        FROM V5_CALIBRATION_PROPOSALS
        WHERE PROPOSAL_ID = (SELECT MAX(PROPOSAL_ID) FROM V5_CALIBRATION_PROPOSALS)
        ORDER BY SEGMENT
    """)
    rows = cur.fetchall()
    if rows:
        print(f"  {'Proposal':30}  {'Segment':20}  {'FlatBias':>9}  {'RecBias':>8}  {'PropOff':>8}  {'CurOff':>7}  {'Change':>7}  {'Flag'}")
        print("  " + "-"*100)
        for r in rows:
            print(f"  {str(r[0]):30}  {str(r[1]):20}  {r[2] or 0:>+9.2f}  {r[3] or 0:>+8.2f}  {r[4] or 0:>+8.2f}  {r[5] or 0:>+7.2f}  {r[6] or 0:>+7.2f}  {r[7]}")
    else:
        print("  (no prior proposals — procedure has not been run yet)")
except Exception as e:
    print(f"  ERROR: {e}")

conn.close()
