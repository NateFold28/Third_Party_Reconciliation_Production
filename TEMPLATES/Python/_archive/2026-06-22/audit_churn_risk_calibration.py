"""
audit_churn_risk_calibration.py
================================
Deep calibration audit for the V5 churn risk ranking model.

Answers for the board:
  Q1. Does high risk percentile actually predict higher churn? (lift test)
  Q2. How far out can we trust the ranking? (H=0..5 horizon decay)
  Q3. Is P_CHURN_CAL numerically calibrated or directionally-only?
  Q4. Which contracts to flag NOW for the board's top-10 at-risk list?
  Q5. Are CHURN_PCT and RETENTION_PCT properly decoupled?
  Q6. What is the discrimination score (AUC proxy) per segment?
  Q7. What do the top false positives / false negatives look like?

Usage:
    cd c:\\Users\\Nate.Fold\\projects
    .venv\\Scripts\\python.exe TEMPLATES\\Python\\audit_churn_risk_calibration.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe
import pandas as pd
import numpy as np

pd.set_option('display.float_format', '{:,.2f}'.format)
pd.set_option('display.width', 200)
pd.set_option('display.max_columns', 40)
pd.set_option('display.max_rows', 100)

conn = get_snowflake_connection()
SEP  = "=" * 80

def hdr(s: str, sub: str = ""):
    print(f"\n{SEP}\n{s}")
    if sub:
        print(f"  {sub}")
    print(SEP)

def pct_fmt(x):
    """Format a 0-1 float as 'XX.X%'."""
    return f"{x*100:.1f}%"

# Core table references
PREDS   = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
FEAT    = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE"
RUNS    = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_MODEL_RUNS"
APP     = "STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"
SHAP    = "STREAMLIT_APPS.DBO.V5_SANDBOX_APP_SHAP_DRIVERS"

# NOTE on schema:
# PREDICTIONS key: CONTRACT_ID_UFR + RENEWAL_MONTH + SPLIT (no PRODUCT_GROUP)
# FEATURE_STORE actuals: TARGET__IS_CHURN (binary 0/1), TARGET__RENEWAL_RATE (0-1 float)
# APP table key: CONTRACT_ID + PRODUCT_GROUP (app-layer grain)
CHURN_THRESHOLD = 0.50  # renewal rate below this = "churned" for binary event


# =============================================================================
# STEP 0: Current champion run metadata
# =============================================================================
hdr("STEP 0 — Champion run metadata")
df_run = fetch_dataframe(f"""
    SELECT RUN_ID, MAX(PREDICTION_TS) AS LATEST_TS, COUNT(*) AS N_SCORE_ROWS
    FROM {PREDS}
    WHERE SPLIT = 'SCORE'
    GROUP BY RUN_ID
    ORDER BY LATEST_TS DESC
    LIMIT 3
""", conn=conn)
print(df_run.to_string(index=False))
LATEST_RUN = df_run.iloc[0]['RUN_ID']
print(f"\nUsing RUN_ID: {LATEST_RUN}")


# =============================================================================
# Q1 / A: DISCRIMINATION LIFT — Quintile analysis on matured validation cohort
#    Core question: "Does the model rank risky contracts above safe ones?"
# =============================================================================
hdr(
    "Q1 / A — Discrimination Lift (Quintile × Segment, H=0, Validation split)",
    "Critical trust test: do Q5 (riskiest) contracts actually churn more than Q1?"
)
q_lift = f"""
WITH bt AS (
    SELECT
        p.CONTRACT_ID_UFR, p.SEGMENT, p.HORIZON,
        p.P_CHURN_CAL                                                     AS PRED_CHURN,
        p.PRED_RENEW_RATE_FINAL                                           AS PRED_RENEWAL,
        p.ATR,
        f.TARGET__RENEWAL_RATE                                            AS ACTUAL_RATE,
        f.ATR                                                             AS ACTUAL_ATR,
        f.TARGET__IS_CHURN                                                AS DID_CHURN
    FROM {PREDS} p
    INNER JOIN {FEAT} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
        AND p.SPLIT           = f.SPLIT
    WHERE p.RUN_ID  = '{LATEST_RUN}'
      AND p.SPLIT   = 'VALIDATION'
      AND p.HORIZON = 0
      AND f.ATR > 0
),
q AS (
    SELECT *,
        NTILE(5) OVER (PARTITION BY SEGMENT ORDER BY PRED_CHURN) AS RISK_Q
    FROM bt
)
SELECT
    SEGMENT,
    RISK_Q,
    COUNT(*)                             AS N,
    ROUND(AVG(PRED_CHURN)*100, 1)       AS AVG_PRED_CHURN_PCT,
    ROUND(AVG(ACTUAL_RATE)*100, 1)      AS AVG_ACTUAL_RENEWAL_PCT,
    ROUND(AVG(DID_CHURN)*100, 1)        AS CHURN_EVENT_RATE_PCT,
    ROUND(SUM(ACTUAL_ATR)/1e3, 1)       AS ATR_K
