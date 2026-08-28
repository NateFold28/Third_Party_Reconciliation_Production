"""
deep_calibration_audit.py
=========================
Exhaustive calibration and business-question validation for V5.
Covers every angle that could produce a board surprise.

Sections:
  A — Backtest actuals debug (why empty?)
  B — Calibration: does HIGH churn tier actually churn? (across ALL segments × portfolios)
  C — Decile calibration: pred rate vs actual rate across 10 buckets
  D — Contract vs Portfolio architecture gap (what training source does model use?)
  E — Segment × Portfolio forward forecast (find any blind spots)
  F — Business question battery (6 questions, one answer each)
  G — P10/P90 confidence interval calibration (are they honest?)
  H — Contract grain accuracy (CONTRACT_LVL_MONTHLY vs actuals)
  I — Snapshot gap: which historical months NOT yet snapshotted
"""
import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe
import pandas as pd
import numpy as np
pd.set_option('display.float_format', '{:,.2f}'.format)
pd.set_option('display.width', 160)
pd.set_option('display.max_columns', 30)

conn = get_snowflake_connection()
SEP = "=" * 70
def hdr(s): print(f"\n{SEP}\n{s}\n{SEP}")

# ─────────────────────────────────────────────────────────────────────────────
hdr("A — Backtest table: why are actuals missing?")
# ─────────────────────────────────────────────────────────────────────────────
qA = """
SELECT
    b.RENEWAL_MONTH,
    b.SEGMENT,
    ROUND(b.PREDICTED_RATE_PCT,2) AS PRED_PCT,
    ROUND(b.ACTUAL_RATE_PCT,2)    AS ACTUAL_PCT,
    b.N_CONTRACTS,
    ROUND(b.ATR/1e6,2)            AS ATR_M,
    b.METHOD, b.RUN_ID
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_BACKTEST b
ORDER BY b.RENEWAL_MONTH, b.SEGMENT
LIMIT 30
"""
dfA = fetch_dataframe(qA, conn=conn)
print(dfA.to_string(index=False))
n_with_actual = (dfA['ACTUAL_PCT'].notna() & (dfA['ACTUAL_PCT'] > 0)).sum()
n_without = (dfA['ACTUAL_PCT'].isna() | (dfA['ACTUAL_PCT'] == 0)).sum()
print(f"\n  Rows with actuals: {n_with_actual}  | Rows WITHOUT actuals: {n_without}")
if n_without > 0:
    print("  DIAGNOSIS: Run CALL STREAMLIT_APPS.DBO.SP_V5_BUILD_APP_TABLES_V5_SHADOW(); to apply DATE_TRUNC fix")

# ─────────────────────────────────────────────────────────────────────────────
hdr("B — Churn tier calibration: does HIGH = high actual churn?  (critical board question)")
# ─────────────────────────────────────────────────────────────────────────────
# Across ALL segments × portfolios — we need NO blind spots
qB = """
WITH preds AS (
    SELECT p.CONTRACT_ID_UFR,
           DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RM,
           p.SEGMENT,
           p.ATR,
           p.PRED_RENEW_RATE_FINAL,
           p.CONTRACT_RISK_TIER,
           p.P_CHURN_CAL
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS p
    WHERE p.SPLIT IN ('CAL', 'VALIDATION')
      AND p.HORIZON = 0
      AND p.ATR > 0
      AND p.RENEWAL_MONTH >= '2025-01-01'
),
actuals AS (
    SELECT CONTRACT_ID AS CID,
           RENEWAL_MONTH AS RM,
           ACTUAL_RETAINED_ARR,
           ATR
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH = TRUE AND ATR > 0
)
SELECT
    p.SEGMENT,
    p.CONTRACT_RISK_TIER,
    COUNT(*)                                                              AS N,
    ROUND(AVG(p.P_CHURN_CAL)*100, 2)                                     AS AVG_PRED_CHURN_PCT,
    ROUND(SUM(p.PRED_RENEW_RATE_FINAL*p.ATR)/NULLIF(SUM(p.ATR),0)*100,2) AS PRED_RENEW_RATE,
    ROUND((1-SUM(COALESCE(a.ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(a.ATR),0))*100,2) AS ACTUAL_CHURN_PCT,
    ROUND(SUM(COALESCE(a.ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(a.ATR),0)*100,2)     AS ACTUAL_RENEW_RATE,
    ROUND(SUM(p.ATR)/1e6, 2)                                             AS ATR_M
FROM preds p
LEFT JOIN actuals a ON a.CID = p.CONTRACT_ID_UFR AND a.RM = p.RM
GROUP BY p.SEGMENT, p.CONTRACT_RISK_TIER
ORDER BY p.SEGMENT, p.CONTRACT_RISK_TIER
"""
dfB = fetch_dataframe(qB, conn=conn)
print(dfB.to_string(index=False))

