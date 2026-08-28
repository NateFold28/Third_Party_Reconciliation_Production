"""
Walk-forward validation of per-segment binary thresholds.

Proves (or disproves) that thresholds calibrated on past months
actually improve predictions on future months — not just in-sample.

Approach:
  - Sort all closed months chronologically
  - Walk-forward: for each test window, calibrate thresholds on all
    PRIOR months, then evaluate on the test window
  - Compare EV vs per-segment binary on held-out months only
  - Final section: show what the Jul-Sep 2026 watchlist would look like
    under each method (churn/renew call + implied dollar)
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

# ── Load closed months ────────────────────────────────────────────────────────
cur.execute("""
    SELECT RENEWAL_MONTH, SEGMENT, ATR,
           CHURN_PCT,
           COALESCE(ACTUAL_RETAINED_ARR, 0) AS ACTUAL,
           COALESCE(ML_FORECAST, 0)          AS ML_FORECAST
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURE = TRUE AND IS_MATURED_MONTH = TRUE
      AND COALESCE(ATR, 0) > 1000
      AND CHURN_PCT IS NOT NULL
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    ORDER BY RENEWAL_MONTH
""")
df = pd.DataFrame(cur.fetchall(), columns=[
    "MONTH","SEGMENT","ATR","CHURN_PCT","ACTUAL","ML_FORECAST"])
df["ATR"]       = pd.to_numeric(df["ATR"],      errors="coerce").fillna(0)
df["CHURN_PCT"] = pd.to_numeric(df["CHURN_PCT"],errors="coerce").fillna(0)
df["ACTUAL"]    = pd.to_numeric(df["ACTUAL"],   errors="coerce").fillna(0)

# ── Load forward contracts for Jul-Sep 2026 watchlist ────────────────────────
cur.execute("""
    SELECT CONTRACT_ID, PARTNER, SEGMENT, RENEWAL_MONTH,
           ROUND(ATR,0) AS ATR, CHURN_PCT,
           ROUND(ML_FORECAST,0) AS ML_FORECAST_EV,
           CONTRACT_RISK_TIER_RELATIVE AS TIER,
           ROUND(AT_RISK_DOLLARS,0) AS LOSS_EV
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-09-01'
      AND IS_MATURE = FALSE
      AND COALESCE(AT_RISK_DOLLARS,0) > 0
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    ORDER BY AT_RISK_DOLLARS DESC
    LIMIT 50