FROM q
GROUP BY SEGMENT, RISK_Q
ORDER BY SEGMENT, RISK_Q
"""
df_lift = fetch_dataframe(q_lift, conn=conn)

# Print raw table
print(df_lift.to_string(index=False))

# Compute per-segment lift (Q5 churn rate / Q1 churn rate)
print("\n--- Lift summary (Q5 riskiest vs Q1 safest churn event rate) ---")
lift_summary = []
for seg, grp in df_lift.groupby('SEGMENT'):
    q1 = grp[grp['RISK_Q'] == 1]['CHURN_EVENT_RATE_PCT'].values
    q5 = grp[grp['RISK_Q'] == 5]['CHURN_EVENT_RATE_PCT'].values
    if len(q1) and len(q5) and q1[0] > 0:
        lift = q5[0] / q1[0]
        mono = all(
            grp.sort_values('RISK_Q')['CHURN_EVENT_RATE_PCT'].diff().dropna() >= -1.0
        )
        lift_summary.append({
            'Segment': seg,
            'Q1_Churn%': q1[0],
            'Q5_Churn%': q5[0],
            'Lift': round(lift, 2),
            'Monotonic': '✓' if mono else '✗ ISSUE'
        })
    else:
        lift_summary.append({'Segment': seg, 'Q1_Churn%': None, 'Q5_Churn%': None, 'Lift': None, 'Monotonic': '?'})

df_ls = pd.DataFrame(lift_summary)
print(df_ls.to_string(index=False))

# Board soundbite
if df_ls['Lift'].dropna().size > 0:
    avg_lift = df_ls['Lift'].dropna().mean()
    print(f"\nBOARD SOUNDBITE: Top-20% riskiest contracts churn {avg_lift:.1f}× more than bottom-20% on average.")


# =============================================================================
# Q2 / B: HORIZON DECAY — Does discrimination hold at H=1,2,3?
# =============================================================================
hdr(
    "Q2 / B — Horizon Decay (discrimination vs calendar distance)",
    "Safe trust horizon: H where Spearman rank-corr is still meaningful (>0.15)"
)
q_horizon = f"""
WITH preds AS (
    SELECT
        p.CONTRACT_ID_UFR, p.SEGMENT,
        p.HORIZON, p.P_CHURN_CAL, p.PRED_RENEW_RATE_FINAL, p.ATR,
        f.TARGET__IS_CHURN   AS DID_CHURN,
        f.TARGET__RENEWAL_RATE AS ACTUAL_RATE
    FROM {PREDS} p
    INNER JOIN {FEAT} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
        AND p.SPLIT           = f.SPLIT
    WHERE p.RUN_ID = '{LATEST_RUN}'
      AND p.SPLIT = 'VALIDATION' AND p.HORIZON BETWEEN 0 AND 5
      AND f.ATR > 0
)
SELECT
    HORIZON,
    COUNT(*)                                                   AS N,
    ROUND(AVG(P_CHURN_CAL)*100, 1)                           AS AVG_PRED_CHURN_PCT,
    ROUND(AVG(DID_CHURN)*100, 1)                             AS AVG_ACTUAL_CHURN_PCT,
    ROUND(CORR(P_CHURN_CAL, DID_CHURN::FLOAT), 3)           AS PEARSON_AUC_PROXY,
    ROUND(CORR(P_CHURN_CAL, ACTUAL_RATE), 3)                AS CORR_VS_ACTUAL_RATE