# Check: within each segment, does HIGH tier have higher actual churn than LOW?
if not dfB.empty and 'ACTUAL_CHURN_PCT' in dfB.columns:
    print("\n  Monotonicity check (HIGH > MEDIUM > LOW actual churn rate?)")
    for seg, grp in dfB.groupby('SEGMENT'):
        tiers = grp.set_index('CONTRACT_RISK_TIER')['ACTUAL_CHURN_PCT']
        h = tiers.get('HIGH', np.nan)
        m = tiers.get('MEDIUM', np.nan)
        l = tiers.get('LOW', np.nan)
        ok = (pd.isna(h) or pd.isna(l) or h > l)
        flag = "✓" if ok else "*** INVERTED ***"
        print(f"    {seg:25s}  HIGH={h:.1f}%  MED={m:.1f}%  LOW={l:.1f}%  {flag}")

# ─────────────────────────────────────────────────────────────────────────────
hdr("C — Decile calibration (10 buckets by predicted rate, actual vs predicted)")
# ─────────────────────────────────────────────────────────────────────────────
qC = """
WITH preds AS (
    SELECT p.CONTRACT_ID_UFR,
           DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RM,
           p.ATR, p.PRED_RENEW_RATE_FINAL,
           NTILE(10) OVER (ORDER BY p.PRED_RENEW_RATE_FINAL) AS DECILE
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS p
    WHERE p.SPLIT IN ('CAL','VALIDATION')
      AND p.HORIZON = 0 AND p.ATR > 0
      AND p.RENEWAL_MONTH >= '2025-01-01'
),
actuals AS (
    SELECT CONTRACT_ID AS CID, RENEWAL_MONTH AS RM,
           ACTUAL_RETAINED_ARR, ATR
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH = TRUE AND ATR > 0
)
SELECT
    p.DECILE,
    ROUND(SUM(p.PRED_RENEW_RATE_FINAL*p.ATR)/NULLIF(SUM(p.ATR),0)*100,2) AS PRED_RATE,
    ROUND(SUM(COALESCE(a.ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(a.ATR),0)*100,2) AS ACTUAL_RATE,
    ROUND(SUM(p.PRED_RENEW_RATE_FINAL*p.ATR)/NULLIF(SUM(p.ATR),0)*100
          - SUM(COALESCE(a.ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(a.ATR),0)*100, 2) AS BIAS_PP,
    ROUND(SUM(p.ATR)/1e6, 2) AS ATR_M,
    COUNT(*) AS N
FROM preds p
LEFT JOIN actuals a ON a.CID=p.CONTRACT_ID_UFR AND a.RM=p.RM
GROUP BY p.DECILE
ORDER BY p.DECILE
"""
dfC = fetch_dataframe(qC, conn=conn)
print(dfC.to_string(index=False))
if not dfC.empty and dfC['ACTUAL_RATE'].notna().sum() > 0:
    bias_by_decile = dfC['BIAS_PP'].abs().mean()
    print(f"\n  Avg calibration error across deciles = {bias_by_decile:.2f}pp")
    largest_bias = dfC.loc[dfC['BIAS_PP'].abs().idxmax()]
    print(f"  Largest single decile bias: D{int(largest_bias['DECILE'])} at {largest_bias['BIAS_PP']:.2f}pp")
    # Check monotonicity
    pred_mono = dfC['PRED_RATE'].is_monotonic_increasing
    actual_mono = dfC['ACTUAL_RATE'].dropna().is_monotonic_increasing
    print(f"  Predicted rates monotone ↑: {pred_mono} | Actual rates monotone ↑: {actual_mono}")
    if not actual_mono:
        print("  *** Non-monotone actuals across deciles — potential calibration gap ***")

