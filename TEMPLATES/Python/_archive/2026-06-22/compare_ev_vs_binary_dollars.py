"""
Dollar-level comparison: Expected Value vs Per-Segment Binary forecasts.

For each closed month (last 24), shows:
  - Actual renewal dollars
  - EV forecast dollars
  - Binary forecast dollars (per-segment thresholds calibrated on all prior months)
  - Which is closer to actual

Purpose: give concrete evidence for the board / finance decision before
         committing to any change in the app.
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

# ── Load all closed contracts with ATR, actual, and CHURN_PCT ────────────────
cur.execute("""
    SELECT RENEWAL_MONTH, SEGMENT, ATR,
           CHURN_PCT,
           COALESCE(ACTUAL_RETAINED_ARR, 0)  AS ACTUAL,
           COALESCE(ML_FORECAST,          0)  AS EV_FORECAST
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURE = TRUE AND IS_MATURED_MONTH = TRUE
      AND COALESCE(ATR, 0) > 1000
      AND CHURN_PCT IS NOT NULL
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    ORDER BY RENEWAL_MONTH
""")
df = pd.DataFrame(cur.fetchall(), columns=[
    "MONTH","SEGMENT","ATR","CHURN_PCT","ACTUAL","EV_FORECAST"])
conn.close()

for c in ["ATR","CHURN_PCT","ACTUAL","EV_FORECAST"]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

months = sorted(df["MONTH"].unique())
segs   = sorted(df["SEGMENT"].unique())
thresh_range = np.arange(10, 92, 2)
MIN_TRAIN = 12
MIN_SEG_ROWS = 100
FALLBACK_THRESH = 66.0

print(f"Loaded {len(df):,} contracts across {len(months)} months\n")


# ── Walk-forward: calibrate on prior months, evaluate on test month ──────────
def best_threshold(train, seg):
    sub = train[train["SEGMENT"] == seg]
    if len(sub) < MIN_SEG_ROWS:
        return FALLBACK_THRESH
    best_t, best_mae = FALLBACK_THRESH, np.inf
    for t in thresh_range:
        pred = np.where(sub["CHURN_PCT"] >= t, 0.0, sub["ATR"])
        mae  = np.abs(pred - sub["ACTUAL"]).mean()
        if mae < best_mae:
            best_mae = mae
            best_t   = t
    return best_t

records = []
for i, test_month in enumerate(months):
    if i < MIN_TRAIN:
        continue
    train  = df[df["MONTH"] < test_month]
    test   = df[df["MONTH"] == test_month].copy()

    # per-segment thresholds on training data
    thresholds = {s: best_threshold(train, s) for s in segs}

    # binary forecast for each contract in test month
    test["BIN_FORECAST"] = test.apply(
        lambda r: 0.0 if r["CHURN_PCT"] >= thresholds.get(r["SEGMENT"], FALLBACK_THRESH)
                  else r["ATR"], axis=1)

    # portfolio totals
    actual_total   = test["ACTUAL"].sum()
    ev_total       = test["EV_FORECAST"].sum()
    bin_total      = test["BIN_FORECAST"].sum()
    atr_total      = test["ATR"].sum()

    actual_rate    = actual_total / atr_total * 100 if atr_total else 0
    ev_rate        = ev_total     / atr_total * 100 if atr_total else 0
    bin_rate       = bin_total    / atr_total * 100 if atr_total else 0

    ev_err_dollar  = ev_total  - actual_total
    bin_err_dollar = bin_total - actual_total

    records.append({
        "MONTH":          str(test_month)[:7],
        "ATR_TOTAL":      atr_total,
        "ACTUAL_$":       actual_total,
        "EV_FCST_$":      ev_total,
        "BIN_FCST_$":     bin_total,
        "ACTUAL_RATE%":   actual_rate,
        "EV_RATE%":       ev_rate,
        "BIN_RATE%":      bin_rate,
        "EV_ERR_$":       ev_err_dollar,
        "BIN_ERR_$":      bin_err_dollar,
        "EV_ABS_ERR_$":   abs(ev_err_dollar),
        "BIN_ABS_ERR_$":  abs(bin_err_dollar),
        "WINNER":         "Binary" if abs(bin_err_dollar) < abs(ev_err_dollar) else "EV",
    })

res = pd.DataFrame(records)

# ── Last 24 months ────────────────────────────────────────────────────────────
last24 = res.tail(24).copy()

print("=" * 90)
print("DOLLAR-LEVEL FORECAST ACCURACY — LAST 24 CLOSED MONTHS")
print("=" * 90)
print(f"{'Month':<10}  {'Actual $':>14}  {'EV Fcst $':>14}  {'Bin Fcst $':>14}  "
      f"{'EV Err $':>12}  {'Bin Err $':>12}  {'Winner':<8}")
print("-" * 90)
for _, r in last24.iterrows():
    print(f"{r['MONTH']:<10}  "
          f"${r['ACTUAL_$']:>13,.0f}  "
          f"${r['EV_FCST_$']:>13,.0f}  "
          f"${r['BIN_FCST_$']:>13,.0f}  "
          f"{r['EV_ERR_$']:>+12,.0f}  "
          f"{r['BIN_ERR_$']:>+12,.0f}  "
          f"{r['WINNER']:<8}")

print("-" * 90)
print(f"{'MEAN'::<10}  "
      f"${last24['ACTUAL_$'].mean():>13,.0f}  "
      f"${last24['EV_FCST_$'].mean():>13,.0f}  "
      f"${last24['BIN_FCST_$'].mean():>13,.0f}  "
      f"{last24['EV_ERR_$'].mean():>+12,.0f}  "
      f"{last24['BIN_ERR_$'].mean():>+12,.0f}  ")

print()
print("=" * 70)
print("SUMMARY STATISTICS (last 24 months)")
print("=" * 70)
n24 = len(last24)
ev_wins  = (last24["WINNER"] == "EV").sum()
bin_wins = (last24["WINNER"] == "Binary").sum()

print(f"  Binary wins (lower absolute $ error):  {bin_wins:>3} / {n24} months")
print(f"  EV wins:                                {ev_wins:>3} / {n24} months")
print()
print(f"  Mean absolute $ error  — EV:     ${last24['EV_ABS_ERR_$'].mean():>12,.0f}")
print(f"  Mean absolute $ error  — Binary: ${last24['BIN_ABS_ERR_$'].mean():>12,.0f}")
print(f"  Improvement:                       {(1 - last24['BIN_ABS_ERR_$'].mean() / last24['EV_ABS_ERR_$'].mean())*100:>.1f}%")
print()
print(f"  Mean $ error (signed)  — EV:     ${last24['EV_ERR_$'].mean():>+12,.0f}  (+ = over-forecast, - = under-forecast)")
print(f"  Mean $ error (signed)  — Binary: ${last24['BIN_ERR_$'].mean():>+12,.0f}")
print()
print(f"  Typical monthly ATR:             ${last24['ATR_TOTAL'].mean():>12,.0f}")
ev_bias_pct   = last24['EV_ERR_$'].mean()   / last24['ATR_TOTAL'].mean() * 100
bin_bias_pct  = last24['BIN_ERR_$'].mean()  / last24['ATR_TOTAL'].mean() * 100
print(f"  EV systematic bias:               {ev_bias_pct:>+.2f}% of ATR")
print(f"  Binary systematic bias:           {bin_bias_pct:>+.2f}% of ATR")

print()
print("=" * 70)
print("RENEWAL RATE % ACCURACY (last 24 months)")
print("=" * 70)
print(f"  Mean actual renewal rate:  {last24['ACTUAL_RATE%'].mean():>6.2f}%")
print(f"  Mean EV renewal rate:      {last24['EV_RATE%'].mean():>6.2f}%  (bias {last24['EV_RATE%'].mean()-last24['ACTUAL_RATE%'].mean():>+.2f}pp)")
print(f"  Mean binary renewal rate:  {last24['BIN_RATE%'].mean():>6.2f}%  (bias {last24['BIN_RATE%'].mean()-last24['ACTUAL_RATE%'].mean():>+.2f}pp)")

print()
print("=" * 70)
print("RECOMMENDATION MATRIX")
print("=" * 70)
ev_mae   = last24['EV_ABS_ERR_$'].mean()
bin_mae  = last24['BIN_ABS_ERR_$'].mean()
ev_bias  = abs(last24['EV_ERR_$'].mean())
bin_bias = abs(last24['BIN_ERR_$'].mean())

print(f"\n  ┌────────────────────────────────┬────────────────┬────────────────┐")
print(f"  │ Metric                         │ Expected Value │ Binary Thresh  │")
print(f"  ├────────────────────────────────┼────────────────┼────────────────┤")
print(f"  │ Mean absolute $ error (MAE)    │ ${ev_mae/1e6:>8.2f}M    │ ${bin_mae/1e6:>8.2f}M    │")
print(f"  │ Systematic bias $              │ ${ev_bias/1e6:>8.2f}M    │ ${bin_bias/1e6:>8.2f}M    │")
print(f"  │ Renewal rate bias pp           │ {last24['EV_RATE%'].mean()-last24['ACTUAL_RATE%'].mean():>+8.2f}pp    │ {last24['BIN_RATE%'].mean()-last24['ACTUAL_RATE%'].mean():>+8.2f}pp    │")
print(f"  │ Months with lower $ error      │ {ev_wins:>8} / {n24}  │ {bin_wins:>8} / {n24}  │")
print(f"  └────────────────────────────────┴────────────────┴────────────────┘")
print()