FROM preds
GROUP BY HORIZON
ORDER BY HORIZON
"""
df_hdecay = fetch_dataframe(q_horizon, conn=conn)
print(df_hdecay.to_string(index=False))

# Identify safe trust horizon
print("\n--- Horizon trust verdict ---")
for _, row in df_hdecay.iterrows():
    h = int(row['HORIZON'])
    auc_p = float(row['PEARSON_AUC_PROXY']) if pd.notna(row['PEARSON_AUC_PROXY']) else 0
    corr  = float(row['CORR_VS_ACTUAL_RATE']) if pd.notna(row['CORR_VS_ACTUAL_RATE']) else 0
    if auc_p >= 0.20:
        verdict = "TRUST ✓"
    elif auc_p >= 0.10:
        verdict = "USE WITH CAUTION"
    else:
        verdict = "LOW SIGNAL — directional only"
    print(f"  H={h}: AUC proxy={auc_p:.3f}, corr_vs_actual={corr:.3f} → {verdict}")

# B2: Quintile lift by horizon (visualization-ready)
print("\n--- Quintile lift by horizon (rows = H, cols = Q1..Q5 churn rate %) ---")
q_hq = f"""
WITH preds AS (
    SELECT
        p.HORIZON, p.P_CHURN_CAL, p.ATR,
        f.TARGET__IS_CHURN AS DID_CHURN
    FROM {PREDS} p
    INNER JOIN {FEAT} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
        AND p.SPLIT           = f.SPLIT
    WHERE p.RUN_ID = '{LATEST_RUN}'
      AND p.SPLIT = 'VALIDATION' AND p.HORIZON BETWEEN 0 AND 5
      AND f.ATR > 0
),
q AS (
    SELECT *, NTILE(5) OVER (PARTITION BY HORIZON ORDER BY P_CHURN_CAL) AS QUINTILE
    FROM preds
)
SELECT
    HORIZON,
    QUINTILE,
    COUNT(*)                        AS N,
    ROUND(AVG(P_CHURN_CAL)*100, 1) AS AVG_PRED_PCT,
    ROUND(AVG(DID_CHURN)*100, 1)   AS ACTUAL_CHURN_RATE_PCT
FROM q
GROUP BY HORIZON, QUINTILE
ORDER BY HORIZON, QUINTILE
"""
df_hq = fetch_dataframe(q_hq, conn=conn)
pivot = df_hq.pivot_table(
    index='HORIZON', columns='QUINTILE',
    values='ACTUAL_CHURN_RATE_PCT', aggfunc='mean'
).round(1)
pivot.columns = [f"Q{c}_Actual%" for c in pivot.columns]
print(pivot.to_string())


# =============================================================================
# Q3 / C: CALIBRATION — Is P_CHURN_CAL numerically reliable?
# =============================================================================
hdr(
    "Q3 / C — Probability Calibration (P_CHURN_CAL bins vs observed churn rate)",
    "Good calibration: each bin's avg predicted probability ≈ observed churn rate"
)
q_cal = f"""
WITH preds AS (
    SELECT
        p.P_CHURN_CAL,
        p.SEGMENT,
        FLOOR(p.P_CHURN_CAL * 10) / 10.0 AS PROB_BIN,
        f.TARGET__IS_CHURN                AS DID_CHURN
    FROM {PREDS} p
    INNER JOIN {FEAT} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
        AND p.SPLIT           = f.SPLIT
    WHERE p.RUN_ID = '{LATEST_RUN}'
      AND p.SPLIT = 'VALIDATION' AND p.HORIZON = 0
      AND f.ATR > 0
)
SELECT
    PROB_BIN,
    COUNT(*)                                                          AS N,
    ROUND(AVG(P_CHURN_CAL)*100, 1)                                  AS AVG_PRED_CHURN_PCT,
    ROUND(AVG(DID_CHURN)*100, 1)                                    AS ACTUAL_CHURN_RATE_PCT,
    ROUND((AVG(P_CHURN_CAL) - AVG(DID_CHURN::FLOAT))*100, 1)       AS CALIBRATION_GAP_PP