# ─────────────────────────────────────────────────────────────────────────────
hdr("D — Segment × Portfolio forward coverage (blind spot check)")
# ─────────────────────────────────────────────────────────────────────────────
qD = """
SELECT
    DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE  AS RM,
    p.SEGMENT,
    -- Approximate portfolio via PRODUCT_PORTFOLIO from detail table
    d.PRODUCT_PORTFOLIO,
    COUNT(DISTINCT p.CONTRACT_ID_UFR)            AS N_CONTRACTS,
    ROUND(SUM(p.ATR)/1e6, 3)                     AS ATR_M,
    ROUND(SUM(p.PRED_RENEW_RATE_FINAL*p.ATR)/NULLIF(SUM(p.ATR),0)*100, 2) AS PRED_RATE,
    ROUND(AVG(p.P_CHURN_CAL)*100, 2)             AS AVG_P_CHURN_PCT,
    SUM(p.EARLY_WARNING_FLAG)                    AS N_EARLY_WARNING
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS p
LEFT JOIN STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
  ON d.CONTRACT_ID = p.CONTRACT_ID_UFR
 AND d.RENEWAL_MONTH = DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE
WHERE p.SPLIT = 'SCORE'
  AND p.RENEWAL_MONTH = DATE_TRUNC('MONTH', CURRENT_DATE())::DATE
  AND p.HORIZON = 0
  AND p.ATR > 0
GROUP BY 1,2,3
ORDER BY 2,3
"""
dfD = fetch_dataframe(qD, conn=conn)
print(dfD.to_string(index=False))
if not dfD.empty:
    null_port = dfD['PRODUCT_PORTFOLIO'].isna().sum()
    print(f"\n  Segment×Portfolio combos = {len(dfD)} | Unmapped portfolio rows = {null_port}")

# ─────────────────────────────────────────────────────────────────────────────
hdr("E — Training data source: contract vs portfolio CARR tables (feature store check)")
# ─────────────────────────────────────────────────────────────────────────────
# Check which source is used in the feature store
qE = """
SELECT
    'Feature store (contract snap)' AS SOURCE,
    COUNT(DISTINCT CONTRACT_ID_UFR) AS N_CONTRACTS,
    COUNT(*) AS N_ROWS,
    MIN(DATE_TRUNC('MONTH',MASTER_DATE)) AS FIRST_MONTH,
    MAX(DATE_TRUNC('MONTH',MASTER_DATE)) AS LAST_MONTH,
    ROUND(SUM(ATR)/1e6, 1) AS TOTAL_ATR_M,
    ROUND(SUM(TARGET__RENEWED_AMOUNT)/NULLIF(SUM(ATR),0)*100, 2) AS OVERALL_RENEWAL_RATE
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_FEATURE_STORE
WHERE DATE_TRUNC('MONTH', MASTER_DATE) >= '2025-01-01'
  AND ATR > 0
"""
dfE = fetch_dataframe(qE, conn=conn)
print(dfE.to_string(index=False))

