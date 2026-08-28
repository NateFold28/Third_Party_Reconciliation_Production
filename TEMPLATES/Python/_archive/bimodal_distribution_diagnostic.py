"""
BIMODAL DISTRIBUTION DIAGNOSTIC — Run all 7 audit queries and print results.

Uses connection.py externalbrowser SSO. Will open a browser tab on first run.
Results are printed to console + saved to BIMODAL_DIAGNOSTIC_RESULTS.txt.
"""
import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from connection import fetch_dataframe
import pandas as pd

pd.set_option("display.max_rows", 100)
pd.set_option("display.max_columns", 20)
pd.set_option("display.width", 140)
pd.set_option("display.float_format", "{:.3f}".format)

PREDS = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
FEAT  = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE"

SEP = "\n" + "=" * 90 + "\n"

results = []

def run(label, query):
    print(f"\nRunning: {label} ...", flush=True)
    try:
        df = fetch_dataframe(query)
        output = f"{SEP}{label}\n{SEP}{df.to_string(index=False)}\n"
        print(output)
        results.append(output)
        return df
    except Exception as e:
        msg = f"{SEP}{label}\nERROR: {e}\n"
        print(msg)
        results.append(msg)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Q1. ACTUAL OUTCOME DISTRIBUTION — Proves (or disproves) bimodality
# ─────────────────────────────────────────────────────────────────────────────
Q1 = f"""
WITH actuals AS (
    SELECT
        ATR,
        TARGET__RENEWAL_RATE,
        CASE
            WHEN TARGET__RENEWAL_RATE IS NULL         THEN '0_NULL'
            WHEN TARGET__RENEWAL_RATE = 0             THEN '1_Zero (full churn)'
            WHEN TARGET__RENEWAL_RATE < 0.05          THEN '2_(0, 0.05)'
            WHEN TARGET__RENEWAL_RATE < 0.25          THEN '3_[0.05, 0.25)'
            WHEN TARGET__RENEWAL_RATE < 0.50          THEN '4_[0.25, 0.50)'
            WHEN TARGET__RENEWAL_RATE < 0.75          THEN '5_[0.50, 0.75)'
            WHEN TARGET__RENEWAL_RATE < 0.95          THEN '6_[0.75, 0.95)'
            WHEN TARGET__RENEWAL_RATE < 1.0           THEN '7_[0.95, 1.00)'
            ELSE                                           '8_One (full renew)'
        END AS BUCKET
    FROM {FEAT}
    WHERE COHORT = 'MATURED'
      AND ATR > 0
      AND HORIZON = 0
      AND SPLIT = 'VALIDATION'
)
SELECT
    BUCKET,
    COUNT(*) AS N_CONTRACTS,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS PCT_CONTRACTS,
    ROUND(SUM(ATR) / 1e6, 2) AS ATR_MILLIONS,
    ROUND(SUM(ATR) * 100.0 / SUM(SUM(ATR)) OVER (), 2) AS PCT_ATR
FROM actuals
GROUP BY BUCKET
ORDER BY BUCKET
"""
run("Q1 — Actual Outcome Distribution (VALIDATION cohort, H0)", Q1)


