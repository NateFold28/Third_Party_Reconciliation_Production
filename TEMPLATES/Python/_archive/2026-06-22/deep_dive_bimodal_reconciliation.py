"""
DEEP DIVE: Bimodal vs Current — multi-level reconciliation test
================================================================
Tests whether the bimodal decision can REPLACE the current scalar forecast
at every aggregation level finance cares about:

  - Per contract     (MAE, median error)
  - Per segment      (bias, RMSE)
  - Per month        (bias vs actual, vs current method)
  - Per segment×month (the cells finance actually reviews)
  - Portfolio total  (the board-facing number)

For each level we compare:
  CURRENT  = PRED_RENEW_RATE_FINAL * ATR   (what's in ML_FORECAST today)
  BIMODAL  = bimodal decision $             (proposed replacement)

VERDICT criterion: bimodal must be BETTER OR EQUAL at every level finance
reviews. If it's worse at any meaningful aggregation, we don't ship it.
"""
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from connection import fetch_dataframe

PREDS = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
FEAT  = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE"
KNOTS = "STREAMLIT_APPS.DBO.V5_CALIBRATION_KNOTS"

pd.set_option("display.float_format", "{:.3f}".format)
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)


# ── Load data + apply calibration ────────────────────────────────────────────
print("Loading validation data + calibration knots...")
df = fetch_dataframe(f"""
WITH latest_run AS (SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT='VALIDATION')
SELECT
    p.CONTRACT_ID_UFR AS CONTRACT_ID,
    DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
    p.HORIZON, p.SEGMENT, p.ATR,
    p.P_LOGO_CHURN, p.P_FULL_RENEWAL, p.P_DOLLAR_CHURN, p.E_PARTIAL_RATE,
    p.PRED_RENEW_RATE_FINAL,
    f.TARGET__RENEWAL_RATE AS ACTUAL_RATE
FROM {PREDS} p
JOIN latest_run lr ON p.RUN_ID = lr.RUN_ID
JOIN {FEAT} f
    ON p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
    AND DATE_TRUNC('MONTH', p.RENEWAL_MONTH) = DATE_TRUNC('MONTH', f.RENEWAL_MONTH)
    AND p.HORIZON = f.HORIZON AND p.SPLIT = f.SPLIT
WHERE p.SPLIT='VALIDATION' AND f.COHORT='MATURED'
  AND p.ATR > 0 AND f.TARGET__RENEWAL_RATE IS NOT NULL
""")
for c in ("P_LOGO_CHURN", "P_FULL_RENEWAL", "P_DOLLAR_CHURN", "E_PARTIAL_RATE",
          "PRED_RENEW_RATE_FINAL", "ATR", "ACTUAL_RATE"):
    df[c] = pd.to_numeric(df[c], errors="coerce")
df["HORIZON"] = df["HORIZON"].astype(int)
df = df.dropna(subset=["P_LOGO_CHURN", "P_FULL_RENEWAL", "ATR", "ACTUAL_RATE"]).reset_index(drop=True)
df["ACTUAL_DOLLAR"] = df["ACTUAL_RATE"] * df["ATR"]
print(f"  Loaded {len(df):,} rows / {df['SEGMENT'].nunique()} segments / "
      f"{df['RENEWAL_MONTH'].nunique()} months / "
      f"ATR ${df['ATR'].sum()/1e6:.1f}M / Actual ${df['ACTUAL_DOLLAR'].sum()/1e6:.1f}M\n")

# Load + apply calibration knots
knot_df = fetch_dataframe(f"SELECT MODEL_TARGET, SEGMENT, HORIZON, KNOT_X_JSON, KNOT_Y_JSON FROM {KNOTS}")
knots = {(str(r["MODEL_TARGET"]), str(r["SEGMENT"]), int(r["HORIZON"])):
         {"x": json.loads(r["KNOT_X_JSON"]), "y": json.loads(r["KNOT_Y_JSON"])}
         for _, r in knot_df.iterrows()}