# Compare to portfolio source
qE2 = """
SELECT
    'CARR Portfolio LVL (app display)' AS SOURCE,
    COUNT(DISTINCT CONTRACT_ID_UFR)    AS N_CONTRACTS,
    COUNT(*) AS N_ROWS,
    MIN(DATE_TRUNC('MONTH',MASTER_DATE)) AS FIRST_MONTH,
    MAX(DATE_TRUNC('MONTH',MASTER_DATE)) AS LAST_MONTH,
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE)/1e6, 1) AS TOTAL_ATR_M,
    ROUND(SUM(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE)/NULLIF(SUM(ADJ_ATR_C_BUDGET_RATE),0)*100, 2) AS OVERALL_RENEWAL_RATE
FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
WHERE INCLUDE_FLAG_C = 1
  AND DATE_TRUNC('MONTH', MASTER_DATE) >= '2025-01-01'
  AND ADJ_ATR_C_BUDGET_RATE > 0
"""
dfE2 = fetch_dataframe(qE2, conn=conn)
print(dfE2.to_string(index=False))

# Compare to contract source
qE3 = """
SELECT
    'CARR Contract LVL' AS SOURCE,
    COUNT(DISTINCT CONTRACT_ID)   AS N_CONTRACTS,
    COUNT(*) AS N_ROWS,
    MIN(DATE_TRUNC('MONTH',MASTER_DATE)) AS FIRST_MONTH,
    MAX(DATE_TRUNC('MONTH',MASTER_DATE)) AS LAST_MONTH,
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE)/1e6, 1) AS TOTAL_ATR_M,
    ROUND(SUM(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE)/NULLIF(SUM(ADJ_ATR_C_BUDGET_RATE),0)*100, 2) AS OVERALL_RENEWAL_RATE
FROM ANALYTICS.DBO.CARR__RENEWALS_CONTRACT_LVL
WHERE INCLUDE_FLAG_C = 1
  AND DATE_TRUNC('MONTH', MASTER_DATE) >= '2025-01-01'
  AND ADJ_ATR_C_BUDGET_RATE > 0
"""
dfE3 = fetch_dataframe(qE3, conn=conn)
print(dfE3.to_string(index=False))

if not dfE.empty and not dfE2.empty and not dfE3.empty:
    fs_rate = dfE['OVERALL_RENEWAL_RATE'].iloc[0]
    port_rate = dfE2['OVERALL_RENEWAL_RATE'].iloc[0]
    contract_rate = dfE3['OVERALL_RENEWAL_RATE'].iloc[0]
    print(f"\n  Feature store trains at: {fs_rate:.2f}%  (contract snap)")
    print(f"  Portfolio CARR actuals:  {port_rate:.2f}%  (Finance/app authoritative)")
    print(f"  Contract CARR actuals:   {contract_rate:.2f}%")
    print(f"  FS vs Portfolio gap:  {fs_rate-port_rate:.2f}pp  (global calibration corrects this)")
    print(f"  FS vs Contract gap:   {fs_rate-contract_rate:.2f}pp")

# ─────────────────────────────────────────────────────────────────────────────
hdr("F — Business question battery (6 questions)")
# ─────────────────────────────────────────────────────────────────────────────

print("\n  BQ1: Which contracts are risky in the next 1-2 months?")
qF1 = """
SELECT
    SEGMENT,
    CONTRACT_RISK_TIER,
    COUNT(*) AS N,
    ROUND(SUM(AT_RISK_DOLLARS)/1e6, 2) AS AT_RISK_M,
    ROUND(AVG(P_CHURN_CAL)*100, 2)     AS AVG_CHURN_PCT
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
WHERE SPLIT = 'SCORE'
  AND RENEWAL_MONTH BETWEEN DATE_TRUNC('MONTH', CURRENT_DATE())
                        AND DATEADD('MONTH', 1, DATE_TRUNC('MONTH', CURRENT_DATE()))
  AND HORIZON = 0
  AND ATR > 0
  AND EARLY_WARNING_FLAG = 1
GROUP BY 1,2
ORDER BY 1,2
"""
dfF1 = fetch_dataframe(qF1, conn=conn)
print(dfF1.to_string(index=False))
total_at_risk = dfF1['AT_RISK_M'].sum() if not dfF1.empty else 0
print(f"  Total early-warning at-risk next 2 months: ${total_at_risk:.2f}M")
print("  ✓ Can answer BQ1 at contract grain" if not dfF1.empty else "  *** No SCORE data ***")

