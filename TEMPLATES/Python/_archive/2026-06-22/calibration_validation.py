"""
CALIBRATION VALIDATION — P_LOGO_CHURN & P_FULL_RENEWAL

Tests the three things that must be true before we surface raw probabilities:
  (1) Calibration:    if P_LOGO_CHURN=40%, does that bin churn ~40%?
  (2) Discrimination: AUC of P_LOGO_CHURN vs actual logo churn (target > 0.75)
  (3) Time stability: do calibration & AUC hold month-by-month?

If (1) fails (e.g. systematic over/under-prediction), we fit isotonic regression
on top — still a no-retrain fix.
"""
import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import numpy as np
import pandas as pd
from connection import fetch_dataframe

pd.set_option("display.max_rows", 100)
pd.set_option("display.max_columns", 30)
pd.set_option("display.width", 180)
pd.set_option("display.float_format", "{:.3f}".format)

PREDS = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
FEAT  = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE"

SEP = "\n" + "=" * 100 + "\n"
results = []

def emit(label, content):
    block = f"{SEP}{label}{SEP}{content}\n"
    print(block)
    results.append(block)


# ─────────────────────────────────────────────────────────────────────────────
# Pull the joined dataset once — VALIDATION split, MATURED cohort, H0
# ─────────────────────────────────────────────────────────────────────────────
print("Loading joined predictions+labels for VALIDATION/H0/MATURED...", flush=True)
join_q = f"""
WITH latest_run AS (
    SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT = 'VALIDATION'
)
SELECT
    p.CONTRACT_ID_UFR,
    p.RENEWAL_MONTH,
    p.SEGMENT,
    p.ATR,
    p.P_LOGO_CHURN,
    p.P_DOLLAR_CHURN,
    p.P_FULL_RENEWAL,
    p.E_PARTIAL_RATE,
    p.E_RENEWAL_RATE,
    p.PRED_RENEW_RATE_FINAL,
    f.TARGET__RENEWAL_RATE                                    AS ACTUAL_RATE,
    CASE WHEN f.TARGET__RENEWAL_RATE = 0   THEN 1 ELSE 0 END  AS ACTUAL_LOGO_CHURN,
    CASE WHEN f.TARGET__RENEWAL_RATE = 1.0 THEN 1 ELSE 0 END  AS ACTUAL_FULL_RENEW,
    f.TARGET__RENEWED_AMOUNT                                  AS ACTUAL_DOLLARS
FROM {PREDS} p
JOIN latest_run lr ON p.RUN_ID = lr.RUN_ID
JOIN {FEAT} f
    ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
    AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
    AND p.SPLIT           = f.SPLIT
WHERE p.SPLIT='VALIDATION' AND p.HORIZON=0 AND f.COHORT='MATURED'
  AND p.ATR > 0 AND f.TARGET__RENEWAL_RATE IS NOT NULL
"""
df = fetch_dataframe(join_q)
print(f"  Rows: {len(df):,}   Months: {df['RENEWAL_MONTH'].nunique()}   "
      f"Total ATR: ${df['ATR'].sum()/1e6:,.1f}M\n", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# (1) CALIBRATION — P_LOGO_CHURN deciles vs actual logo churn rate
# ─────────────────────────────────────────────────────────────────────────────
def calibration_table(probs, actuals, weights, label, n_bins=10):
    df_c = pd.DataFrame({"p": probs, "y": actuals, "w": weights}).dropna()
    df_c["bin"] = pd.qcut(df_c["p"], q=n_bins, duplicates="drop")
    g = df_c.groupby("bin", observed=True).apply(
        lambda x: pd.Series({
            "N":             len(x),
            "ATR_M":         x["w"].sum() / 1e6,
            "AVG_PRED_PCT":  (x["p"] * 100).mean(),
            "ACTUAL_PCT":    (x["y"] * 100).mean(),
            "ACTUAL_WT_PCT": (x["y"] * x["w"]).sum() / x["w"].sum() * 100,
        })
    ).reset_index()
    g["GAP_PP"]    = g["ACTUAL_PCT"]    - g["AVG_PRED_PCT"]
    g["GAP_WT_PP"] = g["ACTUAL_WT_PCT"] - g["AVG_PRED_PCT"]
    return g.round(2)

cal_logo = calibration_table(df["P_LOGO_CHURN"],   df["ACTUAL_LOGO_CHURN"], df["ATR"], "logo")
cal_full = calibration_table(df["P_FULL_RENEWAL"], df["ACTUAL_FULL_RENEW"], df["ATR"], "full")

emit("(1A) CALIBRATION — P_LOGO_CHURN (decile bins)\n"
     "    Reading: GAP_PP near 0 = well-calibrated. Positive = under-predicted churn.\n"
     "    Weighted (ATR$) gap is what board actually cares about.",
     cal_logo.to_string(index=False))

emit("(1B) CALIBRATION — P_FULL_RENEWAL (decile bins)\n"
     "    Reading: GAP_PP near 0 = well-calibrated. Negative = over-predicted full renew.",
     cal_full.to_string(index=False))

# Calibration summary: Brier score + Expected Calibration Error (ECE)
def brier_and_ece(p, y, w, n_bins=10):
    df_b = pd.DataFrame({"p": p, "y": y, "w": w}).dropna()
    brier = ((df_b["p"] - df_b["y"]) ** 2 * df_b["w"]).sum() / df_b["w"].sum()
    df_b["bin"] = pd.qcut(df_b["p"], q=n_bins, duplicates="drop")
    by_bin = df_b.groupby("bin", observed=True).apply(
        lambda x: pd.Series({
            "wt":    x["w"].sum(),
            "p_avg": (x["p"] * x["w"]).sum() / x["w"].sum(),
            "y_avg": (x["y"] * x["w"]).sum() / x["w"].sum(),
        })
    )
    by_bin["abs_gap"] = (by_bin["p_avg"] - by_bin["y_avg"]).abs()
    ece = (by_bin["abs_gap"] * by_bin["wt"]).sum() / by_bin["wt"].sum()
    return brier, ece

b_logo, ece_logo = brier_and_ece(df["P_LOGO_CHURN"],   df["ACTUAL_LOGO_CHURN"], df["ATR"])
b_full, ece_full = brier_and_ece(df["P_FULL_RENEWAL"], df["ACTUAL_FULL_RENEW"], df["ATR"])

emit("(1C) CALIBRATION SUMMARY METRICS (lower = better)",
     pd.DataFrame({
         "metric":      ["Brier Score (wt ATR)", "Expected Cal Error (ECE, wt)"],
         "P_LOGO_CHURN":   [round(b_logo, 4),   round(ece_logo, 4)],
         "P_FULL_RENEWAL": [round(b_full, 4),   round(ece_full, 4)],
         "good_threshold": ["<0.20", "<0.05"],
     }).to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# (2) DISCRIMINATION — AUC, KS for P_LOGO_CHURN vs actual logo churn
# ─────────────────────────────────────────────────────────────────────────────
def auc_weighted(p, y, w):
    """ATR-weighted ROC AUC via Wilcoxon-Mann-Whitney formulation."""
    df_a = pd.DataFrame({"p": p, "y": y, "w": w}).dropna().sort_values("p")
    pos = df_a[df_a["y"] == 1]
    neg = df_a[df_a["y"] == 0]
    if len(pos) == 0 or len(neg) == 0:
        return np.nan
    # Unweighted AUC (simpler, robust)
    from sklearn.metrics import roc_auc_score
    return roc_auc_score(df_a["y"], df_a["p"])

def auc_atr_weighted(p, y, w):
    from sklearn.metrics import roc_auc_score
    df_a = pd.DataFrame({"p": p, "y": y, "w": w}).dropna()
    return roc_auc_score(df_a["y"], df_a["p"], sample_weight=df_a["w"])

def ks_stat(p, y):
    df_k = pd.DataFrame({"p": p, "y": y}).dropna().sort_values("p")
    pos = df_k[df_k["y"] == 1]["p"].values
    neg = df_k[df_k["y"] == 0]["p"].values
    from scipy.stats import ks_2samp
    return ks_2samp(pos, neg).statistic

auc_logo_u = auc_weighted(df["P_LOGO_CHURN"],   df["ACTUAL_LOGO_CHURN"], df["ATR"])
auc_logo_w = auc_atr_weighted(df["P_LOGO_CHURN"], df["ACTUAL_LOGO_CHURN"], df["ATR"])
auc_full_u = auc_weighted(df["P_FULL_RENEWAL"], df["ACTUAL_FULL_RENEW"], df["ATR"])
auc_full_w = auc_atr_weighted(df["P_FULL_RENEWAL"], df["ACTUAL_FULL_RENEW"], df["ATR"])
ks_logo = ks_stat(df["P_LOGO_CHURN"],   df["ACTUAL_LOGO_CHURN"])
ks_full = ks_stat(df["P_FULL_RENEWAL"], df["ACTUAL_FULL_RENEW"])

emit("(2) DISCRIMINATION METRICS (higher = better)",
     pd.DataFrame({
         "metric":         ["AUC (unweighted)", "AUC (ATR-weighted)", "KS statistic"],
         "P_LOGO_CHURN":   [round(auc_logo_u, 3), round(auc_logo_w, 3), round(ks_logo, 3)],
         "P_FULL_RENEWAL": [round(auc_full_u, 3), round(auc_full_w, 3), round(ks_full, 3)],
         "minimum_acceptable": [">0.70", ">0.70", ">0.30"],
         "target":             [">0.80", ">0.80", ">0.45"],
     }).to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# (3) TIME STABILITY — calibration & AUC month-by-month
# ─────────────────────────────────────────────────────────────────────────────
months = sorted(df["RENEWAL_MONTH"].dropna().unique())
ts_rows = []
for m in months:
    sub = df[df["RENEWAL_MONTH"] == m]
    if len(sub) < 100 or sub["ACTUAL_LOGO_CHURN"].sum() < 5:
        continue
    try:
        auc_l = auc_atr_weighted(sub["P_LOGO_CHURN"], sub["ACTUAL_LOGO_CHURN"], sub["ATR"])
    except Exception:
        auc_l = np.nan
    pred_logo_rate = (sub["P_LOGO_CHURN"] * sub["ATR"]).sum() / sub["ATR"].sum()
    actual_logo_rate = (sub["ACTUAL_LOGO_CHURN"] * sub["ATR"]).sum() / sub["ATR"].sum()
    pred_full_rate = (sub["P_FULL_RENEWAL"] * sub["ATR"]).sum() / sub["ATR"].sum()
    actual_full_rate = (sub["ACTUAL_FULL_RENEW"] * sub["ATR"]).sum() / sub["ATR"].sum()
    ts_rows.append({
        "RENEWAL_MONTH":   str(m)[:10],
        "N":               len(sub),
        "ATR_M":           round(sub["ATR"].sum() / 1e6, 1),
        "AUC_LOGO":        round(auc_l, 3),
        "PRED_LOGO_PCT":   round(pred_logo_rate * 100, 2),
        "ACTUAL_LOGO_PCT": round(actual_logo_rate * 100, 2),
        "LOGO_GAP_PP":     round((actual_logo_rate - pred_logo_rate) * 100, 2),
        "PRED_FULL_PCT":   round(pred_full_rate * 100, 2),
        "ACTUAL_FULL_PCT": round(actual_full_rate * 100, 2),
        "FULL_GAP_PP":     round((actual_full_rate - pred_full_rate) * 100, 2),
    })

ts_df = pd.DataFrame(ts_rows)
emit("(3) TIME STABILITY — month-by-month (VALIDATION/H0)",
     ts_df.to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# (4) AGGREGATE BUDGET-NEUTRALITY TEST
#     Compare two ways of computing portfolio $ forecast:
#       Method A (current): SUM(PRED_RENEW_RATE_FINAL * ATR)
#       Method B (new):     SUM(P_FULL_RENEWAL * 1.0 + P_DOLLAR_CHURN * E_PARTIAL_RATE) * ATR
#     If Method B is within ±2pp of actuals, we can use the raw classifier
#     output as the primary number without breaking the board narrative.
# ─────────────────────────────────────────────────────────────────────────────
budget = df.assign(
    METHOD_A_RATE = df["PRED_RENEW_RATE_FINAL"],
    METHOD_B_RATE = (df["P_FULL_RENEWAL"] * 1.0
                     + df["P_DOLLAR_CHURN"] * df["E_PARTIAL_RATE"].fillna(0)
                     + df["P_LOGO_CHURN"]   * 0.0),
)
def wtd(col): return (budget[col] * budget["ATR"]).sum() / budget["ATR"].sum()
actual_rate = wtd("ACTUAL_RATE")
method_a    = wtd("METHOD_A_RATE")
method_b    = wtd("METHOD_B_RATE")

emit("(4) PORTFOLIO BUDGET-NEUTRALITY TEST",
     pd.DataFrame({
         "metric": [
             "Actual aggregate rate",
             "Method A (current FINAL)",
             "Method B (raw classifier expected)",
             "Method A − Actual (pp)",
             "Method B − Actual (pp)",
         ],
         "value_pct": [
             round(actual_rate * 100, 2),
             round(method_a    * 100, 2),
             round(method_b    * 100, 2),
             round((method_a - actual_rate) * 100, 2),
             round((method_b - actual_rate) * 100, 2),
         ],
     }).to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# SAVE
# ─────────────────────────────────────────────────────────────────────────────
out_path = _HERE / "CALIBRATION_VALIDATION_RESULTS.txt"
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(results))
print(f"\nResults saved to: {out_path}")
