"""
HORIZON-AWARE CALIBRATION TEST

The previous test only validated H+0 (post-renewal). Real July production
forecasts are made at H+1, H+3, H+6. Calibration may shift by horizon because:
  - Longer-horizon predictions are inherently more uncertain
  - The classifier was trained jointly across horizons
  - Anchor compression may be horizon-specific

Test:
  1. Check raw calibration & AUC per horizon
  2. Fit separate isotonic calibrators per (segment × horizon)
  3. Validate per-horizon on holdout (Mar-May 2026)
  4. Compare with H+0-only calibrators (do we need horizon-specific?)
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
from sklearn.metrics import roc_auc_score
from connection import fetch_dataframe

pd.set_option("display.max_rows", 200)
pd.set_option("display.max_columns", 30)
pd.set_option("display.width", 200)
pd.set_option("display.float_format", "{:.3f}".format)

PREDS = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
FEAT  = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE"

SEP = "\n" + "=" * 100 + "\n"
results = []
def emit(label, content):
    block = f"{SEP}{label}{SEP}{content}\n"
    print(block)
    results.append(block)


# Pull ALL horizons available
print("Loading predictions+labels for ALL HORIZONS...", flush=True)
q = f"""
WITH latest_run AS (
    SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT = 'VALIDATION'
)
SELECT
    p.CONTRACT_ID_UFR, p.RENEWAL_MONTH, p.HORIZON, p.SEGMENT, p.ATR,
    p.P_LOGO_CHURN, p.P_DOLLAR_CHURN, p.P_FULL_RENEWAL,
    p.E_PARTIAL_RATE, p.PRED_RENEW_RATE_FINAL,
    f.TARGET__RENEWAL_RATE                                    AS ACTUAL_RATE,
    CASE WHEN f.TARGET__RENEWAL_RATE = 0   THEN 1 ELSE 0 END  AS ACTUAL_LOGO_CHURN,
    CASE WHEN f.TARGET__RENEWAL_RATE = 1.0 THEN 1 ELSE 0 END  AS ACTUAL_FULL_RENEW
FROM {PREDS} p
JOIN latest_run lr ON p.RUN_ID = lr.RUN_ID
JOIN {FEAT} f
    ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
    AND p.RENEWAL_MONTH   = f.RENEWAL_MONTH
    AND p.HORIZON         = f.HORIZON
    AND p.SPLIT           = f.SPLIT
WHERE p.SPLIT='VALIDATION' AND f.COHORT='MATURED'
  AND p.ATR > 0 AND f.TARGET__RENEWAL_RATE IS NOT NULL