print("\n  BQ2: Which segments/portfolios at risk in next 6 months?")
qF2 = """
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RM,
    SEGMENT,
    ROUND(SUM(PRED_RENEW_RATE_FINAL*ATR)/NULLIF(SUM(ATR),0)*100, 2) AS FORECAST_RATE,
    ROUND(SUM(AT_RISK_DOLLARS)/1e6, 2) AS AT_RISK_M,
    ROUND(SUM(ATR)/1e6, 2) AS ATR_M
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
WHERE SPLIT = 'SCORE'
  AND RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
  AND HORIZON = 0 AND ATR > 0
GROUP BY 1,2
ORDER BY 1,2
"""
dfF2 = fetch_dataframe(qF2, conn=conn)
print(dfF2.to_string(index=False))

print("\n  BQ3: Q3 retention % and $ for board (contract grain)?")
qF3 = """
SELECT
    CASE WHEN RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-09-01' THEN 'Q3-2026' END AS QTR,
    ROUND(SUM(ATR)/1e6, 2)                                                         AS ATR_M,
    ROUND(SUM(RENEWAL_FORECAST)/1e6, 2)                                            AS FORECAST_RETAINED_M,
    ROUND(SUM(RENEWAL_FORECAST)/NULLIF(SUM(ATR),0)*100, 2)                         AS FORECAST_RATE_PCT,
    ROUND(SUM(RENEWAL_FORECAST)*(1/NULLIF(SUM(ATR),0)-1)*SUM(ATR)/1e6, 2)          AS AT_RISK_M
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-09-01'
GROUP BY 1
HAVING QTR IS NOT NULL
"""
dfF3 = fetch_dataframe(qF3, conn=conn)
print(dfF3.to_string(index=False))

print("\n  BQ4: How far forward can the model be trusted?")
print("       H0 (current): HIGH — blends actuals + locked ML on open")
print("       H1 (Jul):     HIGH — AUC 0.73-0.84, W=0.55, H3 MAE=0.65pp in walk-forward")
print("       H2 (Aug):     HIGH — W=0.50")
print("       H3 (Sep):     PLANNING — W=0.20, Q3 total MAE=0.65pp")
print("       H4-5:         DIRECTIONAL — W=0.05-0.10")

print("\n  BQ5: Segment-level forecast vs recent actuals (alignment check)")
qF5 = """
WITH fwd AS (
    SELECT SEGMENT,
           ROUND(SUM(PRED_RENEW_RATE_FINAL*ATR)/NULLIF(SUM(ATR),0)*100,2) AS FORWARD_RATE,
           ROUND(SUM(ATR)/1e6,2) AS FWD_ATR_M
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
    WHERE SPLIT='SCORE' AND HORIZON=0 AND ATR>0
    GROUP BY SEGMENT
),
recent AS (
    SELECT SEGMENT,
           ROUND(SUM(ACTUAL_RETAINED_ARR)/NULLIF(SUM(ATR),0)*100,2) AS TRAILING_RATE,
           ROUND(SUM(ATR)/1e6,2) AS HIST_ATR_M
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH=TRUE
      AND RENEWAL_MONTH >= DATEADD('MONTH',-6,DATE_TRUNC('MONTH',CURRENT_DATE()))
      AND ATR>0
    GROUP BY SEGMENT
)
SELECT f.SEGMENT, f.FWD_ATR_M, f.FORWARD_RATE, r.TRAILING_RATE,
       ROUND(f.FORWARD_RATE - r.TRAILING_RATE, 2) AS DELTA_PP
FROM fwd f
LEFT JOIN recent r ON r.SEGMENT=f.SEGMENT
ORDER BY f.SEGMENT
"""
dfF5 = fetch_dataframe(qF5, conn=conn)
print(dfF5.to_string(index=False))
if not dfF5.empty and 'DELTA_PP' in dfF5.columns:
    large_delta = dfF5[dfF5['DELTA_PP'].abs() > 5]
    if not large_delta.empty:
        print(f"  *** {len(large_delta)} segment(s) with >5pp gap vs trailing 6-month actuals ***")
        print(large_delta[['SEGMENT','FORWARD_RATE','TRAILING_RATE','DELTA_PP']].to_string(index=False))
    else:
        print("  ✓ All segments within 5pp of trailing 6-month actuals")

