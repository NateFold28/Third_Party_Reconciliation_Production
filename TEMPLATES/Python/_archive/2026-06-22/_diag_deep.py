"""Deep dive: anchor fallback rate, SHAP RUN_ID mismatch, and pareto data."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from connection import fetch_dataframe

DB = "STREAMLIT_APPS.DBO"

# 1. Why are 68% anchor? What FINANCE_ANCHOR_SOURCE values exist and what drives fallback?
print("=== ANCHOR FALLBACK ROOT CAUSE ===")
df = fetch_dataframe(f"""
    SELECT
        CASE WHEN ml.CONTRACT_ID_UFR IS NOT NULL THEN 'HAS_ML_SCORE' ELSE 'NO_ML_SCORE' END AS ML_STATUS,
        d.FINANCE_ANCHOR_SOURCE,
        COUNT(*) AS N,
        ROUND(AVG(d.CHURN_PCT),1) AS AVG_CHURN,
        ROUND(SUM(d.ATR)/1e6,2) AS ATR_M
    FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL d
    LEFT JOIN (
        SELECT DISTINCT CONTRACT_ID_UFR
        FROM {DB}.ML_SANDBOX_V5_CONTRACT_SHAP
        WHERE RUN_ID = (SELECT MAX(RUN_ID) FROM {DB}.ML_SANDBOX_V5_CONTRACT_SHAP)
    ) ml ON ml.CONTRACT_ID_UFR = d.CONTRACT_ID
    WHERE d.IS_MATURED_DISPLAY_CAL = FALSE
    GROUP BY 1,2
    ORDER BY N DESC
""")
print(df.to_string())

# 2. Check if ML_SANDBOX_V5_PREDICTIONS has scores for the anchor contracts
print("\n=== DO ANCHOR CONTRACTS HAVE PREDICTION SCORES? ===")
df2 = fetch_dataframe(f"""
    SELECT
        CASE WHEN p.CONTRACT_ID_UFR IS NOT NULL THEN 'HAS_PREDICTION' ELSE 'NO_PREDICTION' END AS PRED_STATUS,
        d.FINANCE_ANCHOR_SOURCE,
        COUNT(*) AS N,
        ROUND(AVG(d.CHURN_PCT),1) AS AVG_CHURN_D,
           ROUND(AVG(p.P_CHURN_CAL*100),1) AS AVG_CHURN_P
    FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL d
    LEFT JOIN {DB}.ML_SANDBOX_V5_PREDICTIONS p
      ON p.CONTRACT_ID_UFR = d.CONTRACT_ID
     AND DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE = d.RENEWAL_MONTH
     AND p.RUN_ID = (SELECT MAX(RUN_ID) FROM {DB}.ML_SANDBOX_V5_PREDICTIONS)
     AND p.SPLIT = 'SCORE'
    WHERE d.IS_MATURED_DISPLAY_CAL = FALSE
    GROUP BY 1,2
    ORDER BY N DESC
""")
print(df2.to_string())

# 3. Pareto: cumulative risk concentration
print("\n=== PARETO: RISK CONCENTRATION (forward contracts) ===")
df3 = fetch_dataframe(f"""
    WITH ranked AS (
        SELECT
            CONTRACT_ID, PARTNER, SEGMENT, PRODUCT_PORTFOLIO,
            RENEWAL_MONTH, ATR,
            AT_RISK_DOLLARS,
            ROW_NUMBER() OVER (ORDER BY AT_RISK_DOLLARS DESC) AS RNK,
            COUNT(*) OVER () AS TOTAL_N,
            SUM(AT_RISK_DOLLARS) OVER () AS TOTAL_LOSS
        FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE IS_MATURED_DISPLAY_CAL = FALSE
          AND RENEWAL_MONTH <= DATEADD('MONTH', 5, DATE_TRUNC('MONTH', CURRENT_DATE()))
          AND AT_RISK_DOLLARS > 0
    )
    SELECT
        RNK,
        TOTAL_N,
        TOTAL_LOSS,
        ROUND(RNK / TOTAL_N * 100, 1) AS PCT_CONTRACTS,
        ROUND(SUM(AT_RISK_DOLLARS) OVER (ORDER BY RNK) / TOTAL_LOSS * 100, 1) AS CUMULATIVE_LOSS_PCT
    FROM ranked
    WHERE RNK IN (1,5,10,25,50,100,200,500) OR RNK = TOTAL_N
    ORDER BY RNK
""")
print(df3.to_string())

# 4. Top 10 contracts by expected loss — the real pareto drivers
print("\n=== TOP 10 CONTRACTS BY EXPECTED LOSS ===")
df4 = fetch_dataframe(f"""
    SELECT PARTNER, SEGMENT, PRODUCT_PORTFOLIO, RENEWAL_MONTH,
           ROUND(ATR) AS ATR,
           ROUND(CHURN_PCT,1) AS CHURN_PCT,
           ROUND(AT_RISK_DOLLARS) AS EXP_LOSS,
           EARLY_WARNING_FLAG AS EW,
           FINANCE_ANCHOR_SOURCE AS SCORE_SOURCE
    FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_DISPLAY_CAL = FALSE
      AND RENEWAL_MONTH <= DATEADD('MONTH', 5, DATE_TRUNC('MONTH', CURRENT_DATE()))
    ORDER BY AT_RISK_DOLLARS DESC
    LIMIT 10
""")
print(df4.to_string())

# 5. Backtest sample rows to see what columns + values look like
print("\n=== BACKTEST SAMPLE ROWS ===")
df5 = fetch_dataframe(f"""
    SELECT * FROM {DB}.V5_SANDBOX_APP_BACKTEST
    WHERE RUN_ID = (SELECT MAX(RUN_ID) FROM {DB}.V5_SANDBOX_APP_BACKTEST)
    ORDER BY RENEWAL_MONTH DESC
    LIMIT 10
""")
print(df5.to_string())

# 6. What's the churn range per segment for a proper calibration check?
print("\n=== CALIBRATION CHECK: predicted vs actual (backtest) ===")
df6 = fetch_dataframe(f"""
    SELECT SEGMENT, METHOD,
           ROUND(AVG(PREDICTED_RATE_PCT),1) AS AVG_PRED,
           ROUND(AVG(ACTUAL_RATE_PCT),1) AS AVG_ACT,
           ROUND(AVG(ERROR_PP),2) AS AVG_BIAS,
           COUNT(*) AS MONTHS
    FROM {DB}.V5_SANDBOX_APP_BACKTEST
    WHERE RUN_ID = (SELECT MAX(RUN_ID) FROM {DB}.V5_SANDBOX_APP_BACKTEST)
    GROUP BY SEGMENT, METHOD
    ORDER BY SEGMENT, METHOD
""")
print(df6.to_string())
