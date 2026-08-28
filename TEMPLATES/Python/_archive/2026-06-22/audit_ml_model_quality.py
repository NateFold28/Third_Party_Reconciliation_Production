"""
Deep audit of why ML_FORECAST shows 80-83% vs historical actuals at 70-73%.

This script explains the architecture, then validates the model DOES work correctly
by checking three things:
  1. Raw E_RENEWAL_RATE vs FINAL (blended) rate — expected 10pp+ gap (architectural, not a bug)
  2. CAL+VALIDATION backtest accuracy — FINAL_RATE vs actuals at contract level
  3. Rank quality — do high-risk contracts actually churn more? (AUC proxy)
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from connection import fetch_dataframe

PREDS = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
APP   = "STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"
separator = "\n" + "=" * 80 + "\n"

# ─────────────────────────────────────────────────────────────────────────────
# ARCHITECTURE EXPLAINER QUERY
# Confirm: ML_DOLLARS = E_RENEWAL_RATE * ATR (raw 3-outcome model, no anchor)
#          FINAL_DOLLARS = PRED_RENEW_RATE_FINAL * ATR (anchored / blended)
# ─────────────────────────────────────────────────────────────────────────────
# Check A part 1: labeled splits (CAL / VALIDATION) — have actuals
q_arch_labeled = f"""
WITH latest AS (
    SELECT RUN_ID FROM {PREDS} GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
)
SELECT
    p.SPLIT,
    COUNT(*)                                                       AS N,
    ROUND(SUM(p.E_RENEWAL_RATE * p.ATR) / NULLIF(SUM(p.ATR), 0) * 100, 2)  AS RAW_ML_RATE_PCT,
    ROUND(SUM(p.FINAL_DOLLARS) / NULLIF(SUM(p.ATR), 0) * 100, 2)           AS FINAL_BLENDED_RATE_PCT,
    ROUND(SUM(p.TARGET__RENEWED_AMOUNT) / NULLIF(SUM(p.ATR), 0) * 100, 2)  AS ACTUAL_RATE_PCT,
    ROUND((SUM(p.FINAL_DOLLARS) - SUM(p.TARGET__RENEWED_AMOUNT)) / NULLIF(SUM(p.ATR), 0) * 100, 2)         AS FINAL_BIAS_PP,
    ROUND((SUM(p.E_RENEWAL_RATE * p.ATR) - SUM(p.TARGET__RENEWED_AMOUNT)) / NULLIF(SUM(p.ATR), 0) * 100, 2) AS RAW_ML_BIAS_PP
FROM {PREDS} p
JOIN latest l ON l.RUN_ID = p.RUN_ID
WHERE p.SPLIT IN ('CAL', 'VALIDATION')
  AND p.TARGET__RENEWED_AMOUNT IS NOT NULL
  AND p.HORIZON = 0
GROUP BY p.SPLIT
ORDER BY p.SPLIT
"""

# Check A part 2: SCORE rows (forward, no actuals) — just show raw vs final
q_arch_score = f"""
WITH latest AS (
    SELECT RUN_ID FROM {PREDS} GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
)
SELECT
    'SCORE'                                                         AS SPLIT,
    COUNT(*)                                                        AS N,
    ROUND(SUM(p.E_RENEWAL_RATE * p.ATR) / NULLIF(SUM(p.ATR), 0) * 100, 2)  AS RAW_ML_RATE_PCT,
    ROUND(SUM(p.FINAL_DOLLARS) / NULLIF(SUM(p.ATR), 0) * 100, 2)           AS FINAL_BLENDED_RATE_PCT,
    NULL                                                            AS ACTUAL_RATE_PCT,
    NULL                                                            AS FINAL_BIAS_PP,
    NULL                                                            AS RAW_ML_BIAS_PP