# ─────────────────────────────────────────────────────────────────────────────
# Q2. PREDICTED DISTRIBUTION — Proves compression
# ─────────────────────────────────────────────────────────────────────────────
Q2 = f"""
WITH latest_run AS (
    SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT = 'VALIDATION'
),
preds AS (
    SELECT
        p.ATR,
        p.PRED_RENEW_RATE_FINAL,
        CASE
            WHEN p.PRED_RENEW_RATE_FINAL < 0.05   THEN '1_< 0.05'
            WHEN p.PRED_RENEW_RATE_FINAL < 0.25   THEN '2_[0.05, 0.25)'
            WHEN p.PRED_RENEW_RATE_FINAL < 0.50   THEN '3_[0.25, 0.50)'
            WHEN p.PRED_RENEW_RATE_FINAL < 0.75   THEN '4_[0.50, 0.75)'
            WHEN p.PRED_RENEW_RATE_FINAL < 0.95   THEN '5_[0.75, 0.95)'
            ELSE                                       '6_>= 0.95'
        END AS BUCKET
    FROM {PREDS} p
    JOIN latest_run lr ON p.RUN_ID = lr.RUN_ID
    WHERE p.SPLIT = 'VALIDATION' AND p.HORIZON = 0 AND p.ATR > 0
)
SELECT
    BUCKET,
    COUNT(*) AS N_CONTRACTS,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS PCT_CONTRACTS,
    ROUND(SUM(ATR) / 1e6, 2) AS ATR_MILLIONS,
    ROUND(MIN(PRED_RENEW_RATE_FINAL) * 100, 1) AS MIN_PRED_PCT,
    ROUND(MAX(PRED_RENEW_RATE_FINAL) * 100, 1) AS MAX_PRED_PCT,
    ROUND(AVG(PRED_RENEW_RATE_FINAL) * 100, 1) AS AVG_PRED_PCT
FROM preds
GROUP BY BUCKET
ORDER BY BUCKET
"""
run("Q2 — Predicted Distribution (PRED_RENEW_RATE_FINAL, H0)", Q2)


# ─────────────────────────────────────────────────────────────────────────────
# Q3. CONFUSION MATRIX (predicted bucket × actual outcome)
# ─────────────────────────────────────────────────────────────────────────────
Q3 = f"""
WITH latest_run AS (
    SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT = 'VALIDATION'
),
joined AS (
    SELECT
        CASE
            WHEN p.PRED_RENEW_RATE_FINAL < 0.50  THEN '1_Low (<0.50)'
            WHEN p.PRED_RENEW_RATE_FINAL < 0.75  THEN '2_Mid [0.50-0.75)'
            WHEN p.PRED_RENEW_RATE_FINAL < 0.95  THEN '3_High [0.75-0.95)'
            ELSE                                      '4_Top (>=0.95)'
        END AS PRED_BUCKET,
        CASE
            WHEN f.TARGET__RENEWAL_RATE = 0   THEN '1_FullChurn'
            WHEN f.TARGET__RENEWAL_RATE < 1.0 THEN '2_Partial'
            ELSE                                   '3_FullRenew'
        END AS ACTUAL_BUCKET,
        p.ATR
    FROM {PREDS} p
    JOIN latest_run lr ON p.RUN_ID = lr.RUN_ID
    JOIN {FEAT} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
        AND p.SPLIT           = f.SPLIT
    WHERE p.SPLIT = 'VALIDATION' AND p.HORIZON = 0 AND f.COHORT = 'MATURED'
      AND p.ATR > 0 AND f.TARGET__RENEWAL_RATE IS NOT NULL
)
SELECT
    PRED_BUCKET,
    ACTUAL_BUCKET,
    COUNT(*) AS N,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY PRED_BUCKET), 1) AS ROW_PCT,
    ROUND(SUM(ATR) / 1e6, 1) AS ATR_M
FROM joined
GROUP BY PRED_BUCKET, ACTUAL_BUCKET
ORDER BY PRED_BUCKET, ACTUAL_BUCKET
"""
run("Q3 — Confusion Matrix (pred bucket × actual outcome)", Q3)


