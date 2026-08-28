"""
Audit: risk ranking, SHAP magnitudes, segment grain, naming.
Run: .venv\Scripts\python.exe TEMPLATES\Python\audit_risk_shap.py
"""
import sys
sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection, fetch_dataframe
import pandas as pd
import numpy as np

conn = get_snowflake_connection()

# ======================================================================
# 1. SHAP: check magnitudes, BASE_VALUE, CONTRACT_RISK_PCTL distribution
# ======================================================================
print("=" * 70)
print("1. SHAP TABLE — magnitude check, base value, coverage")
print("=" * 70)
q = """
SELECT
    COUNT(DISTINCT CONTRACT_ID || '|' || PRODUCT_GROUP) AS N_CONTRACT_PG,
    COUNT(*)                  AS N_ROWS,
    AVG(ABS(SHAP_VALUE))      AS AVG_ABS_SHAP,
    MAX(ABS(SHAP_VALUE))      AS MAX_ABS_SHAP,
    MIN(ABS(SHAP_VALUE))      AS MIN_ABS_SHAP,
    AVG(BASE_VALUE)           AS AVG_BASE_VALUE,
    COUNT_IF(BASE_VALUE IS NOT NULL) AS N_BASE_VALUE,
    COUNT(DISTINCT FEATURE_NAME) AS N_FEATURES
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_SHAP_DRIVERS
"""
df = fetch_dataframe(conn, q)
print(df.to_string(index=False))

# ======================================================================
# 2. SHAP: top 10 features by mean abs SHAP (what the portfolio chart shows)
# ======================================================================
print("\n" + "=" * 70)
print("2. SHAP — TOP 10 FEATURES (portfolio chart, what executives see)")
print("=" * 70)
q2 = """
SELECT
    FEATURE_NAME,
    AVG(ABS(SHAP_VALUE))   AS MEAN_ABS_SHAP,
    AVG(SHAP_VALUE)        AS MEAN_SIGNED,
    MAX(ABS(SHAP_VALUE))   AS MAX_ABS_SHAP,
    COUNT(DISTINCT CONTRACT_ID || '|' || PRODUCT_GROUP) AS N_CONTRACTS,
    DIRECTION
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_SHAP_DRIVERS
GROUP BY FEATURE_NAME, DIRECTION
ORDER BY MEAN_ABS_SHAP DESC
LIMIT 10
"""
df2 = fetch_dataframe(conn, q2)
print(df2.to_string(index=False))

# ======================================================================
# 3. RISK RANKING — distribution of CHURN_PCT and CONTRACT_RISK_PCTL_IN_SEG
# ======================================================================
print("\n" + "=" * 70)
print("3. RISK RANKING — CHURN_PCT and pctl distribution (forward months only)")
print("=" * 70)
q3 = """
SELECT
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY CHURN_PCT) AS P25_CHURN,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY CHURN_PCT) AS P50_CHURN,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY CHURN_PCT) AS P75_CHURN,
    PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY CHURN_PCT) AS P90_CHURN,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY CHURN_PCT) AS P95_CHURN,
    AVG(CHURN_PCT)                 AS AVG_CHURN,
    MAX(CHURN_PCT)                 AS MAX_CHURN,
    COUNT(DISTINCT CONTRACT_ID)    AS N_CONTRACTS,
    COUNT_IF(CONTRACT_RISK_PCTL_IN_SEG IS NOT NULL) AS N_WITH_PCTL,
    AVG(CONTRACT_RISK_PCTL_IN_SEG) AS AVG_PCTL,
    SUM(ATR)                       AS TOTAL_ATR,
    SUM(CASE WHEN CHURN_PCT >= 50 THEN ATR ELSE 0 END) / NULLIF(SUM(ATR),0) * 100 AS PCT_ATR_HIGH_RISK
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE COALESCE(ATR,0) > 0
  AND IS_MATURED_MONTH = FALSE
"""
df3 = fetch_dataframe(conn, q3)
print(df3.to_string(index=False))

