"""
Deep-dive into ML_SANDBOX_V5_PREDICTIONS to confirm which segments/runs exist
and what the pipeline log shows for the most recent training run.
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

    # =========================================================================
    # CONFIRM: what segments are in ML_SANDBOX_V5_PREDICTIONS for latest run?
    # =========================================================================
    print("=" * 70)
    print("PREDICTIONS TABLE — latest 3 runs, segment coverage")
    print("=" * 70)
    try:
        cur.execute("""
            SELECT
                RUN_ID,
                MAX(PREDICTION_TS) AS latest_ts,
                SEGMENT,
                COUNT(*) AS n_rows,
                COUNT(DISTINCT CONTRACT_ID_UFR) AS contracts,
                COUNT(DISTINCT CASE WHEN SPLIT='SCORE' THEN CONTRACT_ID_UFR END) AS score_contracts,
                ROUND(AVG(P_CHURN_CAL), 4) AS avg_churn,
                COUNT(DISTINCT ROUND(P_CHURN_CAL, 4)) AS distinct_churn_vals
            FROM ML_SANDBOX_V5_PREDICTIONS
            WHERE RUN_ID IN (
                SELECT RUN_ID FROM ML_SANDBOX_V5_PREDICTIONS
                GROUP BY RUN_ID
                ORDER BY MAX(PREDICTION_TS) DESC
                LIMIT 3
            )
            GROUP BY RUN_ID, SEGMENT
            ORDER BY MAX(PREDICTION_TS) DESC, RUN_ID, SEGMENT
        """)
        rows = cur.fetchall()
        prev_run = None
        print(f"{'RUN_ID':<35} {'TS':<22} {'SEGMENT':<20} {'N':>8} {'SCORE':>7} {'Distinct Churn':>14}")
        print("-" * 115)
        for run_id, ts, seg, n, contracts, score_ctrs, avg_churn, distinct_churn in rows:
            if run_id != prev_run and prev_run is not None:
                print("")
            prev_run = run_id
            flag = " ⚠ FLAT (fallback)" if distinct_churn <= 2 else " ✓"
            print(f"{str(run_id):<35} {str(ts)[:19]:<22} {str(seg):<20} {n:>8,} {score_ctrs:>7,} {distinct_churn:>14,}{flag}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # CHECK: pipeline log — most recent training run status
    # =========================================================================
    print("\n" + "=" * 70)
    print("PIPELINE LOG — most recent runs")
    print("=" * 70)
    try:
        cur.execute("""
            SELECT RUN_ID, STATUS, NOTE, BUILT_AT
            FROM ML_SANDBOX_V5_MODEL_RUNS
            ORDER BY BUILT_AT DESC
            LIMIT 10
        """)
        rows = cur.fetchall()
        print(f"{'RUN_ID':<35} {'STATUS':<12} {'NOTE':<60} {'BUILT_AT':<20}")
        print("-" * 130)
        for run_id, status, note, built_at in rows:
            note_str = str(note)[:55] if note else ''
            print(f"{str(run_id):<35} {str(status):<12} {note_str:<60} {str(built_at)[:19]}")
    except Exception as e:
        print(f"⚠ Error (try MODEL_RUNS): {e}")
        try:
            cur.execute("""
                SELECT RUN_ID, NOTES, BUILT_AT
                FROM ML_SANDBOX_V5_MODEL_RUNS
                ORDER BY BUILT_AT DESC
                LIMIT 10
            """)
            rows = cur.fetchall()
            for run_id, notes, built_at in rows:
                notes_str = str(notes)[:80] if notes else ''
                print(f"  {str(run_id):<35} {str(built_at)[:19]}  {notes_str}")
        except Exception as e2:
            print(f"⚠ Error: {e2}")

    # =========================================================================
    # CHECK TRAIN SP LOG for segment-level detail
    # =========================================================================
    print("\n" + "=" * 70)
    print("SANDBOX RUN LOG — most recent pipeline runs")
    print("=" * 70)
    try:
        cur.execute("""
            SELECT RUN_ID, STEP_NAME, STATUS, ROWS_WRITTEN, BUILT_AT
            FROM V5_SANDBOX_APP_RUNS
            ORDER BY BUILT_AT DESC
            LIMIT 15
        """)
        rows = cur.fetchall()
        print(f"{'RUN_ID':<35} {'STEP':<30} {'STATUS':<12} {'ROWS':>10} {'BUILT_AT':<20}")
        print("-" * 115)
        for run_id, step, status, rows_written, built_at in rows:
            print(f"{str(run_id):<35} {str(step):<30} {str(status):<12} {str(rows_written):>10} {str(built_at)[:19]}")
    except Exception as e:
        print(f"⚠ Error querying V5_SANDBOX_APP_RUNS: {e}")

    # =========================================================================
    # CONFIRM THE V5_SANDBOX_FORECAST_COMPAT view — what RUN_ID is selected?
    # =========================================================================
    print("\n" + "=" * 70)
    print("V5_SANDBOX_FORECAST_COMPAT — selected run & segment coverage")
    print("=" * 70)
    try:
        cur.execute("""
            SELECT
                SOURCE_RUN_ID,
                SEGMENT,
                COUNT(*) AS n,
                COUNT(DISTINCT CONTRACT_ID_UFR) AS contracts
            FROM V5_SANDBOX_FORECAST_COMPAT
            GROUP BY SOURCE_RUN_ID, SEGMENT
            ORDER BY SOURCE_RUN_ID, SEGMENT
        """)
        rows = cur.fetchall()
        print(f"{'RUN_ID':<35} {'SEGMENT':<20} {'N':>8} {'Contracts':>10}")
        print("-" * 80)
        for run_id, seg, n, contracts in rows:
            print(f"{str(run_id):<35} {str(seg):<20} {n:>8,} {contracts:>10,}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    conn.close()
    print("\nDiagnostic complete.")

if __name__ == '__main__':
    run()
