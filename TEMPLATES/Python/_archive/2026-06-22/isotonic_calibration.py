"""
ISOTONIC CALIBRATION — fit per-segment isotonic regression on raw
P_LOGO_CHURN and P_FULL_RENEWAL, then re-validate all metrics:
  - Per-decile gap should drop to <±3pp
  - AUC should be preserved (isotonic is monotonic — cannot reduce AUC)
  - Portfolio aggregate must land within ±2pp of actuals

Train on VALIDATION months Dec-2025 → Feb-2026, test on Mar-May 2026
(hold out the most recent 3 months to confirm calibration generalizes
forward — this mimics the July production scenario).
"""
import sys
from pathlib import Path
import json
import pickle
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score, brier_score_loss
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
# Load joined data
# ─────────────────────────────────────────────────────────────────────────────
print("Loading joined predictions+labels...", flush=True)
join_q = f"""
WITH latest_run AS (
    SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT = 'VALIDATION'
)
SELECT
    p.CONTRACT_ID_UFR, p.RENEWAL_MONTH, p.SEGMENT, p.ATR,
    p.P_LOGO_CHURN, p.P_DOLLAR_CHURN, p.P_FULL_RENEWAL,
    p.E_PARTIAL_RATE, p.E_RENEWAL_RATE, p.PRED_RENEW_RATE_FINAL,
    f.TARGET__RENEWAL_RATE                                    AS ACTUAL_RATE,
    CASE WHEN f.TARGET__RENEWAL_RATE = 0   THEN 1 ELSE 0 END  AS ACTUAL_LOGO_CHURN,
    CASE WHEN f.TARGET__RENEWAL_RATE = 1.0 THEN 1 ELSE 0 END  AS ACTUAL_FULL_RENEW
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
df["RENEWAL_MONTH"] = pd.to_datetime(df["RENEWAL_MONTH"])
print(f"  Total rows: {len(df):,}", flush=True)

# Train on earlier months, test on later months
cutoff = pd.Timestamp("2026-03-01")
train = df[df["RENEWAL_MONTH"] <  cutoff].copy()
test  = df[df["RENEWAL_MONTH"] >= cutoff].copy()
print(f"  Train: {len(train):,} rows ({train['RENEWAL_MONTH'].min().date()} → "
      f"{train['RENEWAL_MONTH'].max().date()})")
print(f"  Test:  {len(test):,} rows ({test['RENEWAL_MONTH'].min().date()} → "
      f"{test['RENEWAL_MONTH'].max().date()})\n")


# ─────────────────────────────────────────────────────────────────────────────
# Fit per-segment isotonic calibrators (ATR-weighted)
# ─────────────────────────────────────────────────────────────────────────────
def fit_iso(train_df, prob_col, target_col):
    """Per-segment isotonic regression. Fallback to global if segment too small."""
    cals = {}
    global_iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    global_iso.fit(train_df[prob_col].values,
                   train_df[target_col].values,
                   sample_weight=train_df["ATR"].values)
    cals["__GLOBAL__"] = global_iso
    for seg, sub in train_df.groupby("SEGMENT"):
        if len(sub) < 500 or sub[target_col].sum() < 20:
            print(f"    [{prob_col}] {seg}: only {len(sub)} rows / "
                  f"{int(sub[target_col].sum())} positives → using global")
            cals[seg] = global_iso
            continue
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(sub[prob_col].values, sub[target_col].values,
                sample_weight=sub["ATR"].values)
        cals[seg] = iso
    return cals

print("Fitting isotonic calibrators...")
print("  P_LOGO_CHURN:")
cals_logo = fit_iso(train, "P_LOGO_CHURN",   "ACTUAL_LOGO_CHURN")
print("  P_FULL_RENEWAL:")
cals_full = fit_iso(train, "P_FULL_RENEWAL", "ACTUAL_FULL_RENEW")
print()

def apply_cal(df_in, prob_col, cals, new_col):
    out = np.zeros(len(df_in))
    for seg, idx in df_in.groupby("SEGMENT").groups.items():
        iso = cals.get(seg, cals["__GLOBAL__"])
        out[df_in.index.get_indexer(idx)] = iso.transform(df_in.loc[idx, prob_col].values)
    df_in[new_col] = np.clip(out, 0.0, 1.0)
    return df_in

test  = apply_cal(test.reset_index(drop=True),  "P_LOGO_CHURN",   cals_logo, "P_LOGO_CAL")
test  = apply_cal(test,                          "P_FULL_RENEWAL", cals_full, "P_FULL_CAL")
train = apply_cal(train.reset_index(drop=True), "P_LOGO_CHURN",   cals_logo, "P_LOGO_CAL")
train = apply_cal(train,                         "P_FULL_RENEWAL", cals_full, "P_FULL_CAL")


# ─────────────────────────────────────────────────────────────────────────────
# Renormalise so calibrated P_LOGO + P_PARTIAL + P_FULL = 1
#   Strategy: keep P_LOGO_CAL and P_FULL_CAL, derive P_PARTIAL = 1 - them.
#   If P_LOGO_CAL + P_FULL_CAL > 1, scale them down proportionally.
# ─────────────────────────────────────────────────────────────────────────────
def renormalize(d):
    total = d["P_LOGO_CAL"] + d["P_FULL_CAL"]
    overflow = total > 1.0
    scale = np.where(overflow, 1.0 / total.where(total > 0, 1.0), 1.0)
    d["P_LOGO_CAL_R"] = d["P_LOGO_CAL"] * scale
    d["P_FULL_CAL_R"] = d["P_FULL_CAL"] * scale
    d["P_PARTIAL_CAL_R"] = (1.0 - d["P_LOGO_CAL_R"] - d["P_FULL_CAL_R"]).clip(lower=0.0)
    return d

test  = renormalize(test)
train = renormalize(train)


# ─────────────────────────────────────────────────────────────────────────────
# (A) Re-check calibration on HOLDOUT (Mar-May)
# ─────────────────────────────────────────────────────────────────────────────
def calibration_table(probs, actuals, weights, n_bins=10):
    df_c = pd.DataFrame({"p": probs, "y": actuals, "w": weights}).dropna()
    try:
        df_c["bin"] = pd.qcut(df_c["p"], q=n_bins, duplicates="drop")
    except ValueError:
        df_c["bin"] = pd.cut(df_c["p"], bins=n_bins)
    g = df_c.groupby("bin", observed=True, group_keys=False).apply(
        lambda x: pd.Series({
            "N":             len(x),
            "ATR_M":         x["w"].sum() / 1e6,
            "AVG_PRED_PCT":  (x["p"] * 100).mean(),
            "ACTUAL_WT_PCT": (x["y"] * x["w"]).sum() / x["w"].sum() * 100,
        }),
        include_groups=False,
    ).reset_index()
    g["GAP_WT_PP"] = g["ACTUAL_WT_PCT"] - g["AVG_PRED_PCT"]
    return g.round(2)

emit("(A1) HOLDOUT CALIBRATION — RAW P_LOGO_CHURN",
     calibration_table(test["P_LOGO_CHURN"], test["ACTUAL_LOGO_CHURN"], test["ATR"]).to_string(index=False))
emit("(A2) HOLDOUT CALIBRATION — CALIBRATED P_LOGO_CAL (renormalized)",
     calibration_table(test["P_LOGO_CAL_R"], test["ACTUAL_LOGO_CHURN"], test["ATR"]).to_string(index=False))
emit("(A3) HOLDOUT CALIBRATION — RAW P_FULL_RENEWAL",
     calibration_table(test["P_FULL_RENEWAL"], test["ACTUAL_FULL_RENEW"], test["ATR"]).to_string(index=False))
emit("(A4) HOLDOUT CALIBRATION — CALIBRATED P_FULL_CAL (renormalized)",
     calibration_table(test["P_FULL_CAL_R"], test["ACTUAL_FULL_RENEW"], test["ATR"]).to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# (B) Summary metrics: ECE, Brier, AUC — RAW vs CALIBRATED on HOLDOUT
# ─────────────────────────────────────────────────────────────────────────────
def brier_ece_auc(p, y, w, n_bins=10):
    df_b = pd.DataFrame({"p": p, "y": y, "w": w}).dropna()
    brier = ((df_b["p"] - df_b["y"]) ** 2 * df_b["w"]).sum() / df_b["w"].sum()
    df_b["bin"] = pd.qcut(df_b["p"], q=n_bins, duplicates="drop")
    by_bin = df_b.groupby("bin", observed=True, group_keys=False).apply(
        lambda x: pd.Series({
            "wt":    x["w"].sum(),
            "p_avg": (x["p"] * x["w"]).sum() / x["w"].sum(),
            "y_avg": (x["y"] * x["w"]).sum() / x["w"].sum(),
        }),
        include_groups=False,
    )
    by_bin["abs_gap"] = (by_bin["p_avg"] - by_bin["y_avg"]).abs()
    ece    = (by_bin["abs_gap"] * by_bin["wt"]).sum() / by_bin["wt"].sum()
    auc_u  = roc_auc_score(df_b["y"], df_b["p"])
    auc_w  = roc_auc_score(df_b["y"], df_b["p"], sample_weight=df_b["w"])
    return round(brier, 4), round(ece, 4), round(auc_u, 3), round(auc_w, 3)

b_lr, e_lr, au_lr, aw_lr = brier_ece_auc(test["P_LOGO_CHURN"],   test["ACTUAL_LOGO_CHURN"], test["ATR"])
b_lc, e_lc, au_lc, aw_lc = brier_ece_auc(test["P_LOGO_CAL_R"],   test["ACTUAL_LOGO_CHURN"], test["ATR"])
b_fr, e_fr, au_fr, aw_fr = brier_ece_auc(test["P_FULL_RENEWAL"], test["ACTUAL_FULL_RENEW"], test["ATR"])
b_fc, e_fc, au_fc, aw_fc = brier_ece_auc(test["P_FULL_CAL_R"],   test["ACTUAL_FULL_RENEW"], test["ATR"])

emit("(B) HOLDOUT METRICS — RAW vs CALIBRATED",
     pd.DataFrame({
         "metric":          ["Brier (wt ATR)", "ECE (wt)", "AUC (unweighted)", "AUC (ATR-wt)"],
         "P_LOGO_raw":      [b_lr, e_lr, au_lr, aw_lr],
         "P_LOGO_CAL":      [b_lc, e_lc, au_lc, aw_lc],
         "P_FULL_raw":      [b_fr, e_fr, au_fr, aw_fr],
         "P_FULL_CAL":      [b_fc, e_fc, au_fc, aw_fc],
         "good_threshold":  ["<0.20", "<0.05", ">0.78", ">0.70"],
     }).to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# (C) Budget-neutrality — Aggregate $ forecast comparison on HOLDOUT
#     Method A: SUM(PRED_RENEW_RATE_FINAL * ATR)
#     Method B: SUM((P_FULL_CAL_R*1 + P_PARTIAL_CAL_R*E_PARTIAL + P_LOGO_CAL_R*0) * ATR)
# ─────────────────────────────────────────────────────────────────────────────
test["E_PARTIAL_RATE_FILL"] = test["E_PARTIAL_RATE"].fillna(test["E_PARTIAL_RATE"].median())
test["METHOD_B_RATE_CAL"] = (
    test["P_FULL_CAL_R"]    * 1.0
  + test["P_PARTIAL_CAL_R"] * test["E_PARTIAL_RATE_FILL"]
  + test["P_LOGO_CAL_R"]    * 0.0
)

def wtd(col): return (test[col] * test["ATR"]).sum() / test["ATR"].sum()
actual_rate = wtd("ACTUAL_RATE")
method_a    = wtd("PRED_RENEW_RATE_FINAL")
method_b    = wtd("METHOD_B_RATE_CAL")

emit("(C) HOLDOUT BUDGET-NEUTRALITY — Aggregate $ Forecast",
     pd.DataFrame({
         "metric":  [
             "Actual aggregate rate",
             "Method A — current FINAL",
             "Method B — CALIBRATED raw (renormalized)",
             "Method A − Actual (pp)",
             "Method B − Actual (pp)",
             "ATR_M (holdout)",
             "Method A $ off ($M)",
             "Method B $ off ($M)",
         ],
         "value": [
             round(actual_rate * 100, 2),
             round(method_a    * 100, 2),
             round(method_b    * 100, 2),
             round((method_a - actual_rate) * 100, 2),
             round((method_b - actual_rate) * 100, 2),
             round(test["ATR"].sum() / 1e6, 1),
             round((method_a - actual_rate) * test["ATR"].sum() / 1e6, 1),
             round((method_b - actual_rate) * test["ATR"].sum() / 1e6, 1),
         ],
     }).to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# (D) Month-by-month stability of calibrated forecasts on HOLDOUT
# ─────────────────────────────────────────────────────────────────────────────
rows = []
for m, sub in test.groupby("RENEWAL_MONTH"):
    try:
        auc_l = roc_auc_score(sub["ACTUAL_LOGO_CHURN"], sub["P_LOGO_CAL_R"], sample_weight=sub["ATR"])
    except Exception:
        auc_l = np.nan
    rows.append({
        "MONTH":           str(m.date()),
        "N":               len(sub),
        "ATR_M":           round(sub["ATR"].sum() / 1e6, 1),
        "AUC_LOGO_CAL":    round(auc_l, 3),
        "ACTUAL_LOGO_PCT": round((sub["ACTUAL_LOGO_CHURN"] * sub["ATR"]).sum() / sub["ATR"].sum() * 100, 2),
        "PRED_LOGO_RAW":   round((sub["P_LOGO_CHURN"]      * sub["ATR"]).sum() / sub["ATR"].sum() * 100, 2),
        "PRED_LOGO_CAL":   round((sub["P_LOGO_CAL_R"]      * sub["ATR"]).sum() / sub["ATR"].sum() * 100, 2),
        "ACTUAL_FULL_PCT": round((sub["ACTUAL_FULL_RENEW"] * sub["ATR"]).sum() / sub["ATR"].sum() * 100, 2),
        "PRED_FULL_RAW":   round((sub["P_FULL_RENEWAL"]    * sub["ATR"]).sum() / sub["ATR"].sum() * 100, 2),
        "PRED_FULL_CAL":   round((sub["P_FULL_CAL_R"]      * sub["ATR"]).sum() / sub["ATR"].sum() * 100, 2),
    })

emit("(D) HOLDOUT MONTH-BY-MONTH — calibrated forecasts vs actuals",
     pd.DataFrame(rows).to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# Save calibrators + results
# ─────────────────────────────────────────────────────────────────────────────
cals_path = _HERE / "isotonic_calibrators.pkl"
with open(cals_path, "wb") as fp:
    pickle.dump({"P_LOGO_CHURN": cals_logo, "P_FULL_RENEWAL": cals_full}, fp)
print(f"\nCalibrators saved to: {cals_path}")

out_path = _HERE / "ISOTONIC_CALIBRATION_RESULTS.txt"
with open(out_path, "w", encoding="utf-8") as fp:
    fp.write("\n".join(results))
print(f"Results saved to: {out_path}")

# Also export calibrator points to JSON for Snowflake deployment
def cals_to_json(cals_dict):
    out = {}
    for seg, iso in cals_dict.items():
        out[seg] = {
            "x": iso.X_thresholds_.tolist(),
            "y": iso.y_thresholds_.tolist(),
        }
    return out
json_path = _HERE / "isotonic_calibrators.json"
with open(json_path, "w") as fp:
    json.dump({
        "P_LOGO_CHURN":   cals_to_json(cals_logo),
        "P_FULL_RENEWAL": cals_to_json(cals_full),
    }, fp, indent=2)
print(f"Calibrator knots exported to: {json_path}")