# ======================================================================
# 4. TOP 15 contracts by AT_RISK (CHURN_PCT * ATR) — forward only
# ======================================================================
print("\n" + "=" * 70)
print("4. TOP 15 HIGHEST IMPACT CONTRACTS (CHURN_PCT * ATR, forward months)")
print("=" * 70)
q4 = """
SELECT
    CONTRACT_ID,
    MAX(PARTNER_NAME)           AS PARTNER,
    MAX(SEGMENT)                AS SEGMENT,
    MAX(PRODUCT_PORTFOLIO)      AS PORTFOLIO,
    MIN(RENEWAL_MONTH)          AS EARLIEST_RENEWAL,
    SUM(ATR)                    AS TOTAL_ATR,
    AVG(CHURN_PCT)              AS AVG_CHURN_PCT,
    SUM(CHURN_PCT / 100.0 * ATR) AS AT_RISK_DOLLARS,
    MAX(CONTRACT_RISK_PCTL_IN_SEG) AS RISK_PCTL
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE COALESCE(ATR,0) > 0
  AND IS_MATURED_MONTH = FALSE
GROUP BY CONTRACT_ID
ORDER BY AT_RISK_DOLLARS DESC
LIMIT 15
"""
df4 = fetch_dataframe(conn, q4)
print(df4.to_string(index=False))

# ======================================================================
# 5. Pareto: what % of AT_RISK comes from top N contracts
# ======================================================================
print("\n" + "=" * 70)
print("5. PARETO — what % of total AT_RISK comes from top N contracts")
print("=" * 70)
q5 = """
WITH base AS (
    SELECT
        CONTRACT_ID,
        SUM(CHURN_PCT / 100.0 * ATR) AS AT_RISK_DOLLARS
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE COALESCE(ATR,0) > 0
      AND IS_MATURED_MONTH = FALSE
    GROUP BY CONTRACT_ID
),
ranked AS (
    SELECT
        CONTRACT_ID,
        AT_RISK_DOLLARS,
        ROW_NUMBER() OVER (ORDER BY AT_RISK_DOLLARS DESC) AS RN,
        SUM(AT_RISK_DOLLARS) OVER () AS TOTAL_AT_RISK
    FROM base
)
SELECT
    RN,
    AT_RISK_DOLLARS,
    ROUND(SUM(AT_RISK_DOLLARS) OVER (ORDER BY RN) / TOTAL_AT_RISK * 100, 1) AS CUMULATIVE_PCT
FROM ranked
WHERE RN IN (5, 10, 15, 20, 25, 50, 100)
QUALIFY ROW_NUMBER() OVER (PARTITION BY RN ORDER BY RN) = 1
ORDER BY RN
"""
df5 = fetch_dataframe(conn, q5)
print(df5.to_string(index=False))

# ======================================================================
# 6. SEGMENT grain check — is there a contract-level segment view?
# ======================================================================
print("\n" + "=" * 70)
print("6. SEGMENT table — CONTRACT_LVL_MONTHLY segment coverage")
print("=" * 70)
q6 = """
SELECT
    SEGMENT,
    COUNT(DISTINCT CONTRACT_ID) AS N_CONTRACTS,
    SUM(CONTRACT_ATR)           AS TOTAL_CONTRACT_ATR,
    AVG(CONTRACT_FORECAST_RATE_PCT) AS AVG_CONTRACT_FORECAST_PCT,
    AVG(CONTRACT_RATE_PCT)      AS AVG_CONTRACT_ACTUAL_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
WHERE CONTRACT_ATR > 0
GROUP BY SEGMENT
ORDER BY TOTAL_CONTRACT_ATR DESC
"""
df6 = fetch_dataframe(conn, q6)
print(df6.to_string(index=False))

# ======================================================================
# 7. SHAP: sample a single high-risk contract to see actual values + conversion
# ======================================================================
print("\n" + "=" * 70)
print("7. SHAP SAMPLE — single contract, raw SHAP + pp-equivalent")
print("=" * 70)
q7 = """
WITH top_contract AS (
    SELECT CONTRACT_ID, PRODUCT_GROUP
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_SHAP_DRIVERS
    WHERE CHURN_PCT >= 50
    ORDER BY CHURN_PCT DESC, ABS_SHAP DESC
    LIMIT 1
)
SELECT
    s.FEATURE_NAME,
    s.SHAP_VALUE,
    s.ABS_SHAP,
    s.CHURN_PCT,
    s.BASE_VALUE,
    s.DIRECTION,
    s.MAGNITUDE,
    -- pp approximation: shap * p * (1-p) * 100
    -- where p = sigmoid(pred_log_odds), but here p ≈ CHURN_PCT/100
    ROUND(s.SHAP_VALUE * (s.CHURN_PCT/100) * (1 - s.CHURN_PCT/100) * 100, 2) AS APPROX_PP_IMPACT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_SHAP_DRIVERS s
JOIN top_contract t ON s.CONTRACT_ID = t.CONTRACT_ID AND s.PRODUCT_GROUP = t.PRODUCT_GROUP
ORDER BY s.ABS_SHAP DESC
LIMIT 10
"""
df7 = fetch_dataframe(conn, q7)
print(df7.to_string(index=False))

print("\n\nAUDIT COMPLETE")
conn.close()
