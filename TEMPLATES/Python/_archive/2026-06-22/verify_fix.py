"""
Quick verification — checks if the fix was applied correctly.
"""
import sys
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

def run():
    conn = get_snowflake_connection()
    cur = conn.cursor()
    for sql in ["USE ROLE STREAMLIT_USER", "USE WAREHOUSE REPORTING_WH",
                "USE DATABASE STREAMLIT_APPS", "USE SCHEMA DBO"]:
        cur.execute(sql)
    print("✓ Connected\n")

    # 1) Did the delete happen?
    print("VERIFY 1: Incomplete run row count")
    cur.execute("SELECT COUNT(*) FROM ML_SANDBOX_V5_PREDICTIONS WHERE RUN_ID = 'V5_20260616_182522'")
    count = cur.fetchone()[0]
    if count == 0:
        print("  ✓ V5_20260616_182522 has been deleted")
    else:
        print(f"  ⚠ V5_20260616_182522 still has {count:,} rows — delete did not complete")

    # 2) What is the current latest run?
    print("\nVERIFY 2: Current latest run in predictions table")
    cur.execute("""
        SELECT RUN_ID, COUNT(*) AS n, COUNT(DISTINCT SEGMENT) AS segs
        FROM ML_SANDBOX_V5_PREDICTIONS
        GROUP BY RUN_ID
        ORDER BY MAX(PREDICTION_TS) DESC
        LIMIT 3
    """)
    rows = cur.fetchall()
    for run_id, n, segs in rows:
        print(f"  {run_id}: {n:,} rows, {segs} segments")

    # 3) Does the app detail now have per-contract calibration?
    print("\nVERIFY 3: App detail CHURN_PCT distinctness by segment")
    cur.execute("""
        SELECT SEGMENT,
               COUNT(DISTINCT ROUND(CHURN_PCT, 2)) AS distinct_vals,
               MAX(FINANCE_ANCHOR_SOURCE) AS src,
               COUNT(*) AS n
        FROM V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE ATR > 0
        GROUP BY SEGMENT ORDER BY SEGMENT
    """)
    rows = cur.fetchall()
    all_good = True
    for seg, distinct, src, n in rows:
        status = "✓ PER-CONTRACT" if distinct > 10 else "⚠ STILL FLAT"
        if distinct <= 2:
            all_good = False
        print(f"  {str(seg):<22} distinct={distinct:>6,}  source={src:<10} n={n:,}  {status}")

    if all_good:
        print("\n✅ ALL SEGMENTS HAVE PER-CONTRACT CALIBRATION")
    else:
        print("\n⚠ FIX NOT YET COMPLETE — SP may not have rebuilt yet")

    # 4) CONTRACT_RISK_PCTL_IN_SEG
    print("\nVERIFY 4: CONTRACT_RISK_PCTL_IN_SEG null counts")
    cur.execute("""
        SELECT SEGMENT,
               SUM(CASE WHEN CONTRACT_RISK_PCTL_IN_SEG IS NULL THEN 1 ELSE 0 END) AS nulls,
               COUNT(*) AS total
        FROM V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE ATR > 0
        GROUP BY SEGMENT ORDER BY SEGMENT
    """)
    rows = cur.fetchall()
    for seg, nulls, total in rows:
        null_pct = nulls/total*100 if total else 0
        status = "✓" if nulls == 0 else f"⚠ {null_pct:.0f}% still NULL"
        print(f"  {str(seg):<22} nulls={nulls:>7,}  total={total:>8,}  {status}")

    conn.close()

if __name__ == '__main__':
    run()
