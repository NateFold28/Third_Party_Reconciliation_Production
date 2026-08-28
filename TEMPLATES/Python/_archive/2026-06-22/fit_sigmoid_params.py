"""
fit_sigmoid_params.py
======================
Answers three questions in one run:

  Q1. Is naive linear  (renewal = 100 - CHURN_PCT) better or worse than the
      current model  (ML_FORECAST / ATR)?

  Q2. Does a sigmoid correction improve on the current model?

  Q3. What are the optimal sigmoid (a, b) params for the current data regime?

Method — rolling walk-forward:
  FIT  = all matured months EXCEPT the most recent N_TEST_MONTHS
  TEST = the most recent N_TEST_MONTHS matured months
  This ensures we never use future data to fit, and tests on the current regime.

Output:
  • Console comparison table
  • sigmoid_params.json  — paste SIG_A / SIG_B back into the app constant

Usage:
    cd c:\\Users\\Nate.Fold\\projects
    .venv\\Scripts\\python.exe TEMPLATES\\Python\\fit_sigmoid_params.py
"""

from __future__ import annotations

import json
import sys
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar, minimize
from connection import get_snowflake_connection, fetch_dataframe

pd.set_option("display.float_format", "{:,.3f}".format)
pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)

SEP = "=" * 80

# ── Config ────────────────────────────────────────────────────────────────────
N_TEST_MONTHS = 6       # most-recent matured months held out for honest OOS test
MIN_FIT_ROWS  = 50      # minimum contracts needed in fit window to attempt grid search

# Grid search space — deliberately wide and fine
A_RANGE = np.concatenate([np.arange(2.0, 10.0, 0.25), np.arange(10.0, 30.0, 0.5)])
B_RANGE = np.arange(0.10, 0.95, 0.02)

OUT_JSON = r"c:\Users\Nate.Fold\projects\TEMPLATES\Python\sigmoid_params.json"


def hdr(title: str) -> None:
    print(f"\n{SEP}\n{title}\n{SEP}")


# ── Math ──────────────────────────────────────────────────────────────────────
def sigmoid_renewal(cp_0_1: np.ndarray, a: float, b: float) -> np.ndarray:
    """churn_prob [0,1] → renewal rate [0,100]"""
    z = a * (cp_0_1 - b)
    return 100.0 * (1.0 - 1.0 / (1.0 + np.exp(-np.clip(z, -40.0, 40.0))))


def grid_search(cp: np.ndarray, actual: np.ndarray, w: np.ndarray) -> tuple[float, float, float]:
    """ATR-weighted grid search. Returns (best_a, best_b, best_mae_pp)."""
    cp_01 = cp / 100.0
    wn = w / w.sum()
    best = (999.0, 5.0, 0.50)
    for a in A_RANGE:
        for b in B_RANGE:
            pred = sigmoid_renewal(cp_01, a, b)
            mae  = float(np.sum(wn * np.abs(pred - actual)))
            if mae < best[0]:
                best = (mae, a, b)
    return best[1], best[2], best[0]


def eval_preds(pred: np.ndarray, actual: np.ndarray, w: np.ndarray, label: str) -> dict:
    wn = w / w.sum()
    mae  = float(np.sum(wn * np.abs(pred - actual)))
    bias = float(np.sum(wn * (pred - actual)))
    return {"label": label, "atr_mae_pp": round(mae, 2), "atr_bias_pp": round(bias, 2)}


# ── Pull data ─────────────────────────────────────────────────────────────────
hdr("Pulling matured contracts from V5_SANDBOX_APP_CONTRACT_DETAIL")
conn = get_snowflake_connection()

SQL = """
SELECT
    d.CONTRACT_ID,
    DATE_TRUNC('MONTH', d.RENEWAL_DATE)::DATE          AS RENEWAL_MONTH,
    d.SEGMENT,
    d.ATR,
    COALESCE(d.ACTUAL_RETAINED_ARR, 0)                 AS ACTUAL_RETAINED_ARR,
    d.ML_FORECAST,
    d.CHURN_PCT,
    d.ACTUAL_RETAINED_ARR / NULLIF(d.ATR, 0) * 100.0  AS ACTUAL_RATE_PCT,
    d.ML_FORECAST          / NULLIF(d.ATR, 0) * 100.0  AS PRED_RATE_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
WHERE d.IS_MATURED_MONTH = TRUE
  AND d.ATR > 0
  AND d.ACTUAL_RETAINED_ARR IS NOT NULL
  AND d.CHURN_PCT IS NOT NULL
ORDER BY d.RENEWAL_DATE
"""

df = fetch_dataframe(SQL, conn=conn)
df["RENEWAL_MONTH"] = pd.to_datetime(df["RENEWAL_MONTH"])

print(f"  Rows      : {len(df):,}")
print(f"  Contracts : {df['CONTRACT_ID'].nunique():,}")
print(f"  Range     : {df['RENEWAL_MONTH'].min().date()} → {df['RENEWAL_MONTH'].max().date()}")
print(f"  Avg CHURN_PCT : {df['CHURN_PCT'].mean():.1f}%  (median: {df['CHURN_PCT'].median():.1f}%)")
print(f"  Avg Actual renewal rate: {(df['ACTUAL_RATE_PCT'].mean()):.1f}%")