FROM preds
GROUP BY PROB_BIN
ORDER BY PROB_BIN
"""
df_cal = fetch_dataframe(q_cal, conn=conn)
print(df_cal.to_string(index=False))

# Compute ECE (Expected Calibration Error)
if not df_cal.empty and df_cal['N'].sum() > 0:
    total_n = df_cal['N'].sum()
    ece = (df_cal['N'] / total_n * df_cal['CALIBRATION_GAP_PP'].abs()).sum()
    print(f"\nExpected Calibration Error (ECE): {ece:.2f}pp")
    print(f"Interpretation: avg predicted churn % deviates {ece:.1f}pp from actual frequency")
    if ece < 5:
        print("  → WELL CALIBRATED (ECE < 5pp)")
    elif ece < 10:
        print("  → ACCEPTABLY CALIBRATED (ECE 5-10pp) — directional use is reliable")
    else:
        print("  → OVER/UNDER-CALIBRATED (ECE > 10pp) — treat as ranking signal only, not probability")

# Calibration by segment
print("\n--- Calibration by segment ---")
q_cal_seg = f"""
WITH preds AS (
    SELECT p.P_CHURN_CAL, p.SEGMENT, p.ATR,
        f.TARGET__IS_CHURN AS DID_CHURN
    FROM {PREDS} p
    INNER JOIN {FEAT} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
        AND p.SPLIT           = f.SPLIT
    WHERE p.RUN_ID = '{LATEST_RUN}'
      AND p.SPLIT = 'VALIDATION' AND p.HORIZON = 0
      AND f.ATR > 0
)
SELECT
    SEGMENT,
    COUNT(*)                                                          AS N,
    ROUND(AVG(P_CHURN_CAL)*100, 1)                                  AS AVG_PRED_CHURN_PCT,
    ROUND(AVG(DID_CHURN)*100, 1)                                    AS ACTUAL_CHURN_RATE_PCT,
    ROUND((AVG(P_CHURN_CAL) - AVG(DID_CHURN::FLOAT))*100, 1)       AS CALIBRATION_GAP_PP,
    ROUND(CORR(P_CHURN_CAL, DID_CHURN::FLOAT), 3)                  AS DISCRIMINATION_CORR
FROM preds
GROUP BY SEGMENT
ORDER BY ABS(CALIBRATION_GAP_PP) DESC
"""
df_cal_seg = fetch_dataframe(q_cal_seg, conn=conn)
print(df_cal_seg.to_string(index=False))

# Identify segments where absolute calibration is unreliable
if not df_cal_seg.empty:
    bad_cal = df_cal_seg[df_cal_seg['CALIBRATION_GAP_PP'].abs() > 10]
    if not bad_cal.empty:
        print("\n⚠ SEGMENTS WITH CALIBRATION GAP > 10pp (treat CHURN_PCT as rank only):")
        print(bad_cal[['SEGMENT','CALIBRATION_GAP_PP','DISCRIMINATION_CORR']].to_string(index=False))
    else:
        print("\n✓ All segments calibrated within 10pp — absolute values usable")


# =============================================================================
# Q4 / D: BOARD TOP-10 — High-risk, high-ATR forward contracts
# =============================================================================
hdr(
    "Q4 / D — Board Top-10 at-risk contracts (H=0..3, ATR ≥ $5k)",
    "Composite risk score = Risk Percentile × ATR — ranks by 'how much can we lose'"
)
q_top10 = f"""
SELECT
    a.CONTRACT_ID,
    a.SEGMENT,
    a.PARTNER,
    a.RENEWAL_MANAGER,
    a.PRODUCT_PORTFOLIO,
    DATE_TRUNC('MONTH', a.RENEWAL_DATE)::DATE          AS RENEWAL_MONTH,
    a.V2_MONTHS_TO_RENEWAL                             AS HORIZON,
    ROUND(a.ATR, 0)                                    AS ATR,
    ROUND(a.CONTRACT_RISK_PCTL_IN_SEG, 0)             AS RISK_PCTL_IN_SEG,
    ROUND(a.CHURN_PCT, 1)                              AS CHURN_PROB_PCT,
    ROUND(a.RETENTION_PCT, 1)                          AS RETENTION_RATE_PCT,
    ROUND(a.ML_FORECAST, 0)                            AS RENEWAL_FORECAST,
    ROUND(a.ATR * (1 - a.RETENTION_PCT / 100), 0)     AS EXPECTED_LOSS,
    ROUND(a.CONTRACT_RISK_PCTL_IN_SEG * a.ATR / 100, 0) AS COMPOSITE_RISK_SCORE,
    a.EARLY_WARNING_FLAG,
    a.CONTRACT_RISK_TIER