FROM {PREDS} p
JOIN latest l ON l.RUN_ID = p.RUN_ID
WHERE p.SPLIT = 'SCORE'
"""

print(separator)
print("CHECK A — Architecture: RAW ML rate vs FINAL (blended) rate vs actual")
print("Raw ML = E_RENEWAL_RATE (3-outcome model, no anchor)")
print("Final  = PRED_RENEW_RATE_FINAL (anchor + W_HORIZON delta + calibration)")
print("")
import pandas as pd
df_a = pd.concat([fetch_dataframe(q_arch_labeled), fetch_dataframe(q_arch_score)], ignore_index=True)
print(df_a.to_string(index=False))
print("""
WHAT TO EXPECT:
  RAW_ML_RATE on SCORE rows: 75-85% (raw E[rate] without anchor — inflated, by design)
  FINAL_BLENDED on SCORE rows: ~70% (pulled back to historical base — should match actuals)
  FINAL_BLENDED on CAL/VAL: near ACTUAL (low bias = model is calibrated)
  RAW_ML on CAL/VAL: also elevated vs actual = covariate shift is real, anchor is doing its job""")

# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST ACCURACY BY SEGMENT + MONTH (CAL and VALIDATION splits)
# This is the TRUE test of whether the FINAL blended model is accurate
# ─────────────────────────────────────────────────────────────────────────────
q_bt = f"""
WITH latest AS (
    SELECT RUN_ID FROM {PREDS}
    GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
)
SELECT
    p.F_SEGMENT                                                  AS SEGMENT,
    DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE                   AS MONTH,
    p.SPLIT,
    COUNT(*)                                                      AS N_CONTRACTS,
    ROUND(SUM(p.ATR) / 1e6, 2)                                   AS ATR_M,
    ROUND(SUM(p.TARGET__RENEWED_AMOUNT) / NULLIF(SUM(p.ATR), 0) * 100, 2)  AS ACTUAL_RATE,
    ROUND(SUM(p.FINAL_DOLLARS) / NULLIF(SUM(p.ATR), 0) * 100, 2)           AS FINAL_RATE,
    ROUND(SUM(p.E_RENEWAL_RATE * p.ATR) / NULLIF(SUM(p.ATR), 0) * 100, 2)  AS RAW_ML_RATE,
    ROUND(
        (SUM(p.FINAL_DOLLARS) - SUM(p.TARGET__RENEWED_AMOUNT)) / NULLIF(SUM(p.ATR), 0) * 100
    , 2)                                                          AS FINAL_BIAS_PP,
    ROUND(
        ABS(SUM(p.FINAL_DOLLARS) - SUM(p.TARGET__RENEWED_AMOUNT)) / NULLIF(SUM(p.ATR), 0) * 100
    , 2)                                                          AS FINAL_ABS_ERROR_PP
FROM {PREDS} p
JOIN latest l ON l.RUN_ID = p.RUN_ID
WHERE p.SPLIT IN ('CAL', 'VALIDATION')
  AND p.TARGET__RENEWED_AMOUNT IS NOT NULL
  AND p.HORIZON = 0
GROUP BY 1, 2, 3
ORDER BY 3, 1, 2
"""

print(separator)
print("CHECK B — Backtest accuracy: FINAL blended model on CAL + VALIDATION splits, H=0")
print("(This is the honest out-of-sample test that drives the board gate)")
df_bt = fetch_dataframe(q_bt)
print(df_bt.to_string(index=False))
print("""
Gate: FINAL_ABS_ERROR_PP <= 5pp per segment on VALIDATION = BOARD READY
Expected Iteration 9: Core 0.17pp, Strategic 0.52pp, Growth 2.13pp, Emerging 0.80pp, SC 1.15pp""")

# ─────────────────────────────────────────────────────────────────────────────
# RANK QUALITY (AUC proxy) — do high P_CHURN_CAL contracts actually churn more?
# Uses CAL+VALIDATION splits where we have actual outcomes
# ─────────────────────────────────────────────────────────────────────────────
q_rank = f"""
WITH latest AS (
    SELECT RUN_ID FROM {PREDS}
    GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
),
risk_buckets AS (
    SELECT
        p.F_SEGMENT,
        p.SPLIT,
        CASE
            WHEN p.P_CHURN_CAL >= 0.75 THEN 'HIGH (top 25%)'
            WHEN p.P_CHURN_CAL >= 0.50 THEN 'MED-HIGH (50-75%)'
            WHEN p.P_CHURN_CAL >= 0.25 THEN 'MED-LOW (25-50%)'
            ELSE 'LOW (bot 25%)'
        END AS RISK_BUCKET,
        p.TARGET__IS_CHURN,
        p.ATR
    FROM {PREDS} p
    JOIN latest l ON l.RUN_ID = p.RUN_ID
    WHERE p.SPLIT IN ('CAL', 'VALIDATION')
      AND p.TARGET__IS_CHURN IS NOT NULL
      AND p.HORIZON = 0
)
SELECT
    F_SEGMENT,
    RISK_BUCKET,
    COUNT(*)                                                  AS N,
    ROUND(SUM(TARGET__IS_CHURN) / COUNT(*) * 100, 1)         AS ACTUAL_CHURN_RATE_PCT,
    ROUND(SUM(ATR) / 1e6, 2)                                 AS ATR_M,
    ROUND(SUM(CASE WHEN TARGET__IS_CHURN = 1 THEN ATR ELSE 0 END) / NULLIF(SUM(ATR), 0) * 100, 1) AS ATR_CHURN_RATE_PCT
