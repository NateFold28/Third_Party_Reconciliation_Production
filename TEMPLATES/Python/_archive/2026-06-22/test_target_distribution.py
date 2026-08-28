"""
Target distribution analysis: does a nonlinear/threshold approach improve
contract-level magnitude predictions vs the current expected-value model?

Tests three forecast methods on historical closed months:
  A. Current:  RENEWAL_FORECAST = ATR * (1 - CHURN_PCT/100)  [expected value]
  B. Binary50: RENEWAL_FORECAST = 0 if CHURN_PCT >= 50 else ATR  [hard 50% threshold]
  C. OptThresh: find the threshold that minimises contract-level MAE  [calibrated binary]

Metrics at two grains:
  - Contract level: MAE, MAPE, % predictions within 10% of actual
  - Portfolio level (monthly rollup): MAE pp, bias pp
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

conn = get_snowflake_connection(); cur = conn.cursor()
for s in ["USE ROLE STREAMLIT_USER","USE WAREHOUSE REPORTING_WH",
          "USE DATABASE STREAMLIT_APPS","USE SCHEMA DBO"]:
    cur.execute(s)
print("Connected\n")

# Pull all closed months with model predictions
cur.execute("""
    SELECT
        RENEWAL_MONTH, SEGMENT,
        ATR,
        CHURN_PCT,
        COALESCE(ACTUAL_RETAINED_ARR, 0) AS ACTUAL_DOLLARS,
        COALESCE(RETENTION_PCT, 0)        AS MODEL_RETENTION_PCT,
        COALESCE(ML_FORECAST, 0)          AS ML_FORECAST_DOLLARS
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURE = TRUE
      AND IS_MATURED_MONTH = TRUE
      AND COALESCE(ATR, 0) > 1000      -- exclude micro-contracts
      AND CHURN_PCT IS NOT NULL
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    ORDER BY RENEWAL_MONTH, SEGMENT
""")
df = pd.DataFrame(cur.fetchall(), columns=[
    "MONTH","SEGMENT","ATR","CHURN_PCT","ACTUAL","MODEL_RETENTION_PCT","ML_FORECAST"
])
conn.close()
print(f"Loaded {len(df):,} closed contracts across {df['MONTH'].nunique()} months\n")

df["ATR"]        = pd.to_numeric(df["ATR"],        errors="coerce").fillna(0)
df["CHURN_PCT"]  = pd.to_numeric(df["CHURN_PCT"],  errors="coerce").fillna(0)
df["ACTUAL"]     = pd.to_numeric(df["ACTUAL"],     errors="coerce").fillna(0)
df["ML_FORECAST"]= pd.to_numeric(df["ML_FORECAST"],errors="coerce").fillna(0)

# A. Current expected-value model
df["PRED_A"] = df["ATR"] * (1.0 - df["CHURN_PCT"] / 100.0)

# B. Hard 50% binary threshold
df["PRED_B"] = np.where(df["CHURN_PCT"] >= 50, 0.0, df["ATR"])

# ─────────────────────────────────────────────────────────────────────────────
# Calibrate optimal threshold (minimise contract-level MAE)
# ─────────────────────────────────────────────────────────────────────────────
print("Calibrating optimal binary threshold...")
thresholds  = np.arange(10, 90, 2)
mae_by_thresh = []
for t in thresholds:
    pred = np.where(df["CHURN_PCT"] >= t, 0.0, df["ATR"])
    mae  = np.abs(pred - df["ACTUAL"]).mean()
    mae_by_thresh.append((t, mae))
thresh_df   = pd.DataFrame(mae_by_thresh, columns=["THRESHOLD","MAE"])
opt_thresh  = float(thresh_df.loc[thresh_df["MAE"].idxmin(), "THRESHOLD"])
print(f"  Optimal threshold: {opt_thresh:.0f}%  (minimises contract-level MAE)\n")

df["PRED_C"] = np.where(df["CHURN_PCT"] >= opt_thresh, 0.0, df["ATR"])

# ─────────────────────────────────────────────────────────────────────────────
# 1. CONTRACT-LEVEL METRICS
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("1. CONTRACT-LEVEL METRICS")
print("=" * 70)

def contract_metrics(actual, pred, label):
    err       = pred - actual
    mae       = np.abs(err).mean()
    # MAPE only for contracts with actual > 0
    mask      = actual > 100
    mape      = (np.abs(err[mask] / actual[mask]) * 100).mean()
    within10  = (np.abs(err) / (actual + 1) < 0.10).mean() * 100
    # Direction accuracy: did we predict churn direction correctly?
    actually_churned = actual < (0.95 * df["ATR"])   # >5% dollar loss
    pred_churned     = pred < (0.05 * df["ATR"])     # model predicts near-zero
    dir_acc          = (actually_churned == pred_churned).mean() * 100
    return {"Model": label, "Contract MAE ($)": f"${mae:,.0f}",
            "MAPE %": f"{mape:.1f}%", "Within 10% of actual": f"{within10:.1f}%",
            "Churn direction accuracy": f"{dir_acc:.1f}%"}

rows = [
    contract_metrics(df["ACTUAL"], df["PRED_A"], "A. Expected value (current)"),
    contract_metrics(df["ACTUAL"], df["PRED_B"], "B. Binary threshold (50%)"),
    contract_metrics(df["ACTUAL"], df["PRED_C"], f"C. Binary threshold ({opt_thresh:.0f}% optimised)"),
]
print(pd.DataFrame(rows).to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# 2. PORTFOLIO-LEVEL METRICS (monthly rollup — what finance sees)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("2. PORTFOLIO-LEVEL METRICS (monthly rollup — what finance sees)")
print("=" * 70)

monthly = df.groupby("MONTH").agg(
    ATR=("ATR","sum"), ACTUAL=("ACTUAL","sum"),
    PRED_A=("PRED_A","sum"), PRED_B=("PRED_B","sum"), PRED_C=("PRED_C","sum")
).reset_index()
monthly["ACTUAL_RATE"]  = monthly["ACTUAL"]  / monthly["ATR"] * 100
monthly["PRED_A_RATE"]  = monthly["PRED_A"]  / monthly["ATR"] * 100
monthly["PRED_B_RATE"]  = monthly["PRED_B"]  / monthly["ATR"] * 100
monthly["PRED_C_RATE"]  = monthly["PRED_C"]  / monthly["ATR"] * 100

def port_metrics(rate_col, label):
    err   = monthly[rate_col] - monthly["ACTUAL_RATE"]
    return {"Model": label,
            "Bias pp (mean error)": f"{err.mean():+.2f}pp",
            "MAE pp":               f"{err.abs().mean():.2f}pp",
            "Max error pp":         f"{err.abs().max():.1f}pp"}

print(pd.DataFrame([
    port_metrics("PRED_A_RATE", "A. Expected value (current)"),
    port_metrics("PRED_B_RATE", "B. Binary threshold (50%)"),
    port_metrics("PRED_C_RATE", f"C. Binary threshold ({opt_thresh:.0f}% optimised)"),
]).to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# 3. THRESHOLD SENSITIVITY CURVE — show MAE vs threshold
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("3. CONTRACT MAE BY THRESHOLD (shows the optimal cut-point visually)")
print("=" * 70)
print(f"{'Threshold':>10} {'Contract MAE':>15} {'Portfolio Bias':>15}")
for t, mae in mae_by_thresh:
    pred_t  = np.where(df["CHURN_PCT"] >= t, 0.0, df["ATR"])
    mon_t   = df.copy(); mon_t["P"] = pred_t
    bias_pp = (mon_t.groupby("MONTH")["P"].sum() / mon_t.groupby("MONTH")["ATR"].sum() * 100
               - monthly["ACTUAL_RATE"].values).mean()
    marker  = " <-- current" if t == 50 else (" <-- OPTIMAL" if t == opt_thresh else "")
    print(f"{t:>9}%  ${mae:>13,.0f}  {bias_pp:>+14.2f}pp{marker}")

# ─────────────────────────────────────────────────────────────────────────────
# 4. SEGMENT BREAKDOWN — does optimal threshold differ by segment?
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("4. OPTIMAL THRESHOLD BY SEGMENT")
print("=" * 70)
for seg, grp in df.groupby("SEGMENT"):
    seg_maes = []
    for t in thresholds:
        pred = np.where(grp["CHURN_PCT"] >= t, 0.0, grp["ATR"])
        seg_maes.append((t, np.abs(pred - grp["ACTUAL"]).mean()))
    best_t = min(seg_maes, key=lambda x: x[1])
    ev_mae = np.abs(grp["ATR"] * (1 - grp["CHURN_PCT"]/100) - grp["ACTUAL"]).mean()
    print(f"  {seg:<22} optimal threshold={best_t[0]:>3}%  "
          f"binary MAE=${best_t[1]:>10,.0f}  vs  "
          f"expected-value MAE=${ev_mae:>10,.0f}  "
          f"({'binary better' if best_t[1] < ev_mae else 'EV better':>14})")

print("""
CONCLUSION GUIDE:
  - If binary MAE < EV MAE at CONTRACT level → threshold model is better for risk lists
  - If portfolio bias is higher for binary → threshold model hurts finance forecasts
  - If optimal threshold ≠ 50%, the model is miscalibrated (overconfident or underconfident)
  - Ideal: use EV model for portfolio $, use threshold for contract risk tier/direction
""")