# ─────────────────────────────────────────────────────────────────────────────
# Q4. PER-CONTRACT MAE — The hidden number
# ─────────────────────────────────────────────────────────────────────────────
Q4 = f"""
WITH latest_run AS (
    SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT = 'VALIDATION'
),
errors AS (
    SELECT
        p.ATR,
        p.PRED_RENEW_RATE_FINAL        AS PRED_RATE,
        f.TARGET__RENEWAL_RATE         AS ACTUAL_RATE,
        ABS(p.PRED_RENEW_RATE_FINAL - f.TARGET__RENEWAL_RATE) AS ABS_ERR
    FROM {PREDS} p
    JOIN latest_run lr ON p.RUN_ID = lr.RUN_ID
    JOIN {FEAT} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
        AND p.SPLIT           = f.SPLIT
    WHERE p.SPLIT = 'VALIDATION' AND p.HORIZON = 0 AND f.COHORT = 'MATURED'
      AND p.ATR > 0 AND f.TARGET__RENEWAL_RATE IS NOT NULL
)
SELECT
    COUNT(*)                                                          AS N_CONTRACTS,
    ROUND(AVG(ABS_ERR) * 100, 2)                                    AS MAE_PP,
    ROUND(SUM(ABS_ERR * ATR) / SUM(ATR) * 100, 2)                   AS WEIGHTED_MAE_PP,
    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY ABS_ERR)*100, 2) AS MEDIAN_ABS_ERR_PP,
    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY ABS_ERR)*100, 2) AS P75_ABS_ERR_PP,
    ROUND(PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY ABS_ERR)*100, 2) AS P90_ABS_ERR_PP,
    ROUND(AVG(PRED_RATE) * 100, 2)                                  AS AVG_PRED_PCT,
    ROUND(AVG(ACTUAL_RATE) * 100, 2)                                AS AVG_ACTUAL_PCT,
    ROUND((AVG(PRED_RATE) - AVG(ACTUAL_RATE)) * 100, 2)             AS AGGREGATE_BIAS_PP
FROM errors
"""
run("Q4 — Per-Contract MAE vs Aggregate Bias (the hidden number)", Q4)


# ─────────────────────────────────────────────────────────────────────────────
# Q5. RAW CLASSIFIER PROBABILITIES vs ACTUAL OUTCOME — Is the classifier sound?
# ─────────────────────────────────────────────────────────────────────────────
Q5 = f"""
WITH latest_run AS (
    SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT = 'VALIDATION'
),
joined AS (
    SELECT
        p.P_LOGO_CHURN,
        p.P_DOLLAR_CHURN,
        p.P_FULL_RENEWAL,
        p.ATR,
        CASE
            WHEN f.TARGET__RENEWAL_RATE = 0   THEN '1_LOGO_CHURN'
            WHEN f.TARGET__RENEWAL_RATE < 1.0 THEN '2_PARTIAL'
            ELSE                                   '3_FULL_RENEW'
        END AS ACTUAL_OUTCOME
    FROM {PREDS} p
    JOIN latest_run lr ON p.RUN_ID = lr.RUN_ID
    JOIN {FEAT} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
        AND p.SPLIT           = f.SPLIT
    WHERE p.SPLIT = 'VALIDATION' AND p.HORIZON = 0 AND f.COHORT = 'MATURED'
      AND p.ATR > 0 AND f.TARGET__RENEWAL_RATE IS NOT NULL
)
SELECT
    ACTUAL_OUTCOME,
    COUNT(*) AS N,
    ROUND(AVG(P_LOGO_CHURN) * 100, 1)   AS AVG_P_LOGO_PCT,
    ROUND(AVG(P_DOLLAR_CHURN) * 100, 1) AS AVG_P_PARTIAL_PCT,
    ROUND(AVG(P_FULL_RENEWAL) * 100, 1) AS AVG_P_FULL_PCT
FROM joined
GROUP BY ACTUAL_OUTCOME
ORDER BY ACTUAL_OUTCOME
"""
run("Q5 — Raw Classifier Probabilities vs Actual Outcome (classifier sanity check)", Q5)