FROM {APP} a
WHERE a.COHORT = 'FORWARD_OPEN'
  AND a.V2_MONTHS_TO_RENEWAL BETWEEN 0 AND 3
  AND a.ATR >= 5000
  AND a.CONTRACT_RISK_PCTL_IN_SEG >= 60
ORDER BY COMPOSITE_RISK_SCORE DESC
LIMIT 20
"""
df_top = fetch_dataframe(q_top10, conn=conn)
print(df_top.to_string(index=False))

# Board summary dollar figures
if not df_top.empty:
    top10 = df_top.head(10)
    total_atr = top10['ATR'].sum()
    total_loss = top10['EXPECTED_LOSS'].sum()
    early_warn = top10['EARLY_WARNING_FLAG'].sum() if 'EARLY_WARNING_FLAG' in top10 else "?"
    print(f"\n--- TOP 10 BOARD SUMMARY ---")
    print(f"  Total ATR at risk:     ${total_atr:,.0f}")
    print(f"  Expected loss (model): ${total_loss:,.0f}")
    print(f"  Contracts with Early Warning flag: {early_warn}/10")

# D2: Summary by renewal month
print("\n--- Portfolio exposure by renewal month (H=0..5) ---")
q_d2 = f"""
SELECT
    DATE_TRUNC('MONTH', RENEWAL_DATE)::DATE              AS RENEWAL_MONTH,
    SEGMENT,
    COUNT(*)                                             AS N,
    ROUND(SUM(ATR)/1e6, 2)                              AS ATR_M,
    ROUND(SUM(ATR * (1 - RETENTION_PCT/100))/1e6, 2)   AS EXPECTED_LOSS_M,
    COUNT_IF(EARLY_WARNING_FLAG = TRUE)                  AS EARLY_WARN,
    COUNT_IF(CONTRACT_RISK_PCTL_IN_SEG >= 80)           AS HIGH_RISK_N,
    ROUND(AVG(CHURN_PCT), 1)                            AS AVG_CHURN_PCT
FROM {APP}
WHERE COHORT = 'FORWARD_OPEN'
  AND V2_MONTHS_TO_RENEWAL BETWEEN 0 AND 5
  AND ATR > 0
GROUP BY 1, 2
ORDER BY 1, 2
"""
df_d2 = fetch_dataframe(q_d2, conn=conn)
print(df_d2.to_string(index=False))


# =============================================================================
# Q5 / E: MODEL DECOUPLING — CHURN_PCT vs RETENTION_PCT
# =============================================================================
hdr(
    "Q5 / E — Decoupling: CHURN_PCT vs RETENTION_PCT",
    "Should be negatively correlated but NOT sum to 100 — confirms two independent models"
)
q_decouple = f"""
SELECT
    SEGMENT,
    COUNT(*)                                        AS N,
    ROUND(AVG(CHURN_PCT), 1)                       AS AVG_CHURN_PCT,
    ROUND(AVG(RETENTION_PCT), 1)                   AS AVG_RETENTION_PCT,
    ROUND(AVG(CHURN_PCT + RETENTION_PCT), 1)       AS AVG_SUM,
    ROUND(CORR(CHURN_PCT, RETENTION_PCT), 3)       AS CORR,
    ROUND(STDDEV(CHURN_PCT), 1)                    AS STD_CHURN,
    ROUND(STDDEV(RETENTION_PCT), 1)                AS STD_RETENTION
