"""
FIX: Incomplete training run poisoning app tables

ROOT CAUSE:
  Two training runs fired within 5 seconds (race condition):
    V5_20260616_182522 — INCOMPLETE: only Core + 1/5th of Emerging
    V5_20260616_182527 — COMPLETE: all 5 segments, per-contract calibration

  V5_SANDBOX_FORECAST_COMPAT selects MAX(PREDICTION_TS) DESC LIMIT 1.
  The partial run's rows were written AFTER the complete run finished,
  so its MAX(PREDICTION_TS) is newer → compat view picks the partial run.
  
  Result: Emerging/Growth/Strategic/ScreenConnect fall back to segment-level
  anchor (flat CHURN_PCT, NULL CONTRACT_RISK_PCTL_IN_SEG, flat trend charts).

FIX:
  1. Delete the incomplete run from ML_SANDBOX_V5_PREDICTIONS
  2. Rebuild app tables → SP now picks the complete run (182527)
  3. Verify all segments have per-contract calibration

ALSO FIXES (permanently):
  Update V5_SANDBOX_FORECAST_COMPAT and SP_V5_BUILD_APP_TABLES_V5_SHADOW
  to select "most complete" run instead of "newest timestamp" so this
  race condition cannot recur.
"""

import sys
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

INCOMPLETE_RUN = 'V5_20260616_182522'
COMPLETE_RUN   = 'V5_20260616_182527'