FROM risk_buckets
GROUP BY 1, 2
ORDER BY F_SEGMENT, RISK_BUCKET
"""

print(separator)
print("CHECK C — Rank quality: do high P_CHURN_CAL contracts actually churn more?")
print("(CAL+VALIDATION, H=0, split by churn probability quartile)")
df_rank = fetch_dataframe(q_rank)
print(df_rank.to_string(index=False))
print("""
WHAT TO LOOK FOR:
  LOW bucket: low actual churn rate (model correctly identifies safe contracts)
  HIGH bucket: high actual churn rate (model correctly flags risky contracts)
  Monotonic increase LOW→HIGH = model is useful for ranking
  AUC > 0.70 on validation = BOARD GATE PASSED""")

# ─────────────────────────────────────────────────────────────────────────────
# RESIDUAL BIAS CHECK — monthly FINAL vs actual on 2026 VALIDATION months
# These are the five matured 2026 months the user is seeing
# ─────────────────────────────────────────────────────────────────────────────
q_2026 = f"""
WITH latest AS (
    SELECT RUN_ID FROM {PREDS}
    GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
)
SELECT
    DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE     AS MONTH,
    p.F_SEGMENT,
    p.SPLIT,
    COUNT(*)                                        AS N,
    ROUND(SUM(p.ATR) / 1e6, 2)                    AS ATR_M,
    ROUND(SUM(p.TARGET__RENEWED_AMOUNT) / NULLIF(SUM(p.ATR), 0) * 100, 2) AS ACTUAL_RATE,
    ROUND(SUM(p.FINAL_DOLLARS) / NULLIF(SUM(p.ATR), 0) * 100, 2)          AS FINAL_RATE,
    ROUND(SUM(p.E_RENEWAL_RATE * p.ATR) / NULLIF(SUM(p.ATR), 0) * 100, 2) AS RAW_ML_RATE,
    ROUND((SUM(p.FINAL_DOLLARS) - SUM(p.TARGET__RENEWED_AMOUNT)) / NULLIF(SUM(p.ATR), 0) * 100, 2) AS BIAS_PP
FROM {PREDS} p
JOIN latest l ON l.RUN_ID = p.RUN_ID
WHERE p.RENEWAL_MONTH >= '2026-01-01'
  AND p.RENEWAL_MONTH < '2026-06-01'
  AND p.TARGET__RENEWED_AMOUNT IS NOT NULL
  AND p.HORIZON = 0
  AND p.SPLIT IN ('CAL', 'VALIDATION')
GROUP BY 1, 2, 3
ORDER BY 1, 2
"""

print(separator)
print("CHECK D — Jan-May 2026 FINAL vs actual (the months Finance can compare against)")
df_2026 = fetch_dataframe(q_2026)
print(df_2026.to_string(index=False))
print("""
Expected per V5 Iteration 9:
  Jan: +0.4pp  Feb: +0.9pp  Mar: -0.2pp  Apr: 0.0pp  May: +2.0pp MARGINAL
  ALL within the board gate (|bias| <= 5pp)""")

# ─────────────────────────────────────────────────────────────────────────────
# FORWARD COMPOSITION — why are Jun-Dec model predictions 80%?
# Show the BASE_RATE (anchor) vs E_RENEWAL_RATE (raw) vs FINAL (blended)
# for forward SCORE rows, to explain the gap clearly
# ─────────────────────────────────────────────────────────────────────────────
q_fwd = f"""
WITH latest AS (
    SELECT RUN_ID FROM {PREDS}
    GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
)
SELECT
    DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE   AS MONTH,
    p.F_SEGMENT,
    COUNT(*)                                      AS N,
    ROUND(AVG(p.BASE_RATE) * 100, 2)             AS BASE_RATE_PCT,
    ROUND(AVG(p.W_HORIZON), 3)                   AS AVG_W_HORIZON,
    ROUND(SUM(p.E_RENEWAL_RATE * p.ATR) / NULLIF(SUM(p.ATR), 0) * 100, 2) AS RAW_ML_RATE,
    ROUND(SUM(p.FINAL_DOLLARS) / NULLIF(SUM(p.ATR), 0) * 100, 2)          AS FINAL_RATE,
    ROUND(AVG(p.ML_DELTA) * 100, 3)              AS AVG_ML_DELTA_PP,
    ROUND(AVG(p.P_CHURN_CAL) * 100, 2)           AS AVG_CHURN_CAL_PCT
FROM {PREDS} p
JOIN latest l ON l.RUN_ID = p.RUN_ID
WHERE p.RENEWAL_MONTH >= '2026-06-01'
  AND p.RENEWAL_MONTH <= '2026-12-01'
  AND p.SPLIT = 'SCORE'
GROUP BY 1, 2
ORDER BY 1, 2
"""

print(separator)
print("CHECK E — Why does RAW ML show 80%? Base rate vs raw ML vs final (SCORE rows only)")
df_fwd = fetch_dataframe(q_fwd)
print(df_fwd.to_string(index=False))
print("""
ARCHITECTURE EXPLANATION:
  BASE_RATE = historical anchor (70%, ATR-weighted recency halflife)
  E_RENEWAL_RATE (RAW_ML_RATE) = three-outcome model P(full)*1 + P(partial)*rate  ← 80%+
  ML_DELTA = E_RENEWAL_RATE - segment_mean (centered around 0)
  FINAL_RATE = BASE_RATE + W_HORIZON * ML_DELTA ← capped back to ~70%

  The model INTENTIONALLY runs at 80%+ raw because it's predicting P(churn=0)
  on forward contracts that haven't been filtered by 'already churned.' The
  ANCHOR correction brings it to the right level. ML's value is CONTRACT-LEVEL
  RANKING, not setting the portfolio rate.""")

print(separator)
print("ALL CHECKS COMPLETE")