if df.empty:
    print("No data. Exiting.")
    sys.exit(1)

# ── Walk-forward split ────────────────────────────────────────────────────────
months_sorted = sorted(df["RENEWAL_MONTH"].unique())
if len(months_sorted) < N_TEST_MONTHS + 2:
    print(f"Not enough months ({len(months_sorted)}) for a {N_TEST_MONTHS}-month holdout. Exiting.")
    sys.exit(1)

test_months = months_sorted[-N_TEST_MONTHS:]
fit_months  = months_sorted[:-N_TEST_MONTHS]

df_fit  = df[df["RENEWAL_MONTH"].isin(fit_months)].copy()
df_test = df[df["RENEWAL_MONTH"].isin(test_months)].copy()

print(f"\n  Fit  window: {str(fit_months[0])[:7]} → {str(fit_months[-1])[:7]}  ({len(df_fit):,} rows)")
print(f"  Test window: {str(test_months[0])[:7]} → {str(test_months[-1])[:7]}  ({len(df_test):,} rows)")

# ── Arrays ────────────────────────────────────────────────────────────────────
def arrays(d: pd.DataFrame) -> tuple[np.ndarray, ...]:
    cp     = d["CHURN_PCT"].to_numpy(dtype=float)
    actual = d["ACTUAL_RATE_PCT"].to_numpy(dtype=float)
    pred   = d["PRED_RATE_PCT"].to_numpy(dtype=float)
    atr    = d["ATR"].to_numpy(dtype=float)
    mask   = ~(np.isnan(cp) | np.isnan(actual) | np.isnan(pred))
    return cp[mask], actual[mask], pred[mask], atr[mask]

cp_fit,  act_fit,  pred_fit,  atr_fit  = arrays(df_fit)
cp_test, act_test, pred_test, atr_test = arrays(df_test)

# ── CHURN_PCT distribution (sanity check before fitting) ─────────────────────
hdr("CHURN_PCT Distribution (Test Window — current regime)")
bins   = [0, 20, 30, 40, 50, 60, 70, 80, 100]
labels = ["<20", "20-30", "30-40", "40-50", "50-60", "60-70", "70-80", "80+"]
cats   = pd.cut(cp_test, bins=bins, labels=labels, right=False)
_dist_df = pd.DataFrame({
    "bin":    cats,
    "atr":    atr_test,
    "actual": act_test,
    "pred":   pred_test,
    "churn":  cp_test,
})
dist = (
    _dist_df.groupby("bin", observed=True)
    .agg(
        contracts=("atr", "count"),
        atr_share_pct=("atr", lambda x: x.sum() / atr_test.sum() * 100),
        avg_churn_pct=("churn", "mean"),
        avg_actual=("actual", "mean"),
        avg_pred=("pred", "mean"),
    )
    .reset_index()
)
dist["bias_pp"] = (dist["avg_pred"] - dist["avg_actual"]).round(1)
dist["avg_actual"]    = dist["avg_actual"].round(1)
dist["avg_pred"]      = dist["avg_pred"].round(1)
dist["avg_churn_pct"] = dist["avg_churn_pct"].round(1)
dist["atr_share_pct"] = dist["atr_share_pct"].round(1)
print(dist.to_string(index=False))

# ── Baseline comparison on FIT window ────────────────────────────────────────
hdr("Q1 — Is Linear Better Than Current Model?  (fit window, ATR-weighted)")
lin_fit = np.clip(100.0 - cp_fit, 0.0, 100.0)
rows_fit = [
    eval_preds(pred_fit,  act_fit, atr_fit, "Current model (ML_FORECAST/ATR)"),
    eval_preds(lin_fit,   act_fit, atr_fit, "Naive linear  (100 - CHURN_PCT)"),
]
print(pd.DataFrame(rows_fit).to_string(index=False))

hdr("Q1 — Is Linear Better Than Current Model?  (TEST window, ATR-weighted)")
lin_test = np.clip(100.0 - cp_test, 0.0, 100.0)
rows_test = [
    eval_preds(pred_test, act_test, atr_test, "Current model (ML_FORECAST/ATR)"),
    eval_preds(lin_test,  act_test, atr_test, "Naive linear  (100 - CHURN_PCT)"),
]
print(pd.DataFrame(rows_test).to_string(index=False))

# ── Fit sigmoid on FIT window ─────────────────────────────────────────────────
hdr(f"Q2/Q3 — Grid-search sigmoid on FIT window ({len(df_fit):,} rows)")
print("  Searching a ∈ [2.0, 30.0],  b ∈ [0.10, 0.95] ...")
best_a, best_b, fit_mae = grid_search(cp_fit, act_fit, atr_fit)
print(f"  Best fit params : a={best_a:.2f},  b={best_b:.2f}")
print(f"  Fit MAE         : {fit_mae:.2f}pp")
print(f"  Formula         : renewal = 100 × (1 − σ({best_a:.2f} × (p_churn − {best_b:.2f})))")

