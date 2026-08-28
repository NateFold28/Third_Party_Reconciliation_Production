"""Validate churn probability calibration: do predicted rates match actual outcomes?"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from connection import fetch_dataframe

DB = "STREAMLIT_APPS.DBO"

# 1. Calibration by decile — does P(churn)=80% actually mean 80% churn rate?
print("=== CHURN CALIBRATION BY DECILE (backtest closed months) ===")
print("Binning contracts by predicted churn probability and checking actual churn rate")
df1 = fetch_dataframe(f"""
    WITH preds AS (
        SELECT
            p.CONTRACT_ID_UFR,
            DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
            p.SEGMENT,
            p.P_CHURN_CAL * 100 AS CHURN_PCT_PRED,
            p.AT_RISK_DOLLARS,
            p.ATR,
            FLOOR(p.P_CHURN_CAL * 10) * 10 AS PRED_DECILE  -- 0=0-10%, 10=10-20%, ... 90=90-100%
        FROM {DB}.ML_SANDBOX_V5_PREDICTIONS p
        WHERE p.RUN_ID = (SELECT MAX(RUN_ID) FROM {DB}.ML_SANDBOX_V5_PREDICTIONS)
          AND p.SPLIT = 'SCORE'
    ),
    actuals AS (
        SELECT
            d.CONTRACT_ID,
            DATE_TRUNC('MONTH', d.RENEWAL_DATE)::DATE AS RENEWAL_MONTH,
            d.ACTUAL_RETAINED_ARR,
            d.ATR AS ATR_D,
            d.IS_MATURED_DISPLAY_CAL
        FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL d
        WHERE d.IS_MATURED_DISPLAY_CAL = TRUE  -- closed months only
    )
    SELECT
        PRED_DECILE,
        COUNT(*) AS N,
        ROUND(AVG(p.CHURN_PCT_PRED), 1) AS AVG_PRED_CHURN_PCT,
        ROUND(
            (1 - SUM(a.ACTUAL_RETAINED_ARR) / NULLIF(SUM(p.ATR), 0)) * 100,
            1
        ) AS ACTUAL_CHURN_RATE_PCT,
        ROUND(AVG(p.CHURN_PCT_PRED) - (1 - SUM(a.ACTUAL_RETAINED_ARR) / NULLIF(SUM(p.ATR), 0)) * 100, 2) AS BIAS_PP
    FROM preds p
    JOIN actuals a
      ON a.CONTRACT_ID = p.CONTRACT_ID_UFR
     AND a.RENEWAL_MONTH = p.RENEWAL_MONTH
    GROUP BY PRED_DECILE
    ORDER BY PRED_DECILE
""")
print(df1.to_string())

# 2. Per-segment calibration (closed months)
print("\n=== PER-SEGMENT CALIBRATION ===")
df2 = fetch_dataframe(f"""
    WITH base AS (
        SELECT b.SEGMENT,
               ROUND(AVG(b.PREDICTED_RATE_PCT), 1) AS AVG_PRED_RENEWAL,
               ROUND(AVG(b.ACTUAL_RATE_PCT), 1)    AS AVG_ACT_RENEWAL,
               ROUND(AVG(b.ERROR_PP), 2)            AS AVG_BIAS_PP,
               COUNT(*) AS MONTHS,
               ROUND(MAX(ABS(b.ERROR_PP)), 1) AS MAX_ABS_BIAS
        FROM {DB}.V5_SANDBOX_APP_BACKTEST b
        WHERE b.RUN_ID = (SELECT MAX(RUN_ID) FROM {DB}.V5_SANDBOX_APP_BACKTEST)
        GROUP BY b.SEGMENT
    )
    SELECT *,
        CASE WHEN ABS(AVG_BIAS_PP) < 2 THEN 'EXCELLENT'
             WHEN ABS(AVG_BIAS_PP) < 5 THEN 'GOOD'
             WHEN ABS(AVG_BIAS_PP) < 10 THEN 'FAIR'
             ELSE 'POOR' END AS CALIBRATION_GRADE
    FROM base ORDER BY ABS(AVG_BIAS_PP) DESC
""")
print(df2.to_string())

# 3. What does "80% churn" actually mean in dollar terms?
# If a contract is scored 75-85% churn prob, what fraction of ATR actually churns?
print("\n=== WHAT 80% CHURN PROBABILITY ACTUALLY MEANS (dollar loss fraction) ===")
df3 = fetch_dataframe(f"""
    WITH preds AS (
        SELECT
            p.CONTRACT_ID_UFR,
            DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
            p.P_CHURN_CAL * 100 AS CHURN_PCT_PRED,
            p.ATR
        FROM {DB}.ML_SANDBOX_V5_PREDICTIONS p
        WHERE p.RUN_ID = (SELECT MAX(RUN_ID) FROM {DB}.ML_SANDBOX_V5_PREDICTIONS)
          AND p.SPLIT = 'SCORE'
          AND p.P_CHURN_CAL BETWEEN 0.75 AND 0.85
    ),
    actuals AS (
        SELECT d.CONTRACT_ID, DATE_TRUNC('MONTH', d.RENEWAL_DATE)::DATE AS RM,
               d.ACTUAL_RETAINED_ARR, d.ATR AS ATR_D
        FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL d
        WHERE d.IS_MATURED_DISPLAY_CAL = TRUE
    )
    SELECT
        COUNT(*) AS N_CONTRACTS,
        ROUND(AVG(p.CHURN_PCT_PRED),1) AS AVG_PRED_CHURN_PCT,
        ROUND(SUM(a.ACTUAL_RETAINED_ARR)/NULLIF(SUM(p.ATR),0)*100,1) AS ACT_RENEWAL_RATE_PCT,
        ROUND(100 - SUM(a.ACTUAL_RETAINED_ARR)/NULLIF(SUM(p.ATR),0)*100,1) AS ACT_CHURN_RATE_PCT
    FROM preds p
    JOIN actuals a ON a.CONTRACT_ID = p.CONTRACT_ID_UFR AND a.RM = p.RENEWAL_MONTH
""")
print(df3.to_string())
print("Interpretation: When model says 80% churn probability, what % of ATR actually does NOT renew?")

# 4. Heatmap data — check June specifically
print("\n=== JUNE 2026 CONTRACT STATUS (why heatmap shows low risk) ===")
df4 = fetch_dataframe(f"""
    SELECT
        IS_MATURED_DISPLAY_CAL,
        IS_MATURE,
        COUNT(*) AS N,
        ROUND(SUM(ATR)/1e6, 2) AS ATR_M,
        ROUND(AVG(CHURN_PCT), 1) AS AVG_CHURN
    FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-06-01'
    GROUP BY IS_MATURED_DISPLAY_CAL, IS_MATURE
    ORDER BY IS_MATURED_DISPLAY_CAL
""")
print(df4.to_string())
