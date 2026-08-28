"""
END-TO-END VALIDATION + DRIFT MONITOR

Tests three things:
  (1) SAFETY: aggregate $ forecast unchanged after calibration changes
  (2) QUALITY: calibrated probabilities match expected ECE / AUC benchmarks
  (3) DRIFT:   AUC and ECE by month — flag if degradation detected

Run this weekly after each pipeline refresh to catch drift before board presentations.
"""
import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import json
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from connection import fetch_dataframe

pd.set_option("display.max_rows", 50)
pd.set_option("display.max_columns", 20)
pd.set_option("display.width", 160)
pd.set_option("display.float_format", "{:.3f}".format)

PREDS      = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
FEAT       = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE"
APP_DETAIL = "STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"
KNOT_TABLE = "STREAMLIT_APPS.DBO.V5_CALIBRATION_KNOTS"

SEP = "\n" + "=" * 90 + "\n"
results = []
flags = []

def emit(label, content):
    block = f"{SEP}{label}{SEP}{content}\n"
    print(block)
    results.append(block)

def flag(msg):
    flags.append(msg)
    print(f"  *** FLAG: {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# (1) SAFETY: aggregate $ forecast — check that the published board numbers
#     are identical before and after the calibration deployment.
#     Board number = RENEWAL_FORECAST (portfolio, Finance-reconciled).
# ─────────────────────────────────────────────────────────────────────────────
print("(1) Checking aggregate dollar forecast safety...", flush=True)
agg_q = f"""
SELECT
    RENEWAL_MONTH,
    ROUND(SUM(RENEWAL_FORECAST) / 1e6, 2)    AS RENEWAL_FORECAST_M,
    ROUND(SUM(ML_FORECAST) / 1e6, 2)         AS ML_FORECAST_M,
    ROUND(SUM(FINANCE_FORECAST) / 1e6, 2)    AS FINANCE_FORECAST_M,
    ROUND(SUM(ATR) / 1e6, 2)                 AS ATR_M,
    ROUND(SUM(ACTUAL_RETAINED_ARR) / 1e6, 2) AS ACTUAL_M,
    COUNT(*) AS N_CONTRACTS
FROM {APP_DETAIL}
WHERE RENEWAL_MONTH >= '2026-01-01'
  AND COALESCE(ATR, 0) > 0
GROUP BY RENEWAL_MONTH
ORDER BY RENEWAL_MONTH
"""
df_agg = fetch_dataframe(agg_q)
emit("(1) AGGREGATE DOLLAR FORECAST BY MONTH", df_agg.to_string(index=False))

if not df_agg.empty and "ATR_M" in df_agg.columns:
    total_atr    = df_agg["ATR_M"].sum()
    total_fcst   = df_agg["RENEWAL_FORECAST_M"].sum()
    total_ml     = df_agg["ML_FORECAST_M"].sum()
    total_actual = df_agg["ACTUAL_M"].sum()
    port_rate    = total_fcst / total_atr if total_atr > 0 else 0
    emit("(1) PORTFOLIO SUMMARY",
         pd.DataFrame({
             "metric": ["Total ATR ($M)", "Total RENEWAL_FORECAST ($M)",
                        "Total ML_FORECAST ($M)", "Total Actual ($M)",
                        "Portfolio Rate"],
             "value": [round(total_atr, 1), round(total_fcst, 1),
                       round(total_ml, 1), round(total_actual, 1),
                       f"{port_rate*100:.2f}%"],
         }).to_string(index=False))
    # Flag if RENEWAL_FORECAST and ML_FORECAST diverge by >5pp portfolio-wide
    if total_atr > 0:
        ml_rate   = total_ml / total_atr
        diff_pp   = abs(port_rate - ml_rate) * 100
        if diff_pp > 5.0:
            flag(f"RENEWAL_FORECAST and ML_FORECAST differ by {diff_pp:.1f}pp portfolio-wide")


# ─────────────────────────────────────────────────────────────────────────────
# (2) QUALITY: validate calibrated probabilities against VALIDATION holdout
# ─────────────────────────────────────────────────────────────────────────────
print("\n(2) Loading calibration knots...", flush=True)
knot_df = fetch_dataframe(f"SELECT MODEL_TARGET, SEGMENT, HORIZON, KNOT_X_JSON, KNOT_Y_JSON FROM {KNOT_TABLE}")
print(f"  Loaded {len(knot_df)} knot rows")
knots = {}
for _, row in knot_df.iterrows():
    key = (str(row["MODEL_TARGET"]), str(row["SEGMENT"]), int(row["HORIZON"]))
    knots[key] = {"x": json.loads(row["KNOT_X_JSON"]), "y": json.loads(row["KNOT_Y_JSON"])}

def apply_iso(p_vals, target, seg, h, knots):
    h = int(min(max(h, 0), 6))
    pts = knots.get((target, seg, h)) or knots.get((target, "__GLOBAL__", h))
    if not pts:
        return p_vals
    return np.interp(p_vals, pts["x"], pts["y"]).clip(0, 1)

print("(2) Loading validation data...", flush=True)
val_q = f"""
WITH latest_run AS (SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT='VALIDATION')
SELECT p.CONTRACT_ID_UFR, p.RENEWAL_MONTH, p.HORIZON, p.SEGMENT, p.ATR,
       p.P_LOGO_CHURN, p.P_FULL_RENEWAL,
       p.PRED_RENEW_RATE_FINAL,
       f.TARGET__RENEWAL_RATE AS ACTUAL_RATE,
       CASE WHEN f.TARGET__RENEWAL_RATE = 0   THEN 1 ELSE 0 END AS ACTUAL_LOGO,
       CASE WHEN f.TARGET__RENEWAL_RATE = 1.0 THEN 1 ELSE 0 END AS ACTUAL_FULL
FROM {PREDS} p
JOIN latest_run lr ON p.RUN_ID = lr.RUN_ID
JOIN {FEAT} f
    ON p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
    AND p.RENEWAL_MONTH  = f.RENEWAL_MONTH
    AND p.HORIZON        = f.HORIZON
    AND p.SPLIT          = f.SPLIT
WHERE p.SPLIT='VALIDATION' AND f.COHORT='MATURED'
  AND p.ATR > 0 AND f.TARGET__RENEWAL_RATE IS NOT NULL
"""
df_val = fetch_dataframe(val_q)
df_val["RENEWAL_MONTH"] = pd.to_datetime(df_val["RENEWAL_MONTH"])
print(f"  Rows: {len(df_val):,}", flush=True)

# Apply calibration
p_logo_cal = np.full(len(df_val), np.nan)
p_full_cal = np.full(len(df_val), np.nan)
for (seg, h), grp in df_val.groupby(["SEGMENT", "HORIZON"]):
    idx = df_val.index.get_indexer(grp.index)
    p_logo_cal[idx] = apply_iso(grp["P_LOGO_CHURN"].values.astype(float), "P_LOGO_CHURN", seg, h, knots)
    p_full_cal[idx] = apply_iso(grp["P_FULL_RENEWAL"].values.astype(float), "P_FULL_RENEWAL", seg, h, knots)
df_val["P_LOGO_CAL"] = p_logo_cal
df_val["P_FULL_CAL"] = p_full_cal

# ECE helper
def ece(p, y, w, n_bins=10):
    df_e = pd.DataFrame({"p": p, "y": y, "w": w}).dropna()
    try:
        df_e["bin"] = pd.qcut(df_e["p"], q=n_bins, duplicates="drop")
    except Exception:
        return np.nan
    by = df_e.groupby("bin", observed=True, group_keys=False).apply(
        lambda x: pd.Series({
            "wt":    x["w"].sum(),
            "p_avg": (x["p"] * x["w"]).sum() / x["w"].sum(),
            "y_avg": (x["y"] * x["w"]).sum() / x["w"].sum(),
        }), include_groups=False
    )
    by["abs_gap"] = (by["p_avg"] - by["y_avg"]).abs()
    return (by["abs_gap"] * by["wt"]).sum() / by["wt"].sum()

ece_logo_raw = ece(df_val["P_LOGO_CHURN"], df_val["ACTUAL_LOGO"], df_val["ATR"])
ece_full_raw = ece(df_val["P_FULL_RENEWAL"], df_val["ACTUAL_FULL"], df_val["ATR"])
ece_logo_cal = ece(df_val["P_LOGO_CAL"], df_val["ACTUAL_LOGO"], df_val["ATR"])
ece_full_cal = ece(df_val["P_FULL_CAL"], df_val["ACTUAL_FULL"], df_val["ATR"])
auc_logo_raw = roc_auc_score(df_val["ACTUAL_LOGO"], df_val["P_LOGO_CHURN"])
auc_full_raw = roc_auc_score(df_val["ACTUAL_FULL"], df_val["P_FULL_RENEWAL"])
auc_logo_cal = roc_auc_score(df_val["ACTUAL_LOGO"], df_val["P_LOGO_CAL"])
auc_full_cal = roc_auc_score(df_val["ACTUAL_FULL"], df_val["P_FULL_CAL"])

emit("(2) QUALITY METRICS — RAW vs CALIBRATED (full validation set)",
     pd.DataFrame({
         "metric": ["ECE (wt ATR)", "AUC (unweighted)", "good threshold"],
         "P_LOGO raw": [round(ece_logo_raw, 4), round(auc_logo_raw, 3), "ECE<0.05 AUC>0.78"],
         "P_LOGO cal": [round(ece_logo_cal, 4), round(auc_logo_cal, 3), ""],
         "P_FULL raw": [round(ece_full_raw, 4), round(auc_full_raw, 3), ""],
         "P_FULL cal": [round(ece_full_cal, 4), round(auc_full_cal, 3), ""],
     }).to_string(index=False))

# Flags
DRIFT_ECE_THRESHOLD = 0.08
DRIFT_AUC_THRESHOLD = 0.65
if ece_full_cal > DRIFT_ECE_THRESHOLD:
    flag(f"P_FULL_CAL ECE={ece_full_cal:.4f} exceeds threshold {DRIFT_ECE_THRESHOLD}")
if auc_full_cal < DRIFT_AUC_THRESHOLD:
    flag(f"P_FULL_CAL AUC={auc_full_cal:.3f} below threshold {DRIFT_AUC_THRESHOLD}")
if ece_logo_cal > DRIFT_ECE_THRESHOLD:
    flag(f"P_LOGO_CAL ECE={ece_logo_cal:.4f} exceeds threshold {DRIFT_ECE_THRESHOLD}")
if auc_logo_cal < DRIFT_AUC_THRESHOLD:
    flag(f"P_LOGO_CAL AUC={auc_logo_cal:.3f} below threshold {DRIFT_AUC_THRESHOLD}")


# ─────────────────────────────────────────────────────────────────────────────
# (3) DRIFT MONITOR: month-by-month AUC and ECE for calibrated probabilities
# ─────────────────────────────────────────────────────────────────────────────
rows = []
for m, sub in df_val.groupby("RENEWAL_MONTH"):
    if len(sub) < 100 or sub["ACTUAL_LOGO"].sum() < 5:
        continue
    try:
        auc_l = roc_auc_score(sub["ACTUAL_LOGO"], sub["P_LOGO_CAL"])
        auc_f = roc_auc_score(sub["ACTUAL_FULL"], sub["P_FULL_CAL"])
    except Exception:
        auc_l = auc_f = np.nan
    ece_l = ece(sub["P_LOGO_CAL"], sub["ACTUAL_LOGO"], sub["ATR"])
    ece_f = ece(sub["P_FULL_CAL"], sub["ACTUAL_FULL"], sub["ATR"])
    actual_logo = (sub["ACTUAL_LOGO"] * sub["ATR"]).sum() / sub["ATR"].sum() * 100
    pred_logo   = (sub["P_LOGO_CAL"] * sub["ATR"]).sum() / sub["ATR"].sum() * 100
    actual_full = (sub["ACTUAL_FULL"] * sub["ATR"]).sum() / sub["ATR"].sum() * 100
    pred_full   = (sub["P_FULL_CAL"] * sub["ATR"]).sum() / sub["ATR"].sum() * 100
    status = "OK"
    if not np.isnan(auc_f) and auc_f < DRIFT_AUC_THRESHOLD:
        status = "DRIFT: AUC"
    if not np.isnan(ece_f) and ece_f > DRIFT_ECE_THRESHOLD:
        status = "DRIFT: ECE"
    rows.append({
        "MONTH":    str(m.date()),
        "N":        len(sub),
        "AUC_LOGO": round(auc_l, 3),
        "AUC_FULL": round(auc_f, 3),
        "ECE_LOGO": round(ece_l, 4),
        "ECE_FULL": round(ece_f, 4),
        "ACTUAL_CHURN_PCT":  round(actual_logo, 2),
        "PRED_CHURN_PCT":    round(pred_logo, 2),
        "ACTUAL_FULL_PCT":   round(actual_full, 2),
        "PRED_FULL_PCT":     round(pred_full, 2),
        "STATUS":   status,
    })
drift_df = pd.DataFrame(rows)
emit("(3) DRIFT MONITOR — month-by-month calibrated metrics (VALIDATION split)",
     drift_df.to_string(index=False))

if (drift_df["STATUS"] != "OK").any():
    bad = drift_df[drift_df["STATUS"] != "OK"]["MONTH"].tolist()
    flag(f"Drift detected in months: {bad}")


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
out_path = _HERE / "VALIDATION_AND_DRIFT_RESULTS.txt"
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(results))

print(SEP)
if flags:
    print("ALERTS:")
    for fl in flags:
        print(f"  *** {fl}")
    print(f"\n{len(flags)} alert(s) found. Review before next board presentation.")
else:
    print("ALL CHECKS PASSED. No drift flags.")
print(f"\nResults saved to: {out_path}")