p_logo_cal = df["P_LOGO_CHURN"].values.copy().astype(float)
p_full_cal = df["P_FULL_RENEWAL"].values.copy().astype(float)
for (seg, h), grp in df.groupby(["SEGMENT", "HORIZON"]):
    h_int = int(min(max(h, 0), 6))
    mask  = df.index.get_indexer(grp.index)
    for tgt, src_col, arr in [("P_LOGO_CHURN", "P_LOGO_CHURN", p_logo_cal),
                               ("P_FULL_RENEWAL", "P_FULL_RENEWAL", p_full_cal)]:
        k = knots.get((tgt, seg, h_int))
        if k:
            arr[mask] = np.interp(grp[src_col].values.astype(float), k["x"], k["y"]).clip(0, 1)
df["P_LOGO_CAL"] = p_logo_cal
df["P_FULL_CAL"] = p_full_cal
tot = df["P_LOGO_CAL"] + df["P_FULL_CAL"]
scale = np.where(tot > 1.0, 1.0 / np.where(tot > 0, tot, 1.0), 1.0)
df["P_LOGO_CAL"] = (df["P_LOGO_CAL"] * scale).clip(0, 1)
df["P_FULL_CAL"] = (df["P_FULL_CAL"] * scale).clip(0, 1)
df["P_PARTIAL_CAL"] = (1.0 - df["P_LOGO_CAL"] - df["P_FULL_CAL"]).clip(0, 1)


# ── Define candidate forecasting strategies ──────────────────────────────────
ATR = df["ATR"].values
A = df["PRED_RENEW_RATE_FINAL"].clip(0, 1) * ATR

# B: pure calibrated expected value (no thresholding)
ep = df["E_PARTIAL_RATE"].fillna(0.5).values.astype(float)
B = (df["P_FULL_CAL"] * 1.0 + df["P_PARTIAL_CAL"] * ep + df["P_LOGO_CAL"] * 0.0) * ATR

# C: Bimodal decision (current proposal)
p_full_v = df["P_FULL_CAL"].values
p_logo_v = df["P_LOGO_CAL"].values
C = np.full(len(df), np.nan)
m_renew  = p_full_v >= 0.70
m_churn  = (p_logo_v >= 0.40) & ~m_renew
m_likely = (p_full_v >= 0.45) & ~m_renew & ~m_churn
m_risk   = (p_logo_v >= 0.20) & ~m_renew & ~m_churn & ~m_likely
m_other  = ~m_renew & ~m_churn & ~m_likely & ~m_risk
C[m_renew]  = ATR[m_renew]
C[m_churn]  = 0.0
C[m_likely] = ATR[m_likely] * 0.95
C[m_risk]   = ATR[m_risk]   * p_full_v[m_risk]
C[m_other]  = ATR[m_other]  * p_full_v[m_other]

df["DOL_A"] = A
df["DOL_B"] = B
df["DOL_C"] = C


# ── Helper ──────────────────────────────────────────────────────────────────
def _stats(pred, actual, atr, label, by=None):
    """Return MAE in $ + pp, plus aggregate bias in $ + pp."""
    rate_pred = pred / np.where(atr > 0, atr, 1)
    rate_act  = actual / np.where(atr > 0, atr, 1)
    return {
        "method":          label,
        "rows":            len(pred),
        "MAE_pp":          round(np.abs(rate_pred - rate_act).mean() * 100, 2),
        "MAE_$":           int(np.abs(pred - actual).mean()),
        "median_err_$":    int(np.abs(pred - actual).median() if hasattr(np.abs(pred-actual), 'median') else np.median(np.abs(pred-actual))),
        "agg_off_$M":      round((pred.sum() - actual.sum()) / 1e6, 2),
        "agg_off_pp":      round((pred.sum() / atr.sum() - actual.sum() / atr.sum()) * 100, 2),
    }


# ── Level 1: per contract ────────────────────────────────────────────────────
print("=" * 100)
print("LEVEL 1 — PER CONTRACT (each row = one contract)")
print("=" * 100)
lvl1 = pd.DataFrame([
    _stats(df["DOL_A"].values, df["ACTUAL_DOLLAR"].values, ATR, "A. CURRENT (PRED_RENEW_RATE_FINAL)"),
    _stats(df["DOL_B"].values, df["ACTUAL_DOLLAR"].values, ATR, "B. CALIBRATED EXPECTED VAL"),
    _stats(df["DOL_C"].values, df["ACTUAL_DOLLAR"].values, ATR, "C. BIMODAL DECISION"),
])
print(lvl1.to_string(index=False))