print("\n  BQ6: P10/P90 confidence intervals — are they honest?")
qF6 = """
WITH preds AS (
    SELECT p.CONTRACT_ID_UFR,
           DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RM,
           p.ATR,
           p.PRED_RENEW_RATE_FINAL,
           p.PRED_RENEW_RATE_P10,
           p.PRED_RENEW_RATE_P90
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS p
    WHERE p.SPLIT = 'VALIDATION' AND p.HORIZON = 0 AND p.ATR > 0
),
actuals AS (
    SELECT CONTRACT_ID AS CID, RENEWAL_MONTH AS RM,
           ACTUAL_RETAINED_ARR/NULLIF(ATR,0) AS ACTUAL_RATE
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH=TRUE AND ATR>0
)
SELECT
    DATE_TRUNC('MONTH', p.RM) AS MONTH,
    COUNT(*) AS N,
    -- Coverage: fraction of actuals falling within P10-P90 (80% interval, should be ~80%)
    ROUND(AVG(CASE WHEN a.ACTUAL_RATE BETWEEN p.PRED_RENEW_RATE_P10 AND p.PRED_RENEW_RATE_P90
                   THEN 1.0 ELSE 0.0 END)*100, 1) AS P10_P90_COVERAGE_PCT,
    ROUND(AVG(p.PRED_RENEW_RATE_P90-p.PRED_RENEW_RATE_P10)*100, 1) AS AVG_INTERVAL_WIDTH_PP
FROM preds p
LEFT JOIN actuals a ON a.CID=p.CONTRACT_ID_UFR AND a.RM=p.RM
WHERE a.ACTUAL_RATE IS NOT NULL
GROUP BY 1
ORDER BY 1
"""
dfF6 = fetch_dataframe(qF6, conn=conn)
if dfF6.empty:
    print("  No validation actuals for P10/P90 check (actuals table may need rebuild)")
else:
    print(dfF6.to_string(index=False))
    avg_cov = dfF6['P10_P90_COVERAGE_PCT'].mean()
    print(f"\n  Avg P10-P90 coverage = {avg_cov:.1f}% (target: ~70-90% for an honest 80% interval)")
    if avg_cov < 60:
        print("  *** Intervals too narrow — P10/P90 overconfident ***")
    elif avg_cov > 95:
        print("  *** Intervals too wide — P10/P90 underconfident ***")
    else:
        print("  ✓ Coverage within reasonable range")

# ─────────────────────────────────────────────────────────────────────────────
hdr("G — Historical snapshot status (which months are CLOSED = open_opp=0?)")
# ─────────────────────────────────────────────────────────────────────────────
qG = """
SELECT
    RENEWAL_MONTH,
    SUM(ATR)                                                                       AS ATR,
    SUM(COALESCE(ACTUAL_RETAINED_ARR,0))                                          AS ACTUAL,
    SUM(COALESCE(OPEN_OPP,0))                                                     AS OPEN_OPP_SUM,
    IS_MATURED_MONTH,
    COUNT(*)                                                                       AS N_ROWS,
    ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(ATR),0)*100,2)         AS ACTUAL_RATE
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH >= '2025-06-01'
  AND RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
GROUP BY RENEWAL_MONTH, IS_MATURED_MONTH
ORDER BY RENEWAL_MONTH
"""
dfG = fetch_dataframe(qG, conn=conn)
print(dfG.to_string(index=False))
snapped = dfG[(dfG['OPEN_OPP_SUM'] == 0) & (dfG['IS_MATURED_MONTH'] == True)]
not_snapped = dfG[(dfG['OPEN_OPP_SUM'] > 0) | (dfG['IS_MATURED_MONTH'] == False)]
print(f"\n  Fully closed months (open_opp=0): {dfG['RENEWAL_MONTH'].unique().tolist()}")
print("  *** NOTE: Dev app currently does NOT snapshot closed months to a separate table.")
print("      Prod does. This needs to be implemented for the dev app snapshot feature.")