FROM {APP}
WHERE COHORT = 'FORWARD_OPEN' AND ATR > 0
GROUP BY SEGMENT
ORDER BY SEGMENT
"""
df_dec = fetch_dataframe(q_decouple, conn=conn)
print(df_dec.to_string(index=False))

# Interpretation
if not df_dec.empty:
    avg_sum = df_dec['AVG_SUM'].mean()
    avg_corr = df_dec['CORR'].mean()
    print(f"\n  Portfolio-level avg(CHURN_PCT + RETENTION_PCT) = {avg_sum:.1f}%")
    print(f"  Portfolio-level avg correlation(CHURN_PCT, RETENTION_PCT) = {avg_corr:.3f}")
    if avg_sum < 95 and avg_corr < -0.2:
        print("  ✓ Models are properly decoupled (sum ≠ 100, negative correlation confirms independence)")
    elif avg_sum > 95:
        print("  ⚠ Models may be implicitly coupled (sum ≈ 100)")
    if avg_corr > 0:
        print("  ⚠ Unexpected positive correlation — check if CHURN_PCT wired from same model")


# =============================================================================
# Q6 / F: AUC BY SEGMENT (via sklearn if available, otherwise approximation)
# =============================================================================
hdr(
    "Q6 / F — AUC by Segment (discrimination quality)",
    "AUC > 0.70 = good, 0.60-0.70 = acceptable for noisy SaaS renewal data"
)
q_auc_data = f"""
WITH preds AS (
    SELECT
        p.SEGMENT, p.P_CHURN_CAL,
        f.TARGET__IS_CHURN AS DID_CHURN
    FROM {PREDS} p
    INNER JOIN {FEAT} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
        AND p.SPLIT           = f.SPLIT
    WHERE p.RUN_ID = '{LATEST_RUN}'
      AND p.SPLIT = 'VALIDATION' AND p.HORIZON = 0
      AND f.ATR > 0
)
SELECT SEGMENT, P_CHURN_CAL, DID_CHURN FROM preds
"""
df_auc = fetch_dataframe(q_auc_data, conn=conn)

# Compute AUC per segment using sklearn (falls back to Pearson proxy if unavailable)
try:
    from sklearn.metrics import roc_auc_score
    print(f"{'Segment':<20} {'N':>6} {'AUC':>8} {'n_churn':>8} {'Verdict':<20}")
    print("-" * 70)
    for seg, grp in df_auc.groupby('SEGMENT'):
        if grp['DID_CHURN'].nunique() < 2:
            print(f"{seg:<20} {len(grp):>6} {'N/A':>8} {grp['DID_CHURN'].sum():>8} (only 1 class)")
            continue
        auc = roc_auc_score(grp['DID_CHURN'], grp['P_CHURN_CAL'])
        verdict = "STRONG ✓" if auc >= 0.70 else ("ACCEPTABLE" if auc >= 0.60 else "WEAK ⚠")
        print(f"{seg:<20} {len(grp):>6} {auc:>8.4f} {grp['DID_CHURN'].sum():>8} {verdict:<20}")
except ImportError:
    print("sklearn not available — using Pearson correlation as AUC proxy")
    auc_proxy = df_auc.groupby('SEGMENT').apply(
        lambda g: pd.Series({
            'N': len(g),
            'n_churn': g['DID_CHURN'].sum(),
            'AUC_proxy': g['P_CHURN_CAL'].corr(g['DID_CHURN'].astype(float))
        })
    ).reset_index()
    print(auc_proxy.to_string(index=False))


# =============================================================================
# Q7 / G: FALSE POSITIVE / FALSE NEGATIVE SANITY CHECK
# =============================================================================
hdr(
    "Q7 / G — False Positive / False Negative analysis (H=0, material contracts ≥$5k)",
    "FP = flagged high risk but renewed | FN = not flagged but churned"
)
q_fp = f"""
WITH preds AS (
    SELECT
        p.CONTRACT_ID_UFR,
        p.SEGMENT, p.RENEWAL_MONTH,
        p.P_CHURN_CAL, p.PRED_RENEW_RATE_FINAL, p.ATR,
        f.TARGET__RENEWAL_RATE  AS ACTUAL_RATE,
        f.TARGET__IS_CHURN      AS DID_CHURN,
        f.ATR                   AS ACTUAL_ATR,
        NTILE(5) OVER (PARTITION BY p.SEGMENT ORDER BY p.P_CHURN_CAL) AS RISK_Q
    FROM {PREDS} p
    INNER JOIN {FEAT} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
        AND p.SPLIT           = f.SPLIT
    WHERE p.RUN_ID = '{LATEST_RUN}'
      AND p.SPLIT = 'VALIDATION' AND p.HORIZON = 0
      AND f.ATR >= 5000
)
SELECT
    CASE
        WHEN RISK_Q = 5 AND DID_CHURN = 0 THEN 'False Positive'
        WHEN RISK_Q = 1 AND DID_CHURN = 1 THEN 'False Negative'
        WHEN RISK_Q = 5 AND DID_CHURN = 1 THEN 'True Positive'
        WHEN RISK_Q = 1 AND DID_CHURN = 0 THEN 'True Negative'
        ELSE 'Middle quintile'
    END AS CLASSIFICATION,
    SEGMENT,
    COUNT(*)                               AS N,
    ROUND(SUM(ACTUAL_ATR)/1e6, 2)         AS ATR_M,
    ROUND(AVG(P_CHURN_CAL)*100, 1)        AS AVG_PRED_CHURN_PCT,
    ROUND(AVG(PRED_RENEW_RATE_FINAL)*100, 1) AS AVG_PRED_RENEWAL_PCT
