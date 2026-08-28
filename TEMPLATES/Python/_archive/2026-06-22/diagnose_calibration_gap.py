"""
V5 Calibration Root-Cause Diagnostic
-------------------------------------
Pinpoints EXACTLY why non-Core segments have flat CHURN_PCT and NULL
CONTRACT_RISK_PCTL_IN_SEG by checking the predictions table and feature
store for segment coverage gaps.
"""

import sys
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

def run():
    conn = get_snowflake_connection()
    cur = conn.cursor()
    cur.execute("USE ROLE STREAMLIT_USER")
    cur.execute("USE WAREHOUSE REPORTING_WH")
    cur.execute("USE DATABASE STREAMLIT_APPS")
    cur.execute("USE SCHEMA DBO")
    print("✓ Connected\n")

    # =========================================================================
    # DIAG 1: What F_SEGMENT labels are in the feature store (SCORE split)?
    # =========================================================================
    print("=" * 70)
    print("DIAG 1: FEATURE STORE — F_SEGMENT x SPLIT coverage")
    print("=" * 70)
    try:
        cur.execute("""
            SELECT F_SEGMENT, SPLIT, COUNT(*) AS n, COUNT(DISTINCT CONTRACT_ID_UFR) AS contracts
            FROM ML_SANDBOX_V5_FEATURE_STORE
            GROUP BY F_SEGMENT, SPLIT
            ORDER BY F_SEGMENT, SPLIT
        """)
        rows = cur.fetchall()
        print(f"{'F_SEGMENT':<25} {'SPLIT':<12} {'N':>8} {'Contracts':>10}")
        print("-" * 60)
        for seg, split, n, contracts in rows:
            print(f"{str(seg):<25} {str(split):<12} {n:>8,} {contracts:>10,}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # DIAG 2: Predictions table — segment x split coverage for LATEST RUN
    # =========================================================================
    print("\n" + "=" * 70)
    print("DIAG 2: ML_SANDBOX_V5_PREDICTIONS — SEGMENT x SPLIT (latest run)")
    print("=" * 70)
    try:
        cur.execute("""
            WITH latest AS (
                SELECT RUN_ID FROM ML_SANDBOX_V5_PREDICTIONS
                GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
            )
            SELECT
                p.SEGMENT,
                p.SPLIT,
                COUNT(*) AS n,
                COUNT(DISTINCT p.CONTRACT_ID_UFR) AS contracts,
                ROUND(AVG(p.P_CHURN_CAL), 4) AS avg_p_churn_cal,
                COUNT(DISTINCT ROUND(p.P_CHURN_CAL, 4)) AS distinct_p_churn,
                AVG(p.IS_FALLBACK) AS pct_fallback
            FROM ML_SANDBOX_V5_PREDICTIONS p
            JOIN latest l ON l.RUN_ID = p.RUN_ID
            GROUP BY p.SEGMENT, p.SPLIT
            ORDER BY p.SEGMENT, p.SPLIT
        """)
        rows = cur.fetchall()
        print(f"{'SEGMENT':<20} {'SPLIT':<12} {'N':>8} {'Distinct P_CHURN':>16} {'IS_FALLBACK':>12}")
        print("-" * 75)
        for seg, split, n, contracts, avg_p, distinct_p, pct_fb in rows:
            flag = " ⚠ ALL FALLBACK — flat prediction" if pct_fb == 1.0 else (
                   " ⚠ MIXED fallback" if pct_fb and pct_fb > 0.05 else " ✓")
            print(f"{str(seg):<20} {str(split):<12} {n:>8,} {distinct_p:>16,} {pct_fb:>12.2%}{flag}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # DIAG 3: App detail — how many CARR contracts have NO v5 match?
    # =========================================================================
    print("\n" + "=" * 70)
    print("DIAG 3: APP CONTRACT DETAIL — v5 join hit vs miss by segment")
    print("=" * 70)
    print("If FINANCE_ANCHOR_SOURCE = 'SEGMENT' → no v5 prediction matched (fallback)\n")
    try:
        cur.execute("""
            SELECT
                SEGMENT,
                FINANCE_ANCHOR_SOURCE,
                COUNT(*) AS n,
                COUNT(DISTINCT ACCOUNT_ID) AS accounts
            FROM V5_SANDBOX_APP_CONTRACT_DETAIL
            WHERE ATR > 0
            GROUP BY SEGMENT, FINANCE_ANCHOR_SOURCE
            ORDER BY SEGMENT, FINANCE_ANCHOR_SOURCE
        """)
        rows = cur.fetchall()
        print(f"{'SEGMENT':<20} {'ANCHOR_SOURCE':<15} {'N':>8} {'Accounts':>10}")
        print("-" * 60)
        for seg, source, n, accts in rows:
            flag = " ⚠ UNSCORED — using segment anchor only" if source == 'SEGMENT' else " ✓ Model scored"
            print(f"{str(seg):<20} {str(source):<15} {n:>8,} {accts:>10,}{flag}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # DIAG 4: Feature store SCORE rows — which CONTRACT_IDs are covered?
    # =========================================================================
    print("\n" + "=" * 70)
    print("DIAG 4: FEATURE STORE SCORE COVERAGE vs CARR SPINE")
    print("=" * 70)
    try:
        cur.execute("""
            WITH fs_score AS (
                SELECT DISTINCT CONTRACT_ID_UFR, F_SEGMENT
                FROM ML_SANDBOX_V5_FEATURE_STORE
                WHERE SPLIT = 'SCORE'
            ),
            carr_contracts AS (
                SELECT DISTINCT
                    TRIM(c.CONTRACT_ID_UFR) AS CONTRACT_ID_UFR,
                    sm.SEGMENTATION_TIER AS SEGMENT
                FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL c
                LEFT JOIN ANALYTICS.DBO.CARR__SEGMENTATION_TIER sm ON sm.ACCOUNT_ID = c.ACCOUNT_ID
                WHERE c.INCLUDE_FLAG_C = 1
                  AND DATE_TRUNC('MONTH', c.MASTER_DATE) = DATE_TRUNC('MONTH', CURRENT_DATE())
            )
            SELECT
                carr.SEGMENT AS CARR_SEGMENT,
                fs.F_SEGMENT AS FS_SEGMENT,
                COUNT(DISTINCT carr.CONTRACT_ID_UFR) AS carr_contracts,
                SUM(CASE WHEN fs.CONTRACT_ID_UFR IS NOT NULL THEN 1 ELSE 0 END) AS scored_contracts,
                SUM(CASE WHEN fs.CONTRACT_ID_UFR IS NULL     THEN 1 ELSE 0 END) AS unscored_contracts
            FROM carr_contracts carr
            LEFT JOIN fs_score fs
                ON fs.CONTRACT_ID_UFR = carr.CONTRACT_ID_UFR
            GROUP BY carr.SEGMENT, fs.F_SEGMENT
            ORDER BY carr.SEGMENT, fs.F_SEGMENT
        """)
        rows = cur.fetchall()
        print(f"{'CARR SEGMENT':<20} {'FS SEGMENT':<20} {'CARR Ctrs':>10} {'Scored':>8} {'Unscored':>10}")
        print("-" * 75)
        for carr_seg, fs_seg, carr_ctrs, scored, unscored in rows:
            pct_covered = scored/carr_ctrs*100 if carr_ctrs else 0
            flag = " ✓" if unscored == 0 else f" ⚠ {unscored:,} MISSING FROM FEATURE STORE ({100-pct_covered:.0f}% gap)"
            print(f"{str(carr_seg):<20} {str(fs_seg):<20} {carr_ctrs:>10,} {scored:>8,} {unscored:>10,}{flag}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # DIAG 5: Training data in feature store — TRAIN split per segment
    # =========================================================================
    print("\n" + "=" * 70)
    print("DIAG 5: FEATURE STORE TRAINING DATA — TRAIN rows per segment")
    print("=" * 70)
    print("IS_FALLBACK triggers when TRAIN rows < 200 OR target is monoclass\n")
    try:
        cur.execute("""
            SELECT
                F_SEGMENT,
                COUNT(*) AS total_rows,
                SUM(CASE WHEN SPLIT = 'TRAIN' AND HORIZON <= 3 THEN 1 ELSE 0 END) AS train_h03_rows,
                SUM(CASE WHEN SPLIT = 'CAL'   THEN 1 ELSE 0 END) AS cal_rows,
                SUM(CASE WHEN SPLIT = 'SCORE' THEN 1 ELSE 0 END) AS score_rows,
                COUNT(DISTINCT CASE WHEN TARGET__IS_CHURN IS NOT NULL THEN ROUND(TARGET__RENEWAL_RATE,0) END) AS target_classes,
                COUNT(DISTINCT CASE WHEN SPLIT='TRAIN' THEN CONTRACT_ID_UFR END) AS train_contracts
            FROM ML_SANDBOX_V5_FEATURE_STORE
            GROUP BY F_SEGMENT
            ORDER BY F_SEGMENT
        """)
        rows = cur.fetchall()
        print(f"{'F_SEGMENT':<25} {'Total':>8} {'Train H≤3':>10} {'CAL':>8} {'SCORE':>8} {'Trgt Cls':>9} {'Train Ctrs':>11}")
        print("-" * 85)
        for seg, total, train_h03, cal, score, target_cls, train_ctrs in rows:
            flag = ""
            if train_h03 and train_h03 < 200:
                flag += " ⚠ TRAIN_TOO_SMALL"
            if target_cls is not None and target_cls < 2:
                flag += " ⚠ MONOCLASS_TARGET"
            print(f"{str(seg):<25} {total:>8,} {train_h03 or 0:>10,} {cal or 0:>8,} {score or 0:>8,} {target_cls or 0:>9} {train_ctrs or 0:>11,}{flag}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # ROOT CAUSE SUMMARY
    # =========================================================================
    print("\n" + "=" * 70)
    print("ROOT CAUSE ANALYSIS COMPLETE")
    print("=" * 70)

    conn.close()

if __name__ == '__main__':
    run()