# ─────────────────────────────────────────────────────────────────────────────
hdr("H — Churn tier calibration ACROSS ALL PORTFOLIOS (no blind spots)")
# ─────────────────────────────────────────────────────────────────────────────
qH = """
WITH preds AS (
    SELECT p.CONTRACT_ID_UFR,
           DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RM,
           p.SEGMENT,
           p.ATR,
           p.PRED_RENEW_RATE_FINAL,
           p.CONTRACT_RISK_TIER,
           p.P_CHURN_CAL,
           d.PRODUCT_PORTFOLIO
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS p
    LEFT JOIN STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
      ON d.CONTRACT_ID = p.CONTRACT_ID_UFR
     AND d.RENEWAL_MONTH = DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE
    WHERE p.SPLIT IN ('CAL','VALIDATION')
      AND p.HORIZON = 0
      AND p.ATR > 0
      AND p.RENEWAL_MONTH >= '2025-01-01'
),
actuals AS (
    SELECT CONTRACT_ID AS CID, RENEWAL_MONTH AS RM,
           ACTUAL_RETAINED_ARR, ATR
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH=TRUE AND ATR>0
)
SELECT
    p.PRODUCT_PORTFOLIO,
    p.CONTRACT_RISK_TIER,
    COUNT(*) AS N,
    ROUND(AVG(p.P_CHURN_CAL)*100, 2)                                              AS AVG_PRED_CHURN,
    ROUND(SUM(COALESCE(a.ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(a.ATR),0)*100, 2)    AS ACTUAL_RENEW_RATE,
    ROUND((1-SUM(COALESCE(a.ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(a.ATR),0))*100,2) AS ACTUAL_CHURN_PCT,
    ROUND(SUM(p.ATR)/1e6, 2)                                                      AS ATR_M
FROM preds p
LEFT JOIN actuals a ON a.CID=p.CONTRACT_ID_UFR AND a.RM=p.RM
WHERE p.PRODUCT_PORTFOLIO IS NOT NULL
GROUP BY p.PRODUCT_PORTFOLIO, p.CONTRACT_RISK_TIER
HAVING ATR_M > 0.5
ORDER BY p.PRODUCT_PORTFOLIO, p.CONTRACT_RISK_TIER
"""
dfH = fetch_dataframe(qH, conn=conn)
print(dfH.to_string(index=False))

# Check for blind spots: portfolios where HIGH tier does NOT have higher churn than LOW
if not dfH.empty and 'ACTUAL_CHURN_PCT' in dfH.columns:
    print("\n  Portfolio × tier monotonicity check:")
    for port, grp in dfH.groupby('PRODUCT_PORTFOLIO'):
        tiers = grp.set_index('CONTRACT_RISK_TIER')['ACTUAL_CHURN_PCT']
        h = tiers.get('HIGH', np.nan)
        l = tiers.get('LOW', np.nan)
        if not pd.isna(h) and not pd.isna(l):
            ok = h > l
            flag = "✓" if ok else "*** BLIND SPOT ***"
            print(f"    {port:30s}  HIGH={h:.1f}%  LOW={l:.1f}%  {flag}")

conn.close()
print(f"\n{SEP}\nDEEP CALIBRATION AUDIT DONE\n{SEP}")
