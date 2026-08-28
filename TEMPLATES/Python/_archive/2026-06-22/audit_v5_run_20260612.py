"""
V5 Run Audit — V5_20260612_154023
Validates SHAP, calibration, segment risk, backtest bias, and EW list.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
from connection import fetch_dataframe

pd.set_option("display.max_columns", 30)
pd.set_option("display.width", 160)
pd.set_option("display.float_format", lambda x: f"{x:,.2f}")

DB = "STREAMLIT_APPS.DBO"

def section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")

def run():
    # ── 0. Latest runs ────────────────────────────────────────────────────────
    section("0. LATEST RUNS")
    df = fetch_dataframe(f"""
        SELECT RUN_ID,
               RUN_TIMESTAMP::DATE                AS DATE,
               N_FORECAST,
               N_FEATURES,
               ROUND(STAGE_A_AUC, 4)              AS AUC,
               ROUND(STAGE_A_BRIER, 4)            AS BRIER,
               ROUND(HOLDOUT_DOLLAR_BIAS_PCT, 2)  AS HOLDOUT_BIAS_PCT,
               ROUND(HOLDOUT_ACTUAL_RENEW_RATE, 2) AS HOLDOUT_ACTUAL_RATE,
               ROUND(HOLDOUT_PRED_RENEW_RATE, 2)  AS HOLDOUT_PRED_RATE,
               IS_CHAMPION, CHAMPION_GATE_PASSED
        FROM {DB}.V5_SANDBOX_APP_RUNS
        ORDER BY RUN_TIMESTAMP DESC
        LIMIT 5
    """)
    print(df.to_string(index=False))

    # ── 1. SHAP row counts ────────────────────────────────────────────────────
    section("1. SHAP ROW COUNTS")
    df = fetch_dataframe(f"""
        SELECT 'V5_SANDBOX_APP_SHAP_DRIVERS' AS TBL, COUNT(*) AS ROW_COUNT,
               COUNT(DISTINCT CONTRACT_ID) AS DISTINCT_CONTRACTS
          FROM {DB}.V5_SANDBOX_APP_SHAP_DRIVERS
        UNION ALL
        SELECT 'ML_SANDBOX_V5_CONTRACT_SHAP', COUNT(*),
               COUNT(DISTINCT CONTRACT_ID_UFR)
          FROM {DB}.ML_SANDBOX_V5_CONTRACT_SHAP
    """)
    print(df.to_string(index=False))

    # ── 2. SHAP top 15 global drivers ─────────────────────────────────────────
    section("2. SHAP TOP 15 GLOBAL FEATURE DRIVERS (avg |SHAP| across contracts)")
    df = fetch_dataframe(f"""
        SELECT FEATURE_NAME, FEATURE_LABEL,
               ROUND(AVG(ABS_SHAP), 5)  AS MEAN_ABS_SHAP,
               COUNT(DISTINCT CONTRACT_ID) AS N_CONTRACTS,
               RANK() OVER (ORDER BY AVG(ABS_SHAP) DESC) AS RNK
        FROM {DB}.V5_SANDBOX_APP_SHAP_DRIVERS
        WHERE DRIVER_RANK <= 10
        GROUP BY FEATURE_NAME, FEATURE_LABEL
        ORDER BY MEAN_ABS_SHAP DESC
        LIMIT 15
    """)
    if df.empty:
        print("  *** SHAP DRIVERS TABLE IS EMPTY — SHAP fix not yet active in this run.")
    else:
        print(df.to_string(index=False))

    # ── 3. Segment summary (forward contracts, H=0..5) ────────────────────────
    section("3. SEGMENT RISK — FORWARD CONTRACTS (H=0..5)")
    df = fetch_dataframe(f"""
        SELECT SEGMENT,
               COUNT(DISTINCT CONTRACT_ID)                        AS N,
               ROUND(SUM(ATR)/1e6, 2)                            AS ATR_M,
               ROUND(AVG(CHURN_PCT), 1)                          AS AVG_CHURN_PCT,
               ROUND(SUM(AT_RISK_DOLLARS)/1e6, 2)                AS EXP_LOSS_M,
               ROUND(SUM(AT_RISK_DOLLARS)/NULLIF(SUM(ATR),0)*100,1) AS LOSS_RATE_PCT,
               SUM(EARLY_WARNING_FLAG)                           AS EARLY_WARNINGS
        FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE IS_MATURED_DISPLAY_CAL = FALSE
          AND RENEWAL_MONTH <= DATEADD('MONTH', 5, DATE_TRUNC('MONTH', CURRENT_DATE()))
        GROUP BY SEGMENT
        ORDER BY LOSS_RATE_PCT DESC
    """)
    print(df.to_string(index=False))

    # ── 4. Monthly breakdown (forward, H=0..5) ────────────────────────────────
    section("4. MONTHLY ATR & RISK (H=0..5)")
    df = fetch_dataframe(f"""
        SELECT RENEWAL_MONTH,
               COUNT(DISTINCT CONTRACT_ID) AS N,
               ROUND(SUM(ATR)/1e6, 2)      AS ATR_M,
               ROUND(AVG(CHURN_PCT), 1)    AS AVG_CHURN_PCT,
               ROUND(SUM(AT_RISK_DOLLARS)/1e6, 2) AS EXP_LOSS_M,
               SUM(EARLY_WARNING_FLAG)     AS EW_COUNT
        FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE IS_MATURED_DISPLAY_CAL = FALSE
          AND RENEWAL_MONTH <= DATEADD('MONTH', 5, DATE_TRUNC('MONTH', CURRENT_DATE()))
        GROUP BY RENEWAL_MONTH
        ORDER BY RENEWAL_MONTH
    """)
    print(df.to_string(index=False))

    # ── 5. Top 20 Early Warnings by expected loss ─────────────────────────────
    section("5. TOP 20 EARLY WARNINGS (by Expected $ Loss)")
    df = fetch_dataframe(f"""
        SELECT PARTNER, SEGMENT, PRODUCT_PORTFOLIO,
               RENEWAL_MONTH, RENEWAL_MANAGER,
               ROUND(ATR)                  AS ATR,
               ROUND(CHURN_PCT, 1)         AS CHURN_PCT,
               ROUND(AT_RISK_DOLLARS)      AS EXP_LOSS,
               ROUND(CONTRACT_RISK_PCTL_IN_SEG) AS RISK_PCTL
        FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE EARLY_WARNING_FLAG = 1
          AND IS_MATURED_DISPLAY_CAL = FALSE
        ORDER BY AT_RISK_DOLLARS DESC
        LIMIT 20
    """)
    print(df.to_string(index=False))

    # ── 6. Backtest bias by segment (latest run, CHURN_ADJUSTED) ─────────────
    section("6. BACKTEST BIAS BY SEGMENT (latest run, CHURN_ADJUSTED)")
    df = fetch_dataframe(f"""
        SELECT b.SEGMENT, b.RENEWAL_MONTH,
               b.N_CONTRACTS,
               ROUND(b.PREDICTED_RATE_PCT, 1) AS PRED_RATE,
               ROUND(b.ACTUAL_RATE_PCT, 1)    AS ACT_RATE,
               ROUND(b.ERROR_PP, 2)           AS BIAS_PP
        FROM {DB}.V5_SANDBOX_APP_BACKTEST b
        WHERE b.RUN_ID = (SELECT MAX(RUN_ID) FROM {DB}.V5_SANDBOX_APP_BACKTEST)
          AND b.METHOD = 'CHURN_ADJUSTED'
        ORDER BY b.RENEWAL_MONTH DESC, ABS(b.ERROR_PP) DESC
        LIMIT 40
    """)
    if df.empty:
        print("  *** No CHURN_ADJUSTED backtest rows — checking available methods...")
        df2 = fetch_dataframe(f"""
            SELECT METHOD, COUNT(*) AS N
            FROM {DB}.V5_SANDBOX_APP_BACKTEST
            WHERE RUN_ID = (SELECT MAX(RUN_ID) FROM {DB}.V5_SANDBOX_APP_BACKTEST)
            GROUP BY METHOD
        """)
        print(df2.to_string(index=False))
    else:
        print(df.to_string(index=False))
        bias_abs = df["BIAS_PP"].abs()
        print(f"\n  Summary — Mean |bias|: {bias_abs.mean():.2f}pp  |  Max |bias|: {bias_abs.max():.2f}pp")
        bad = df[bias_abs > 5.0]
        if not bad.empty:
            print(f"\n  *** {len(bad)} segment-month(s) with |bias| > 5pp:")
            print(bad.to_string(index=False))
        else:
            print("  OK: All segment-months within +/-5pp bias threshold.")

    # ── 7. Churn distribution by bucket ──────────────────────────────────────
    section("7. CHURN DISTRIBUTION BY BUCKET (forward contracts)")
    df = fetch_dataframe(f"""
        SELECT
            CASE
                WHEN CHURN_PCT < 10  THEN '00-10%'
                WHEN CHURN_PCT < 25  THEN '10-25%'
                WHEN CHURN_PCT < 50  THEN '25-50%'
                WHEN CHURN_PCT < 75  THEN '50-75%'
                ELSE '75-100%'
            END AS BUCKET,
            SEGMENT,
            COUNT(*) AS N,
            ROUND(SUM(ATR)/1e6, 2) AS ATR_M
        FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE IS_MATURED_DISPLAY_CAL = FALSE
          AND RENEWAL_MONTH <= DATEADD('MONTH', 5, DATE_TRUNC('MONTH', CURRENT_DATE()))
        GROUP BY 1, 2
        ORDER BY SEGMENT, BUCKET
    """)
    print(df.to_string(index=False))

    # ── 8. ML vs anchor split (via FINANCE_ANCHOR_SOURCE proxy) ──────────────
    section("8. ML vs ANCHOR SCORING SPLIT (by ATR_SOURCE)")
    df = fetch_dataframe(f"""
        SELECT ATR_SOURCE,
               COUNT(*)                AS N,
               ROUND(SUM(ATR)/1e6, 2) AS ATR_M,
               ROUND(AVG(CHURN_PCT),1) AS AVG_CHURN_PCT
        FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE IS_MATURED_DISPLAY_CAL = FALSE
          AND RENEWAL_MONTH <= DATEADD('MONTH', 5, DATE_TRUNC('MONTH', CURRENT_DATE()))
        GROUP BY ATR_SOURCE
        ORDER BY N DESC
    """)
    print(df.to_string(index=False))

    # Also check FINANCE_ANCHOR_SOURCE to see ML vs fallback split
    df2 = fetch_dataframe(f"""
        SELECT FINANCE_ANCHOR_SOURCE,
               COUNT(*) AS N,
               ROUND(AVG(CHURN_PCT),1) AS AVG_CHURN_PCT
        FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE IS_MATURED_DISPLAY_CAL = FALSE
          AND RENEWAL_MONTH <= DATEADD('MONTH', 5, DATE_TRUNC('MONTH', CURRENT_DATE()))
        GROUP BY FINANCE_ANCHOR_SOURCE
        ORDER BY N DESC
        LIMIT 10
    """)
    print("\nBy FINANCE_ANCHOR_SOURCE:")
    print(df2.to_string(index=False))

    # ── 9. Portfolio-level risk ───────────────────────────────────────────────
    section("9. PORTFOLIO RISK SUMMARY (forward)")
    df = fetch_dataframe(f"""
        SELECT PRODUCT_PORTFOLIO,
               COUNT(DISTINCT CONTRACT_ID)                             AS N,
               ROUND(SUM(ATR)/1e6, 2)                                 AS ATR_M,
               ROUND(AVG(CHURN_PCT), 1)                               AS AVG_CHURN_PCT,
               ROUND(SUM(AT_RISK_DOLLARS)/NULLIF(SUM(ATR),0)*100, 1) AS LOSS_RATE_PCT,
               SUM(EARLY_WARNING_FLAG)                                AS EW
        FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE IS_MATURED_DISPLAY_CAL = FALSE
          AND RENEWAL_MONTH <= DATEADD('MONTH', 5, DATE_TRUNC('MONTH', CURRENT_DATE()))
        GROUP BY PRODUCT_PORTFOLIO
        ORDER BY LOSS_RATE_PCT DESC
    """)
    print(df.to_string(index=False))

    print("\n\nAudit complete.\n")

if __name__ == "__main__":
    run()
