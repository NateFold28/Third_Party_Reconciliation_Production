"""
Complete ML model quality audit — answers:
  Q1. Why does ML_FORECAST show 80%+ when actuals are 70%?
  Q2. Is the blended/final model actually accurate?
  Q3. Does the model correctly rank risky contracts?
  Q4. Is it plugged in correctly in the app?
"""
import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from connection import fetch_dataframe
import pandas as pd

PREDS   = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
FEAT    = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE"
BT      = "STREAMLIT_APPS.DBO.V5_SANDBOX_APP_BACKTEST"
WF      = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_WALK_FORWARD"
APP     = "STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"
sep     = "\n" + "=" * 80 + "\n"

# ─────────────────────────────────────────────────────────────────────────────
# A. The 80% question — compare RAW vs FINAL on the SCORE split,
#    and explain using BASE_RATE, W_HORIZON, ML_DELTA
# ─────────────────────────────────────────────────────────────────────────────
q_a = f"""
WITH latest AS (
    SELECT RUN_ID FROM {PREDS}
    GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
)
SELECT
    p.SEGMENT,
    COUNT(*)                                                         AS N,
    ROUND(AVG(p.BASE_RATE) * 100, 2)                                AS AVG_ANCHOR_PCT,
    ROUND(SUM(p.E_RENEWAL_RATE * p.ATR) / NULLIF(SUM(p.ATR), 0) * 100, 2) AS RAW_ML_PCT,
    ROUND(SUM(p.FINAL_DOLLARS) / NULLIF(SUM(p.ATR), 0) * 100, 2)   AS FINAL_BLENDED_PCT,
    ROUND(AVG(p.ML_DELTA) * 100, 2)                                  AS AVG_ML_DELTA_PP,
    ROUND(AVG(p.W_HORIZON), 3)                                       AS AVG_W_HORIZON,
    ROUND(AVG(p.P_CHURN_CAL) * 100, 1)                               AS AVG_P_CHURN_PCT
FROM {PREDS} p
JOIN latest l ON l.RUN_ID = p.RUN_ID
WHERE p.SPLIT = 'SCORE'
GROUP BY 1
ORDER BY 1
"""
print(sep)
print("A — Why does ML_FORECAST show 80%?  SCORE rows: raw E_RENEWAL_RATE vs final blended rate")
print("""
  RAW_ML_PCT     = E_RENEWAL_RATE — the 3-outcome model's raw expected renewal rate.
                   HIGH because it predicts per-contract P(churn) on forward
                   contracts that haven't yet filtered themselves out by churning.
  AVG_ANCHOR_PCT = historical base rate the anchor layer targets.
  FINAL_BLENDED  = base + W_HORIZON × ML_DELTA — what becomes FINANCE_FORECAST.
                   Should be close to AVG_ANCHOR. This is the board number.
""")
print(fetch_dataframe(q_a).to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# B. Board accuracy — latest backtest (FINAL blended vs actuals by segment+month)
# ─────────────────────────────────────────────────────────────────────────────
q_b = f"""
SELECT
    b.SEGMENT,
    DATE_TRUNC('MONTH', b.RENEWAL_MONTH)::DATE                        AS MONTH,
    b.N_CONTRACTS,
    ROUND(b.ATR / 1e6, 2)                                             AS ATR_M,
    ROUND(b.PREDICTED_RATE_PCT, 2)                                    AS PREDICTED_PCT,
    ROUND(b.ACTUAL_RATE_PCT, 2)                                       AS ACTUAL_PCT,
    ROUND(b.ERROR_PP, 2)                                              AS BIAS_PP,
    ABS(ROUND(b.ERROR_PP, 2))                                         AS ABS_ERROR_PP
FROM {BT} b
WHERE b.RUN_ID = (
    SELECT RUN_ID FROM {BT}
    GROUP BY RUN_ID ORDER BY MAX(BUILT_AT) DESC LIMIT 1
)
ORDER BY b.SEGMENT, b.RENEWAL_MONTH
"""
print(sep)
print("B — Board accuracy: FINAL blended vs actual (per-segment, per-month, latest backtest run)")
print("Gate: |BIAS| <= 5pp per segment on Jan-May 2026 = BOARD READY")
df_b = fetch_dataframe(q_b)
print(df_b.to_string(index=False))
print()
# Summary: overall bias and max error
if not df_b.empty:
    df_b["ABS_ERROR_PP"] = df_b["ABS_ERROR_PP"].astype(float)
    df_b["BIAS_PP"] = df_b["BIAS_PP"].astype(float)
    seg_summary = df_b.groupby("SEGMENT").agg(
        AVG_BIAS=("BIAS_PP", "mean"),
        MAX_ABS_ERROR=("ABS_ERROR_PP", "max"),
        N_MONTHS=("MONTH", "count"),
    ).round(2)
    print("Summary by segment:")
    print(seg_summary.to_string())

# ─────────────────────────────────────────────────────────────────────────────
# C. Walk-forward rank quality — does P_CHURN_CAL rank contracts correctly?
#    (AUC proxy: H=0 Spearman by segment for the LATEST run)
# ─────────────────────────────────────────────────────────────────────────────
q_c = f"""
SELECT
    w.SEGMENT,
    w.HORIZON_BUCKET,
    w.N_CONTRACTS,
    ROUND(w.PREDICTED_RATE * 100, 2)      AS PREDICTED_PCT,
    ROUND(w.ACTUAL_RATE * 100, 2)         AS ACTUAL_PCT,
    ROUND(w.MAE_PP, 2)                    AS MAE_PP,
    ROUND(w.BIAS_PP, 2)                   AS BIAS_PP,
    ROUND(w.RANK_SPEARMAN, 3)             AS SPEARMAN,
    w.BEATS_NAIVE                         AS BEATS_NAIVE
FROM {WF} w
WHERE w.RUN_ID = (
    SELECT RUN_ID FROM {WF}
    GROUP BY RUN_ID ORDER BY MAX(RUN_ID) DESC LIMIT 1
)
  AND w.HORIZON_BUCKET <= 2
ORDER BY w.SEGMENT, w.HORIZON_BUCKET
"""
print(sep)
print("C — Walk-forward rank quality: Spearman rank correlation, H=0-2")
print("Gate: RANK_SPEARMAN >= 0.30 on H=0-2 means the model identifies risky contracts reliably")
print(fetch_dataframe(q_c).to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# D. Contract-level risk ranking: do HIGH-tier contracts actually churn more?
#    Join predictions (SCORE split) back to the feature store CAL+VALIDATION
#    rows that share the same (CONTRACT_ID_UFR, RENEWAL_MONTH) to compare
#    the model's churn probability against the realized outcome
# ─────────────────────────────────────────────────────────────────────────────
q_d = f"""
WITH latest AS (
    SELECT RUN_ID FROM {PREDS}
    GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
),
labeled AS (
    SELECT
        f.CONTRACT_ID_UFR,
        DATE_TRUNC('MONTH', f.RENEWAL_MONTH)::DATE  AS RENEWAL_MONTH,
        f.TARGET__IS_CHURN,
        f.TARGET__RENEWED_AMOUNT,
        f.ATR,
        f.F_SEGMENT
    FROM {FEAT} f
    WHERE f.SPLIT IN ('CAL', 'VALIDATION')
      AND f.TARGET__IS_CHURN IS NOT NULL
      AND f.HORIZON = 0
),
joined AS (
    SELECT
        p.SEGMENT,
        p.RISK_PCTL_IN_SEG,
        p.P_CHURN_CAL,
        p.CONTRACT_RISK_TIER,
        la.TARGET__IS_CHURN,
        la.ATR
    FROM {PREDS} p
    JOIN latest l ON l.RUN_ID = p.RUN_ID
    JOIN labeled la
        ON la.CONTRACT_ID_UFR = p.CONTRACT_ID_UFR
       AND la.RENEWAL_MONTH   = DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE
    WHERE p.HORIZON = 0
      AND p.SPLIT IN ('CAL', 'VALIDATION')
)
SELECT
    j.SEGMENT,
    j.CONTRACT_RISK_TIER,
    CASE
        WHEN j.RISK_PCTL_IN_SEG >= 0.80 THEN 'TOP 20% (highest risk)'
        WHEN j.RISK_PCTL_IN_SEG >= 0.60 THEN '60-80th pctl'
        WHEN j.RISK_PCTL_IN_SEG >= 0.40 THEN '40-60th pctl'
        WHEN j.RISK_PCTL_IN_SEG >= 0.20 THEN '20-40th pctl'
        ELSE 'BOT 20% (lowest risk)'
    END                                                               AS RISK_BAND,
    COUNT(*)                                                          AS N,
    ROUND(SUM(j.TARGET__IS_CHURN) / COUNT(*) * 100, 1)               AS ACTUAL_CHURN_RATE_PCT,
    ROUND(AVG(j.P_CHURN_CAL) * 100, 1)                               AS AVG_MODEL_CHURN_PCT,
    ROUND(SUM(j.ATR) / 1e6, 2)                                       AS ATR_M
FROM joined j
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
"""
print(sep)
print("D — Risk tier calibration: model-predicted churn vs actual churn rate by percentile band")
print("Expected: monotonic increase in ACTUAL_CHURN_RATE as RISK_BAND goes from BOT→TOP")
print(fetch_dataframe(q_d).to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# E. Wiring check: does the app ML_FORECAST == E_RENEWAL_RATE * ATR?
#    And FINANCE_FORECAST == FINAL_DOLLARS?
# ─────────────────────────────────────────────────────────────────────────────
q_e = f"""
WITH latest AS (
    SELECT RUN_ID FROM {PREDS}
    GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
)
SELECT
    DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE   AS MONTH,
    p.SEGMENT,
    COUNT(*)                                      AS N,
    -- App ML_FORECAST = ML_DOLLARS = E_RENEWAL_RATE * ATR (from compat view)
    ROUND(SUM(p.E_RENEWAL_RATE * p.ATR) / NULLIF(SUM(p.ATR), 0) * 100, 2)  AS APP_ML_PCT,
    -- App FINANCE_FORECAST = FINAL_DOLLARS
    ROUND(SUM(p.FINAL_DOLLARS) / NULLIF(SUM(p.ATR), 0) * 100, 2)            AS APP_FIN_PCT,
    -- Cross-check against V5_SANDBOX_APP_CONTRACT_DETAIL
    ROUND(SUM(a.ML_FORECAST) / NULLIF(SUM(a.ATR), 0) * 100, 2)              AS TBL_ML_PCT,
    ROUND(SUM(a.FINANCE_FORECAST) / NULLIF(SUM(a.ATR), 0) * 100, 2)         AS TBL_FIN_PCT
FROM {PREDS} p
JOIN latest l ON l.RUN_ID = p.RUN_ID
LEFT JOIN {APP} a
    ON a.CONTRACT_ID   = p.CONTRACT_ID_UFR
   AND a.RENEWAL_MONTH = DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE
WHERE p.SPLIT = 'SCORE'
  AND p.RENEWAL_MONTH >= '2026-06-01'
  AND p.RENEWAL_MONTH <= '2026-12-01'
GROUP BY 1, 2
ORDER BY 1, 2
"""
print(sep)
print("E — Wiring check: do predictions table rates == app table rates for Jun-Dec 2026?")
print("If APP_ML_PCT ≈ TBL_ML_PCT and APP_FIN_PCT ≈ TBL_FIN_PCT, wiring is correct")
print(fetch_dataframe(q_e).to_string(index=False))

print(sep)
print("ALL CHECKS COMPLETE")