# ── Level 2: per segment ─────────────────────────────────────────────────────
print("\n" + "=" * 100)
print("LEVEL 2 — PER SEGMENT (sum across all months; how each segment rolls up)")
print("=" * 100)
seg_rows = []
for seg, grp in df.groupby("SEGMENT", observed=True):
    actual_M = grp["ACTUAL_DOLLAR"].sum() / 1e6
    atr_M    = grp["ATR"].sum() / 1e6
    seg_rows.append({
        "segment": seg,
        "N":       len(grp),
        "ATR_$M":  round(atr_M, 1),
        "actual_$M": round(actual_M, 2),
        "A_off_$M": round((grp["DOL_A"].sum() - grp["ACTUAL_DOLLAR"].sum()) / 1e6, 2),
        "A_off_pp": round((grp["DOL_A"].sum() / grp["ATR"].sum() - grp["ACTUAL_DOLLAR"].sum() / grp["ATR"].sum()) * 100, 2),
        "B_off_$M": round((grp["DOL_B"].sum() - grp["ACTUAL_DOLLAR"].sum()) / 1e6, 2),
        "B_off_pp": round((grp["DOL_B"].sum() / grp["ATR"].sum() - grp["ACTUAL_DOLLAR"].sum() / grp["ATR"].sum()) * 100, 2),
        "C_off_$M": round((grp["DOL_C"].sum() - grp["ACTUAL_DOLLAR"].sum()) / 1e6, 2),
        "C_off_pp": round((grp["DOL_C"].sum() / grp["ATR"].sum() - grp["ACTUAL_DOLLAR"].sum() / grp["ATR"].sum()) * 100, 2),
    })
lvl2 = pd.DataFrame(seg_rows).sort_values("ATR_$M", ascending=False)
print(lvl2.to_string(index=False))


# ── Level 3: per month ───────────────────────────────────────────────────────
print("\n" + "=" * 100)
print("LEVEL 3 — PER MONTH (the monthly rate finance reports to the board)")
print("=" * 100)
mo_rows = []
for m, grp in df.groupby("RENEWAL_MONTH"):
    mo_rows.append({
        "month":    str(pd.Timestamp(m).date()),
        "N":        len(grp),
        "ATR_$M":   round(grp["ATR"].sum() / 1e6, 1),
        "actual_$M": round(grp["ACTUAL_DOLLAR"].sum() / 1e6, 2),
        "actual_rate": round(grp["ACTUAL_DOLLAR"].sum() / grp["ATR"].sum() * 100, 2),
        "A_rate":   round(grp["DOL_A"].sum() / grp["ATR"].sum() * 100, 2),
        "A_err_pp": round((grp["DOL_A"].sum() / grp["ATR"].sum() - grp["ACTUAL_DOLLAR"].sum() / grp["ATR"].sum()) * 100, 2),
        "C_rate":   round(grp["DOL_C"].sum() / grp["ATR"].sum() * 100, 2),
        "C_err_pp": round((grp["DOL_C"].sum() / grp["ATR"].sum() - grp["ACTUAL_DOLLAR"].sum() / grp["ATR"].sum()) * 100, 2),
    })
lvl3 = pd.DataFrame(mo_rows).sort_values("month")
print(lvl3.to_string(index=False))