# ─────────────────────────────────────────────────────────────────────────────
# Q6. COMPRESSION DIAGNOSTIC — E_RENEWAL_RATE stddev vs FINAL_RATE stddev
# ─────────────────────────────────────────────────────────────────────────────
Q6 = f"""
WITH latest_run AS (
    SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT = 'VALIDATION'
)
SELECT
    p.SEGMENT,
    COUNT(*) AS N,
    ROUND(STDDEV(p.E_RENEWAL_RATE) * 100, 2)       AS RAW_STDDEV_PP,
    ROUND(STDDEV(p.PRED_RENEW_RATE_FINAL) * 100, 2) AS FINAL_STDDEV_PP,
    ROUND(STDDEV(p.PRED_RENEW_RATE_FINAL) /
          NULLIF(STDDEV(p.E_RENEWAL_RATE), 0), 3)  AS COMPRESSION_RATIO,
    ROUND(PERCENTILE_CONT(0.10) WITHIN GROUP
          (ORDER BY p.E_RENEWAL_RATE) * 100, 1)    AS RAW_P10,
    ROUND(PERCENTILE_CONT(0.90) WITHIN GROUP
          (ORDER BY p.E_RENEWAL_RATE) * 100, 1)    AS RAW_P90,
    ROUND(PERCENTILE_CONT(0.10) WITHIN GROUP
          (ORDER BY p.PRED_RENEW_RATE_FINAL) * 100, 1) AS FINAL_P10,
    ROUND(PERCENTILE_CONT(0.90) WITHIN GROUP
          (ORDER BY p.PRED_RENEW_RATE_FINAL) * 100, 1) AS FINAL_P90
FROM {PREDS} p
JOIN latest_run lr ON p.RUN_ID = lr.RUN_ID
WHERE p.SPLIT = 'VALIDATION' AND p.HORIZON = 0 AND p.ATR > 0
GROUP BY p.SEGMENT
ORDER BY p.SEGMENT
"""
run("Q6 — Compression Diagnostic (anchor blend destroys how much signal?)", Q6)


# ─────────────────────────────────────────────────────────────────────────────
# Q7. DECILE LIFT — Does ranking still work even if scalar is compressed?
# ─────────────────────────────────────────────────────────────────────────────
Q7 = f"""
WITH latest_run AS (
    SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT = 'VALIDATION'
),
ranked AS (
    SELECT
        p.ATR,
        p.PRED_RENEW_RATE_FINAL,
        f.TARGET__RENEWAL_RATE,
        NTILE(5) OVER (ORDER BY p.PRED_RENEW_RATE_FINAL) AS RATE_QUINTILE
    FROM {PREDS} p
    JOIN latest_run lr ON p.RUN_ID = lr.RUN_ID
    JOIN {FEAT} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
        AND p.SPLIT           = f.SPLIT
    WHERE p.SPLIT = 'VALIDATION' AND p.HORIZON = 0 AND f.COHORT = 'MATURED'
      AND p.ATR > 0 AND f.TARGET__RENEWAL_RATE IS NOT NULL
)
SELECT
    RATE_QUINTILE,
    COUNT(*) AS N,
    ROUND(AVG(PRED_RENEW_RATE_FINAL) * 100, 1)                                AS AVG_PRED_PCT,
    ROUND(SUM(TARGET__RENEWAL_RATE * ATR) / NULLIF(SUM(ATR), 0) * 100, 1)     AS AVG_ACTUAL_PCT_WTD,
    ROUND(SUM(ATR) / 1e6, 1)                                                  AS ATR_M,
    ROUND(SUM(TARGET__RENEWAL_RATE * ATR) / NULLIF(SUM(ATR), 0) * 100
          - AVG(PRED_RENEW_RATE_FINAL) * 100, 1)                              AS BIAS_PP
FROM ranked
GROUP BY RATE_QUINTILE
ORDER BY RATE_QUINTILE
"""
run("Q7 — Quintile Lift (does ranking work even if scalars are compressed?)", Q7)


# ─────────────────────────────────────────────────────────────────────────────
# SAVE RESULTS
# ─────────────────────────────────────────────────────────────────────────────
out_path = _HERE / "BIMODAL_DIAGNOSTIC_RESULTS.txt"
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(results))
print(f"\n{'='*90}")
print(f"Results saved to: {out_path}")