"""
df = fetch_dataframe(q)
df["RENEWAL_MONTH"] = pd.to_datetime(df["RENEWAL_MONTH"])
print(f"  Total rows: {len(df):,}   Horizons: {sorted(df['HORIZON'].unique())}\n", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# (A) Raw calibration & AUC by horizon
# ─────────────────────────────────────────────────────────────────────────────
def metrics_by_h(d, p_col, y_col):
    rows = []
    for h, sub in d.groupby("HORIZON"):
        if sub[y_col].sum() < 10:
            continue
        try:
            auc_u = roc_auc_score(sub[y_col], sub[p_col])
            auc_w = roc_auc_score(sub[y_col], sub[p_col], sample_weight=sub["ATR"])
        except Exception:
            auc_u, auc_w = np.nan, np.nan
        pred_w = (sub[p_col] * sub["ATR"]).sum() / sub["ATR"].sum()
        actu_w = (sub[y_col] * sub["ATR"]).sum() / sub["ATR"].sum()
        brier  = (((sub[p_col] - sub[y_col]) ** 2) * sub["ATR"]).sum() / sub["ATR"].sum()
        try:
            df_b = sub[[p_col, y_col, "ATR"]].copy()
            df_b["bin"] = pd.qcut(df_b[p_col], q=10, duplicates="drop")
            by = df_b.groupby("bin", observed=True, group_keys=False).apply(
                lambda x: pd.Series({
                    "wt":    x["ATR"].sum(),
                    "p_avg": (x[p_col] * x["ATR"]).sum() / x["ATR"].sum(),
                    "y_avg": (x[y_col] * x["ATR"]).sum() / x["ATR"].sum(),
                }), include_groups=False,
            )
            by["abs_gap"] = (by["p_avg"] - by["y_avg"]).abs()
            ece = (by["abs_gap"] * by["wt"]).sum() / by["wt"].sum()
        except Exception:
            ece = np.nan
        rows.append({
            "HORIZON": h, "N": len(sub),
            "ATR_M":   round(sub["ATR"].sum() / 1e6, 1),
            "PRED_PCT":   round(pred_w * 100, 2),
            "ACTUAL_PCT": round(actu_w * 100, 2),
            "GAP_PP":     round((pred_w - actu_w) * 100, 2),
            "AUC_UW":     round(auc_u, 3),
            "AUC_WT":     round(auc_w, 3),
            "BRIER":      round(brier, 4),
            "ECE":        round(ece, 4),
        })
    return pd.DataFrame(rows)

emit("(A1) RAW P_LOGO_CHURN by horizon — ALL months",
     metrics_by_h(df, "P_LOGO_CHURN", "ACTUAL_LOGO_CHURN").to_string(index=False))
emit("(A2) RAW P_FULL_RENEWAL by horizon — ALL months",
     metrics_by_h(df, "P_FULL_RENEWAL", "ACTUAL_FULL_RENEW").to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# (B) Fit per-(segment × horizon) isotonic calibrators
# ─────────────────────────────────────────────────────────────────────────────
cutoff = pd.Timestamp("2026-03-01")
train = df[df["RENEWAL_MONTH"] <  cutoff].copy()
test  = df[df["RENEWAL_MONTH"] >= cutoff].copy()
print(f"Train: {len(train):,} rows  |  Test: {len(test):,} rows\n")

def fit_iso_2d(t, p, y):
    cals = {}
    # Global per-horizon fallback
    for h, sub in t.groupby("HORIZON"):
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(sub[p].values, sub[y].values, sample_weight=sub["ATR"].values)
        cals[("__GLOBAL__", h)] = iso
    # Per-segment-horizon
    for (seg, h), sub in t.groupby(["SEGMENT", "HORIZON"]):
        if len(sub) < 300 or sub[y].sum() < 15:
            cals[(seg, h)] = cals[("__GLOBAL__", h)]
            continue
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(sub[p].values, sub[y].values, sample_weight=sub["ATR"].values)
        cals[(seg, h)] = iso
    return cals

print("Fitting per-(segment × horizon) isotonic calibrators...")
cals_logo = fit_iso_2d(train, "P_LOGO_CHURN",   "ACTUAL_LOGO_CHURN")
cals_full = fit_iso_2d(train, "P_FULL_RENEWAL", "ACTUAL_FULL_RENEW")

def apply_2d(d, p, cals, new_col):
    out = np.zeros(len(d))
    for (seg, h), idx in d.groupby(["SEGMENT", "HORIZON"]).groups.items():
        iso = cals.get((seg, h), cals.get(("__GLOBAL__", h)))
        if iso is None:
            iso = list(cals.values())[0]
        out[d.index.get_indexer(idx)] = iso.transform(d.loc[idx, p].values)
    d[new_col] = np.clip(out, 0.0, 1.0)
    return d

test = test.reset_index(drop=True)
test = apply_2d(test, "P_LOGO_CHURN",   cals_logo, "P_LOGO_CAL")
test = apply_2d(test, "P_FULL_RENEWAL", cals_full, "P_FULL_CAL")

# Renormalize so they sum ≤ 1
def renorm(d):
    s = d["P_LOGO_CAL"] + d["P_FULL_CAL"]
    over = s > 1.0
    scale = np.where(over, 1.0 / s.where(s > 0, 1.0), 1.0)
    d["P_LOGO_CAL_R"] = d["P_LOGO_CAL"] * scale
    d["P_FULL_CAL_R"] = d["P_FULL_CAL"] * scale
    d["P_PARTIAL_CAL_R"] = (1.0 - d["P_LOGO_CAL_R"] - d["P_FULL_CAL_R"]).clip(lower=0.0)
    return d
test = renorm(test)


# ─────────────────────────────────────────────────────────────────────────────
# (C) Calibrated metrics by horizon on HOLDOUT
# ─────────────────────────────────────────────────────────────────────────────
emit("(C1) CALIBRATED P_LOGO_CAL_R by horizon — HOLDOUT (Mar-May 2026)",
     metrics_by_h(test, "P_LOGO_CAL_R", "ACTUAL_LOGO_CHURN").to_string(index=False))
emit("(C2) CALIBRATED P_FULL_CAL_R by horizon — HOLDOUT (Mar-May 2026)",
     metrics_by_h(test, "P_FULL_CAL_R", "ACTUAL_FULL_RENEW").to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# (D) Budget-neutrality by horizon
# ─────────────────────────────────────────────────────────────────────────────
test["E_PARTIAL_FILL"] = test["E_PARTIAL_RATE"].fillna(test["E_PARTIAL_RATE"].median())
test["METHOD_B_CAL"] = (
    test["P_FULL_CAL_R"] * 1.0
  + test["P_PARTIAL_CAL_R"] * test["E_PARTIAL_FILL"]
)

rows = []
for h, sub in test.groupby("HORIZON"):
    actu = (sub["ACTUAL_RATE"]            * sub["ATR"]).sum() / sub["ATR"].sum()
    cur  = (sub["PRED_RENEW_RATE_FINAL"]  * sub["ATR"]).sum() / sub["ATR"].sum()
    new  = (sub["METHOD_B_CAL"]           * sub["ATR"]).sum() / sub["ATR"].sum()
    atrM = sub["ATR"].sum() / 1e6
    rows.append({
        "HORIZON": h, "N": len(sub), "ATR_M": round(atrM, 1),
        "ACTUAL_PCT":     round(actu * 100, 2),
        "CURRENT_PCT":    round(cur  * 100, 2),
        "CALIBRATED_PCT": round(new  * 100, 2),
        "CURRENT_GAP_PP": round((cur - actu) * 100, 2),
        "NEW_GAP_PP":     round((new - actu) * 100, 2),
        "CURRENT_$M_OFF": round((cur - actu) * atrM, 1),
        "NEW_$M_OFF":     round((new - actu) * atrM, 1),
    })
emit("(D) BUDGET NEUTRALITY by horizon — HOLDOUT",
     pd.DataFrame(rows).to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# (E) Pickle the per-horizon calibrators + export to JSON for SQL deployment
# ─────────────────────────────────────────────────────────────────────────────
with open(_HERE / "isotonic_calibrators_per_horizon.pkl", "wb") as fp:
    pickle.dump({"P_LOGO_CHURN": cals_logo, "P_FULL_RENEWAL": cals_full}, fp)

def cals_to_json(cals_dict):
    out = {}
    for key, iso in cals_dict.items():
        seg, h = key
        k = f"{seg}|{int(h)}"
        out[k] = {"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()}
    return out

with open(_HERE / "isotonic_calibrators_per_horizon.json", "w") as fp:
    json.dump({
        "P_LOGO_CHURN":   cals_to_json(cals_logo),
        "P_FULL_RENEWAL": cals_to_json(cals_full),
    }, fp, indent=2)

out_path = _HERE / "HORIZON_CALIBRATION_RESULTS.txt"
with open(out_path, "w", encoding="utf-8") as fp:
    fp.write("\n".join(results))
print(f"\nCalibrators saved to: isotonic_calibrators_per_horizon.{{pkl,json}}")
print(f"Results saved to:     {out_path}")