# ── Level 4: per segment × month ─────────────────────────────────────────────
print("\n" + "=" * 100)
print("LEVEL 4 — PER SEGMENT × MONTH (the cells finance reviews in QBRs)")
print("=" * 100)
sm_rows = []
for (seg, mo), grp in df.groupby(["SEGMENT", "RENEWAL_MONTH"], observed=True):
    sm_rows.append({
        "segment": seg, "month": str(pd.Timestamp(mo).date()),
        "ATR_$M": round(grp["ATR"].sum() / 1e6, 2),
        "actual_rate": round(grp["ACTUAL_DOLLAR"].sum() / grp["ATR"].sum() * 100, 1),
        "A_err_pp":    round((grp["DOL_A"].sum() / grp["ATR"].sum() - grp["ACTUAL_DOLLAR"].sum() / grp["ATR"].sum()) * 100, 1),
        "C_err_pp":    round((grp["DOL_C"].sum() / grp["ATR"].sum() - grp["ACTUAL_DOLLAR"].sum() / grp["ATR"].sum()) * 100, 1),
    })
lvl4 = pd.DataFrame(sm_rows).sort_values(["segment", "month"])
print(lvl4.to_string(index=False))

# How many cells does each method get within ±3pp / ±5pp?
abs_a = lvl4["A_err_pp"].abs()
abs_c = lvl4["C_err_pp"].abs()
print(f"\nCells within ±3pp:  A={int((abs_a<=3).sum())}/{len(abs_a)}  C={int((abs_c<=3).sum())}/{len(abs_c)}")
print(f"Cells within ±5pp:  A={int((abs_a<=5).sum())}/{len(abs_a)}  C={int((abs_c<=5).sum())}/{len(abs_c)}")
print(f"Worst cell |err|:    A={abs_a.max():.1f}pp  C={abs_c.max():.1f}pp")
print(f"Mean |cell err|:     A={abs_a.mean():.2f}pp  C={abs_c.mean():.2f}pp")


# ── Level 5: portfolio total ─────────────────────────────────────────────────
print("\n" + "=" * 100)
print("LEVEL 5 — PORTFOLIO TOTAL (board number)")
print("=" * 100)
atr_tot = df["ATR"].sum()
act_tot = df["ACTUAL_DOLLAR"].sum()
print(pd.DataFrame([
    {"method": "ACTUAL",  "total_$M": round(act_tot/1e6, 2),
     "rate":  round(act_tot/atr_tot*100, 2), "vs_actual_$M": 0,    "vs_actual_pp": 0},
    {"method": "A. CURRENT (PRED_RENEW_RATE_FINAL)", "total_$M": round(df["DOL_A"].sum()/1e6, 2),
     "rate":  round(df["DOL_A"].sum()/atr_tot*100, 2),
     "vs_actual_$M": round((df["DOL_A"].sum()-act_tot)/1e6, 2),
     "vs_actual_pp": round((df["DOL_A"].sum()/atr_tot - act_tot/atr_tot)*100, 2)},
    {"method": "B. CALIBRATED EV", "total_$M": round(df["DOL_B"].sum()/1e6, 2),
     "rate":  round(df["DOL_B"].sum()/atr_tot*100, 2),
     "vs_actual_$M": round((df["DOL_B"].sum()-act_tot)/1e6, 2),
     "vs_actual_pp": round((df["DOL_B"].sum()/atr_tot - act_tot/atr_tot)*100, 2)},
    {"method": "C. BIMODAL", "total_$M": round(df["DOL_C"].sum()/1e6, 2),
     "rate":  round(df["DOL_C"].sum()/atr_tot*100, 2),
     "vs_actual_$M": round((df["DOL_C"].sum()-act_tot)/1e6, 2),
     "vs_actual_pp": round((df["DOL_C"].sum()/atr_tot - act_tot/atr_tot)*100, 2)},
]).to_string(index=False))


# ── Save full results ────────────────────────────────────────────────────────
out_path = _HERE / "DEEP_DIVE_BIMODAL_RECONCILIATION.txt"
with open(out_path, "w", encoding="utf-8") as f:
    f.write("LEVEL 1 — PER CONTRACT\n" + lvl1.to_string(index=False))
    f.write("\n\nLEVEL 2 — PER SEGMENT\n" + lvl2.to_string(index=False))
    f.write("\n\nLEVEL 3 — PER MONTH\n" + lvl3.to_string(index=False))
    f.write("\n\nLEVEL 4 — PER SEGMENT × MONTH\n" + lvl4.to_string(index=False))
print(f"\nSaved to {out_path}")