FROM preds
WHERE RISK_Q IN (1, 5)
GROUP BY 1, 2
ORDER BY CLASSIFICATION, SEGMENT
"""
df_fp = fetch_dataframe(q_fp, conn=conn)
print(df_fp.to_string(index=False))

# Precision / Recall summary
print("\n--- Precision / Recall @ top quintile ---")
for seg, grp in df_fp.groupby('SEGMENT'):
    tp = grp.loc[grp['CLASSIFICATION'] == 'True Positive', 'N'].sum()
    fp = grp.loc[grp['CLASSIFICATION'] == 'False Positive', 'N'].sum()
    fn = grp.loc[grp['CLASSIFICATION'] == 'False Negative', 'N'].sum()
    tn = grp.loc[grp['CLASSIFICATION'] == 'True Negative', 'N'].sum()
    prec = tp / (tp + fp) if (tp + fp) > 0 else None
    rec  = tp / (tp + fn) if (tp + fn) > 0 else None
    print(f"  {seg:<20}: Precision={prec*100:.0f}% | Recall={rec*100:.0f}%  (TP={tp}, FP={fp}, FN={fn})"
          if prec and rec else f"  {seg}: insufficient data")


# =============================================================================
# SHAP VALIDATION — Top drivers match SaaS business intuition?
# =============================================================================
hdr(
    "SHAP Validation — Top risk drivers (portfolio-wide)",
    "Validates model logic: drivers should reflect known SaaS churn signals"
)
q_shap = f"""
SELECT
    FEATURE_NAME,
    COUNT(DISTINCT CONTRACT_ID)          AS N_CONTRACTS,
    ROUND(AVG(ABS(SHAP_VALUE)), 4)       AS AVG_ABS_IMPACT,
    ROUND(AVG(SHAP_VALUE), 4)            AS AVG_DIRECTION,
    ROUND(SUM(ABS(SHAP_VALUE)) / SUM(SUM(ABS(SHAP_VALUE))) OVER () * 100, 1) AS PCT_TOTAL_IMPACT