# Quick sanity: show what this sigmoid predicts at key churn levels
print("\n  Sigmoid curve at key CHURN_PCT values:")
for cp_val in [10, 20, 28, 35, 40, 50, 60, 70, 80]:
    r = sigmoid_renewal(np.array([cp_val / 100.0]), best_a, best_b)[0]
    print(f"    CHURN_PCT={cp_val:2d}% → renewal rate = {r:.1f}%")

# ── OOS evaluation on TEST window ────────────────────────────────────────────
hdr("Q2 — Does Sigmoid Beat Current Model?  (TEST window, OOS)")
sig_pred_test = sigmoid_renewal(cp_test / 100.0, best_a, best_b)

rows_oos = [
    eval_preds(pred_test,     act_test, atr_test, "Current model"),
    eval_preds(lin_test,      act_test, atr_test, "Naive linear"),
    eval_preds(sig_pred_test, act_test, atr_test, f"Sigmoid (a={best_a:.1f}, b={best_b:.2f})"),
]
print(pd.DataFrame(rows_oos).to_string(index=False))

# Verdict
cur_oos_mae = rows_oos[0]["atr_mae_pp"]
sig_oos_mae = rows_oos[2]["atr_mae_pp"]
lin_oos_mae = rows_oos[1]["atr_mae_pp"]
delta_vs_cur = sig_oos_mae - cur_oos_mae
delta_vs_lin = sig_oos_mae - lin_oos_mae

print(f"\n  VERDICT:")
print(f"    Sigmoid vs current model: {delta_vs_cur:+.2f}pp MAE  ({'WORSE' if delta_vs_cur > 0 else 'BETTER'})")
print(f"    Sigmoid vs naive linear : {delta_vs_lin:+.2f}pp MAE  ({'WORSE' if delta_vs_lin > 0 else 'BETTER'})")
best_method = min(rows_oos, key=lambda r: r["atr_mae_pp"])["label"]
print(f"    Best method on OOS test : {best_method}")

# ── Per-segment analysis ──────────────────────────────────────────────────────
hdr("Per-Segment OOS Comparison")
seg_rows = []
for seg in sorted(df_test["SEGMENT"].dropna().unique()):
    seg_mask = (df_test["SEGMENT"] == seg).to_numpy()
    if seg_mask.sum() < 20:
        continue
    cp_s    = cp_test[seg_mask]
    act_s   = act_test[seg_mask]
    pred_s  = pred_test[seg_mask]
    atr_s   = atr_test[seg_mask]
    lin_s   = np.clip(100.0 - cp_s, 0.0, 100.0)
    sig_s   = sigmoid_renewal(cp_s / 100.0, best_a, best_b)

    cur_mae = eval_preds(pred_s, act_s, atr_s, "")["atr_mae_pp"]
    lin_mae = eval_preds(lin_s,  act_s, atr_s, "")["atr_mae_pp"]
    sig_mae = eval_preds(sig_s,  act_s, atr_s, "")["atr_mae_pp"]
    seg_rows.append({
        "segment":       seg,
        "n":             int(seg_mask.sum()),
        "cur_mae":       cur_mae,
        "lin_mae":       lin_mae,
        "sig_mae":       sig_mae,
        "sig_vs_cur_pp": round(sig_mae - cur_mae, 2),
        "best":          min([("current", cur_mae), ("linear", lin_mae), ("sigmoid", sig_mae)], key=lambda x: x[1])[0],
    })

print(pd.DataFrame(seg_rows).to_string(index=False))

# ── Save params ───────────────────────────────────────────────────────────────
hdr("Output")
params = {
    "run_date":       pd.Timestamp.now().strftime("%Y-%m-%d"),
    "fit_window":     f"{str(fit_months[0])[:7]} → {str(fit_months[-1])[:7]}",
    "test_window":    f"{str(test_months[0])[:7]} → {str(test_months[-1])[:7]}",
    "n_fit":          int(len(df_fit)),
    "n_test":         int(len(df_test)),
    "portfolio_sigmoid": {
        "a":           best_a,
        "b":           best_b,
        "fit_mae_pp":  round(fit_mae, 3),
        "oos_mae_pp":  round(sig_oos_mae, 3),
    },
    "oos_comparison": {
        "current_model_mae_pp": round(cur_oos_mae, 3),
        "naive_linear_mae_pp":  round(lin_oos_mae, 3),
        "sigmoid_mae_pp":       round(sig_oos_mae, 3),
        "sigmoid_vs_current_pp": round(delta_vs_cur, 3),
    },
    "segment_results": seg_rows,
}

with open(OUT_JSON, "w") as f:
    json.dump(params, f, indent=2)
print(f"  Saved: {OUT_JSON}")

print(f"""
  ┌─────────────────────────────────────────────────────────────┐
  │  PASTE INTO APP (assemble_frame sigmoid config):            │
  │    _SIG_A: float = {best_a:<5.1f}  # slope                       │
  │    _SIG_B: float = {best_b:<5.2f}  # inflection (churn → 50% renewal) │
  └─────────────────────────────────────────────────────────────┘
""")