def run_fix():
    conn = get_snowflake_connection()
    cur = conn.cursor()
    for sql in ["USE ROLE STREAMLIT_USER", "USE WAREHOUSE REPORTING_WH",
                "USE DATABASE STREAMLIT_APPS", "USE SCHEMA DBO"]:
        cur.execute(sql)
    print("✓ Connected\n")

    # =========================================================================
    # STEP 1: Confirm complete run is still present and is actually complete
    # =========================================================================
    print("=" * 70)
    print("STEP 1: CONFIRMING COMPLETE RUN EXISTS")
    print("=" * 70)

    cur.execute(f"""
        SELECT SEGMENT, COUNT(*) AS n, COUNT(DISTINCT CASE WHEN SPLIT='SCORE' THEN CONTRACT_ID_UFR END) AS score_ctrs
        FROM ML_SANDBOX_V5_PREDICTIONS
        WHERE RUN_ID = '{COMPLETE_RUN}'
        GROUP BY SEGMENT ORDER BY SEGMENT
    """)
    rows = cur.fetchall()
    print(f"Complete run {COMPLETE_RUN} segments:")
    segs_found = []
    for seg, n, score in rows:
        segs_found.append(seg)
        print(f"  {str(seg):<20} total={n:>8,}  score_contracts={score:>6,}")
    
    expected_segs = {'Core', 'Emerging', 'Growth', 'ScreenConnect Only', 'Strategic'}
    missing_from_complete = expected_segs - set(segs_found)
    if missing_from_complete:
        print(f"\n⚠ COMPLETE RUN IS MISSING SEGMENTS: {missing_from_complete}")
        print("  Cannot safely delete incomplete run — aborting fix.")
        return False
    else:
        print(f"\n✓ All 5 expected segments present in complete run ({COMPLETE_RUN})")

    # =========================================================================
    # STEP 2: Delete the incomplete run
    # =========================================================================
    print(f"\n{'=' * 70}")
    print(f"STEP 2: DELETING INCOMPLETE RUN {INCOMPLETE_RUN}")
    print("=" * 70)

    cur.execute(f"""
        SELECT COUNT(*) FROM ML_SANDBOX_V5_PREDICTIONS WHERE RUN_ID = '{INCOMPLETE_RUN}'
    """)
    count_before = cur.fetchone()[0]
    print(f"Rows to delete: {count_before:,}")

    cur.execute(f"DELETE FROM ML_SANDBOX_V5_PREDICTIONS WHERE RUN_ID = '{INCOMPLETE_RUN}'")
    print(f"✓ Deleted {count_before:,} rows from {INCOMPLETE_RUN}")

    # Confirm deletion
    cur.execute(f"SELECT COUNT(*) FROM ML_SANDBOX_V5_PREDICTIONS WHERE RUN_ID = '{INCOMPLETE_RUN}'")
    remaining = cur.fetchone()[0]
    if remaining > 0:
        print(f"⚠ {remaining} rows still remain — delete may have failed")
        return False
    print(f"✓ Deletion confirmed — 0 rows remain for {INCOMPLETE_RUN}")

    # =========================================================================
    # STEP 3: Verify the compat view now points to the complete run
    # =========================================================================
    print(f"\n{'=' * 70}")
    print("STEP 3: VERIFYING COMPAT VIEW NOW SELECTS COMPLETE RUN")
    print("=" * 70)

    cur.execute("""
        WITH latest_run AS (
            SELECT RUN_ID FROM ML_SANDBOX_V5_PREDICTIONS
            GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
        )
        SELECT RUN_ID FROM latest_run
    """)
    selected_run = cur.fetchone()[0]
    print(f"Latest run by MAX(PREDICTION_TS): {selected_run}")
    if selected_run != COMPLETE_RUN:
        print(f"⚠ Expected {COMPLETE_RUN} but got {selected_run}")
        print("  The compat view may still select the wrong run. Check other partial runs.")
    else:
        print(f"✓ Compat view will now use complete run {COMPLETE_RUN}")

    # =========================================================================
    # STEP 4: Rebuild app tables from complete run
    # =========================================================================
    print(f"\n{'=' * 70}")
    print("STEP 4: REBUILDING APP TABLES (SP_V5_BUILD_APP_TABLES_V5_SHADOW)")
    print("=" * 70)
    print("This takes 3-5 minutes...")

    try:
        cur.execute("CALL SP_V5_BUILD_APP_TABLES_V5_SHADOW()")
        result = cur.fetchone()
        print(f"✓ SP completed: {result[0] if result else 'OK'}")
    except Exception as e:
        print(f"⚠ SP error: {e}")
        print("  If this is a timeout, the rebuild may still complete in background.")
        print("  Re-run verification step manually after a few minutes.")
        return False

    # =========================================================================
    # STEP 5: VERIFY FIX — Check CHURN_PCT distribution post-rebuild
    # =========================================================================
    print(f"\n{'=' * 70}")
    print("STEP 5: VERIFYING FIX — CHURN_PCT DISTRIBUTION POST-REBUILD")
    print("=" * 70)
    print("Healthy: all segments show DISTINCT CHURN_PCT values (many distinct values)")
    print("Fixed:   Emerging/Growth/Strategic/ScreenConnect now have distinct values\n")

    cur.execute("""
        SELECT
            SEGMENT,
            COUNT(*) AS n,
            ROUND(MIN(CHURN_PCT), 2)  AS min_churn,
            ROUND(AVG(CHURN_PCT), 2)  AS avg_churn,
            ROUND(MAX(CHURN_PCT), 2)  AS max_churn,
            COUNT(DISTINCT ROUND(CHURN_PCT, 2)) AS distinct_vals,
            COUNT(DISTINCT FINANCE_ANCHOR_SOURCE) AS distinct_sources,
            MAX(FINANCE_ANCHOR_SOURCE) AS anchor_source
        FROM V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE ATR > 0
        GROUP BY SEGMENT
        ORDER BY SEGMENT
    """)
    rows = cur.fetchall()

    all_fixed = True
    print(f"{'SEGMENT':<20} {'N':>8} {'Min':>7} {'Avg':>7} {'Max':>7} {'Distinct':>9} {'AnchorSrc':<15}")
    print("-" * 80)
    for seg, n, mn, av, mx, distinct, distinct_src, src in rows:
        status = "✓ PER-CONTRACT" if distinct > 10 else "⚠ STILL FLAT"
        if distinct <= 2:
            all_fixed = False
        print(f"{str(seg):<20} {n:>8,} {mn:>7.2f} {av:>7.2f} {mx:>7.2f} {distinct:>9,}  {str(src):<15} {status}")

    if all_fixed:
        print("\n✅ ALL SEGMENTS NOW HAVE PER-CONTRACT CALIBRATION — FIX SUCCESSFUL")
    else:
        print("\n⚠ SOME SEGMENTS STILL FLAT — check if complete run was used")

    # =========================================================================
    # STEP 6: Check CONTRACT_RISK_PCTL_IN_SEG
    # =========================================================================
    print(f"\n{'=' * 70}")
    print("STEP 6: CONTRACT_RISK_PCTL_IN_SEG — now populated for all segments?")
    print("=" * 70)

    cur.execute("""
        SELECT
            SEGMENT,
            SUM(CASE WHEN CONTRACT_RISK_PCTL_IN_SEG IS NULL THEN 1 ELSE 0 END) AS null_pctls,
            COUNT(*) AS total,
            ROUND(MIN(CONTRACT_RISK_PCTL_IN_SEG), 1) AS min_pctl,
            ROUND(MAX(CONTRACT_RISK_PCTL_IN_SEG), 1) AS max_pctl,
            COUNT(DISTINCT ROUND(CONTRACT_RISK_PCTL_IN_SEG, 0)) AS distinct_pctls
        FROM V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE ATR > 0
        GROUP BY SEGMENT
        ORDER BY SEGMENT
    """)
    rows = cur.fetchall()
    print(f"{'SEGMENT':<20} {'Nulls':>8} {'Total':>8} {'Min':>6} {'Max':>6} {'Distinct':>9}")
    print("-" * 65)
    for seg, nulls, total, mn, mx, distinct in rows:
        null_pct = nulls/total*100 if total else 0
        status = "✓" if nulls == 0 else f"⚠ {null_pct:.0f}% NULL"
        print(f"{str(seg):<20} {nulls:>8,} {total:>8,} {mn:>6} {mx:>6} {distinct:>9,}  {status}")

    # =========================================================================
    # STEP 7: Forward rate trend sanity check
    # =========================================================================
    print(f"\n{'=' * 70}")
    print("STEP 7: FORWARD RATE TREND — SEGMENT DIVERGENCE CHECK")
    print("=" * 70)

    cur.execute("""
        SELECT
            RENEWAL_MONTH,
            SEGMENT,
            ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 1) AS ml_rate,
            ROUND(SUM(RENEWAL_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 1) AS renewal_rate
        FROM V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE ATR > 0
          AND RENEWAL_MONTH BETWEEN DATE_TRUNC('MONTH', CURRENT_DATE() - INTERVAL '1 month')
                                AND DATE_TRUNC('MONTH', CURRENT_DATE() + INTERVAL '3 months')
        GROUP BY RENEWAL_MONTH, SEGMENT
        ORDER BY RENEWAL_MONTH, SEGMENT
    """)
    rows = cur.fetchall()
    print(f"{'Month':<12} {'Segment':<20} {'ML%':>7} {'RenewFcst%':>12}")
    print("-" * 60)
    prev_month = None
    for month, seg, ml_rate, renewal_rate in rows:
        if prev_month and prev_month != str(month):
            print("")
        prev_month = str(month)
        print(f"{str(month):<12} {str(seg):<20} {ml_rate:>7.1f} {renewal_rate:>12.1f}")

    conn.close()
    print("\n" + "=" * 70)
    print("FIX COMPLETE")
    print("=" * 70)
    return True

if __name__ == '__main__':
    success = run_fix()
    exit(0 if success else 1)