""")
wl = pd.DataFrame(cur.fetchall(), columns=[
    "CONTRACT_ID","PARTNER","SEGMENT","RENEWAL_MONTH",
    "ATR","CHURN_PCT","ML_FORECAST_EV","TIER","LOSS_EV"])
wl["ATR"]       = pd.to_numeric(wl["ATR"],      errors="coerce").fillna(0)
wl["CHURN_PCT"] = pd.to_numeric(wl["CHURN_PCT"],errors="coerce").fillna(0)
conn.close()

months  = sorted(df["MONTH"].unique())
n       = len(months)
segs    = [s for s in df["SEGMENT"].unique() if s != "Unclassified"]
thresh_range = np.arange(10, 92, 2)
print(f"Loaded {len(df):,} contracts across {n} months\n")


# ── Walk-forward calibration ─────────────────────────────────────────────────
print("=" * 70)
print("WALK-FORWARD VALIDATION (train on past, test on held-out months)")
print("=" * 70)

MIN_TRAIN = 12   # need at least 12 months of history before testing
results = []

for i, test_month in enumerate(months):
    if i < MIN_TRAIN:
        continue
    train = df[df["MONTH"] < test_month]
    test  = df[df["MONTH"] == test_month]
    if len(test) < 50:
        continue

    # Calibrate per-segment threshold on train
    seg_thresh = {}
    for seg in segs:
        seg_train = train[train["SEGMENT"] == seg]
        if len(seg_train) < 100:
            seg_thresh[seg] = 66.0   # fallback to near-zero-bias threshold
            continue
        best = min(thresh_range,
                   key=lambda t: np.abs(
                       np.where(seg_train["CHURN_PCT"] >= t, 0.0, seg_train["ATR"])
                       - seg_train["ACTUAL"]).mean())
        seg_thresh[seg] = float(best)

    # Evaluate on test month
    test = test.copy()
    test["PRED_EV"]  = test["ATR"] * (1.0 - test["CHURN_PCT"] / 100.0)
    test["THRESH"]   = test["SEGMENT"].map(seg_thresh).fillna(66.0)
    test["PRED_BIN"] = np.where(test["CHURN_PCT"] >= test["THRESH"], 0.0, test["ATR"])

    atr = test["ATR"].sum()
    if atr == 0:
        continue
    act_rate  = test["ACTUAL"].sum()  / atr * 100
    ev_rate   = test["PRED_EV"].sum() / atr * 100
    bin_rate  = test["PRED_BIN"].sum()/ atr * 100
    ev_bias   = ev_rate  - act_rate
    bin_bias  = bin_rate - act_rate
    ev_mae    = np.abs(test["PRED_EV"]  - test["ACTUAL"]).mean()
    bin_mae   = np.abs(test["PRED_BIN"] - test["ACTUAL"]).mean()
    results.append({
        "MONTH": test_month, "N": len(test),
        "EV_BIAS_PP": round(ev_bias,2), "BIN_BIAS_PP": round(bin_bias,2),
        "EV_MAE": round(ev_mae,0), "BIN_MAE": round(bin_mae,0),
        "THRESH_USED": {s: seg_thresh.get(s,66) for s in segs}
    })

res_df = pd.DataFrame(results)
print(f"\nTest months evaluated: {len(res_df)}  (held out, thresholds trained on prior months only)\n")

# Summary
print(f"{'Metric':<35} {'Expected Value':>16} {'Per-Seg Binary':>16} {'Winner':>10}")
print("-" * 80)
ev_bias_mean  = res_df["EV_BIAS_PP"].mean()
bin_bias_mean = res_df["BIN_BIAS_PP"].mean()
ev_mae_mean   = res_df["EV_MAE"].mean()
bin_mae_mean  = res_df["BIN_MAE"].mean()
ev_bias_std   = res_df["EV_BIAS_PP"].std()
bin_bias_std  = res_df["BIN_BIAS_PP"].std()

rows = [
    ("Portfolio bias pp (mean)", f"{ev_bias_mean:+.2f}pp", f"{bin_bias_mean:+.2f}pp",
     "Binary" if abs(bin_bias_mean) < abs(ev_bias_mean) else "EV"),
    ("Portfolio bias pp (std)", f"{ev_bias_std:.2f}pp", f"{bin_bias_std:.2f}pp",
     "Binary" if bin_bias_std < ev_bias_std else "EV"),
    ("Contract MAE $ (mean)", f"${ev_mae_mean:,.0f}", f"${bin_mae_mean:,.0f}",
     "Binary" if bin_mae_mean < ev_mae_mean else "EV"),
    ("Months binary beats EV (contract MAE)",
     f"{(res_df['BIN_MAE'] < res_df['EV_MAE']).sum()} / {len(res_df)}",
     f"{(res_df['EV_MAE'] < res_df['BIN_MAE']).sum()} / {len(res_df)}", ""),
]
for label, ev_val, bin_val, winner in rows:
    print(f"  {label:<33} {ev_val:>16} {bin_val:>16} {winner:>10}")

# Monthly bias detail (last 12 test months)
print(f"\n{'Month':<12} {'Act Rate%':>9} {'EV Bias':>9} {'Bin Bias':>9} {'Better':>8}")
for _, r in res_df.tail(12).iterrows():
    better = "Binary" if abs(r["BIN_BIAS_PP"]) < abs(r["EV_BIAS_PP"]) else "EV"
    print(f"  {str(r['MONTH'])[:7]:<10} "
          f"{'':>9} {r['EV_BIAS_PP']:>+8.2f}pp {r['BIN_BIAS_PP']:>+8.2f}pp {better:>8}")

# Show stable threshold estimates from last 6 train windows
print("\n--- Per-segment thresholds (last 6 calibrations) ---")
thresh_history = pd.DataFrame([
    {**{"MONTH": r["MONTH"]}, **r["THRESH_USED"]}
    for r in results[-6:]
])
print(thresh_history.to_string(index=False))


# ── Apply calibrated thresholds to Jul-Sep 2026 watchlist ───────────────────
print("\n" + "=" * 70)
print("WATCHLIST: Expected-value vs Binary call per contract (Jul-Sep 2026)")
print("=" * 70)

# Use thresholds trained on ALL closed months (best estimate going into forward)
final_thresh = {}
for seg in segs:
    seg_data = df[df["SEGMENT"] == seg]
    if len(seg_data) < 100:
        final_thresh[seg] = 66.0
        continue
    best = min(thresh_range,
               key=lambda t, sd=seg_data: np.abs(
                   np.where(sd["CHURN_PCT"] >= t, 0.0, sd["ATR"])
                   - sd["ACTUAL"]).mean())
    final_thresh[seg] = float(best)

print("\nFinal per-segment thresholds (trained on all closed months):")
for s, t in sorted(final_thresh.items()):
    print(f"  {s:<22} → call churn if CHURN_PCT >= {t:.0f}%")

wl["BIN_THRESH"]    = wl["SEGMENT"].map(final_thresh).fillna(66.0)
wl["BIN_CALL"]      = np.where(wl["CHURN_PCT"] >= wl["BIN_THRESH"], "CHURN", "RENEW")
wl["BIN_FORECAST"]  = np.where(wl["BIN_CALL"] == "CHURN", 0.0, wl["ATR"])
wl["LOSS_BINARY"]   = np.where(wl["BIN_CALL"] == "CHURN", wl["ATR"], 0.0)
wl["EV_vs_BIN_$"]   = (wl["ML_FORECAST_EV"] - wl["BIN_FORECAST"]).round(0)

print(f"\n{'PARTNER':<40} {'SEG':>10} {'CHURN%':>7} {'THRESH':>7} "
      f"{'CALL':>6} {'EV Fcst$':>10} {'Bin Fcst$':>10} {'Diff$':>10}")
print("-" * 110)
for _, r in wl.head(30).iterrows():
    print(f"  {str(r['PARTNER'])[:38]:<38} {r['SEGMENT'][:9]:>10} "
          f"{r['CHURN_PCT']:>6.1f}% {r['BIN_THRESH']:>6.0f}% "
          f"{r['BIN_CALL']:>6} "
          f"${r['ML_FORECAST_EV']:>9,.0f} ${r['BIN_FORECAST']:>9,.0f} "
          f"${r['EV_vs_BIN_$']:>+9,.0f}")

print(f"\n  Portfolio-level totals (top-50 watchlist):")
print(f"    EV total forecast:     ${wl['ML_FORECAST_EV'].sum():>12,.0f}")
print(f"    Binary total forecast: ${wl['BIN_FORECAST'].sum():>12,.0f}")
print(f"    Implied binary loss:   ${wl['LOSS_BINARY'].sum():>12,.0f}")
print(f"    EV implied loss:       ${wl['LOSS_EV'].sum():>12,.0f}")
n_churn = (wl["BIN_CALL"] == "CHURN").sum()
n_renew = (wl["BIN_CALL"] == "RENEW").sum()
print(f"\n    Binary calls: {n_churn} CHURN, {n_renew} RENEW out of {len(wl)} contracts")
print(f"    Contracts where calls disagree by >$100K: "
      f"{(wl['EV_vs_BIN_$'].abs() > 100_000).sum()}")