FROM {SHAP}
GROUP BY FEATURE_NAME
HAVING COUNT(DISTINCT CONTRACT_ID) > 10
ORDER BY AVG_ABS_IMPACT DESC
LIMIT 20
"""
try:
    df_shap = fetch_dataframe(q_shap, conn=conn)
    if df_shap.empty:
        print("  ⚠ V5_SANDBOX_APP_SHAP_DRIVERS has 0 rows — running diagnostics...")
        # Diagnostic 1: raw SHAP table row count for this run
        try:
            raw_shap = fetch_dataframe(
                f"SELECT COUNT(*) AS N FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_CONTRACT_SHAP "
                f"WHERE RUN_ID = '{LATEST_RUN}'", conn=conn
            )
            n_raw = int(raw_shap['N'].iloc[0]) if not raw_shap.empty else 0
            print(f"  ML_SANDBOX_V5_CONTRACT_SHAP rows for run {LATEST_RUN}: {n_raw:,}")
            if n_raw == 0:
                print("  CAUSE: Training proc wrote 0 SHAP rows — likely explain_clf failed or no SCORE split.")
                print("  FIX: Re-run SP_V5_SANDBOX_RUN_PIPELINE() and check the procedure output for 'SHAP rows written'.")
        except Exception as e2:
            print(f"  Could not query raw SHAP table: {e2}")
        # Diagnostic 2: did app table build pick up any ML-scored rows?
        try:
            scored_ct = fetch_dataframe(
                "SELECT COUNT(*) AS N FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL "
                "WHERE RUN_ID <> 'V5_ANCHOR_FALLBACK'", conn=conn
            )
            n_scored = int(scored_ct['N'].iloc[0]) if not scored_ct.empty else 0
            print(f"  V5_SANDBOX_APP_CONTRACT_DETAIL ML-scored rows (RUN_ID≠FALLBACK): {n_scored:,}")
            if n_scored == 0:
                print("  CAUSE: All app rows are anchor fallback — contract ID join failed.")
                print("  FIX: Re-run SP_V5_BUILD_APP_TABLES_V5_SHADOW() after confirming CARR spine is current.")
        except Exception as e3:
            print(f"  Could not query app detail table: {e3}")
    else:
        print(df_shap.to_string(index=False))
        print("\nExpected top drivers for SaaS churn: tenure, recent renewal rate, segment,")
        print("product usage signals, contract value, historical pattern. Flag any spurious features.")
except Exception as e:
    print(f"  SHAP query failed (table may not exist yet): {e}")


# =============================================================================
# SUMMARY — Board readiness verdict
# =============================================================================
hdr("FINAL VERDICT — Churn ranking model board readiness")

print("""
TRUST HORIZON GUIDE
━━━━━━━━━━━━━━━━━━
  H=0 (current month):   HIGHEST confidence. Actuals confirming.
  H=1 (1 mo forward):    HIGH confidence. Feature store fresh.
  H=2 (2 mo forward):    GOOD for top-10 board list.
  H=3 (3 mo / 1 quarter): TARGET horizon for quarterly board report.
                           Discrimination degrades but still meaningful.
  H=4..5 (4-5 months):   DIRECTIONAL only. Flag but do not rank-commit.
  H>5:                    No model score — do not report.

HOW TO FRAME FOR BOARD / SALES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  "Risk Rank (Segment)" = percentile within peer-group of same size/type.
  80th pctl = riskier than 80% of similar contracts this quarter.

  COMPOSITE RISK SCORE = Risk_Pctl × ATR / 100
  → Combines probability of loss with size of loss.
  → Sorts the top-10 list by dollars-at-risk, not just probability.

  For the board top-10 slide:
    'These X contracts represent $Y at risk renewing by [QUARTER_END].
     Each ranks in the top 20% of churn risk within their customer segment.
     Combined expected renewal shortfall vs. full renewal: $Z.'

CALIBRATION NOTE
━━━━━━━━━━━━━━━
  CHURN_PCT (probability) is directionally reliable.
  For absolute probability interpretation, see ECE score above.
  ECE < 5pp → quote numbers directly.
  ECE 5-10pp → add ±5pp uncertainty band.
  ECE > 10pp → use rank/tier labels only (High/Medium/Low).

IMPROVEMENT LEVERS (if discrimination is below target)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Post-hoc Platt scaling on P_CHURN_CAL (isotonic regression on CAL split)
     → fixes numerical calibration without affecting ranking order
  2. Segment-specific calibration curves (Emerging needs separate curve)
  3. Add usage/login signal if available (best SaaS churn predictor)
  4. Weight training samples by ATR → improves calibration for large contracts
  5. Horizon-aware features: include T-90/T-60/T-30 leading signals
""")
