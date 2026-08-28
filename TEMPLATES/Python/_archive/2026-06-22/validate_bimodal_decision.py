"""
BIMODAL DECISION VALIDATION
============================
Tests whether surfacing the model's bimodal predictions actually reduces
per-contract error vs. the current "scalar expected value" approach.

This is the answer to the user's question: can the model predict closer
to actual outcomes (0 or ATR) on a per-contract basis without retraining?

Compares 3 approaches on the same VALIDATION holdout data:
  A. CURRENT: PRED_RENEW_RATE_FINAL (scalar expected value, anchor-clipped)
  B. CALIBRATED RATE: P_FULL_CAL * ATR + P_PARTIAL_CAL * E_PARTIAL * ATR
  C. BIMODAL DECISION: per-contract decision rule (Will Renew / Will Churn / etc)

Reports for each:
  - Per-contract MAE in $ and pp
  - Hit rate (predicted outcome matches actual outcome class)
  - Aggregate $ accuracy (board-relevant)
  - Distribution of predicted classes
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
APPC  = "STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"

pd.set_option("display.float_format", "{:.3f}".format)
pd.set_option("display.width", 160)

# ── Load data ────────────────────────────────────────────────────────────────
print("Loading raw predictions + calibration knots + actuals...")
df = fetch_dataframe(f"""
WITH latest_run AS (
    SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT='VALIDATION'
)
SELECT
    p.CONTRACT_ID_UFR AS CONTRACT_ID,
    DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
    p.HORIZON,
    p.SEGMENT,
    p.ATR,
    p.P_LOGO_CHURN,
    p.P_DOLLAR_CHURN,
    p.P_FULL_RENEWAL,
    p.E_PARTIAL_RATE,
    p.PRED_RENEW_RATE_FINAL,
    f.TARGET__RENEWAL_RATE AS ACTUAL_RATE
FROM {PREDS} p
JOIN latest_run lr ON p.RUN_ID = lr.RUN_ID
JOIN {FEAT} f
    ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
    AND DATE_TRUNC('MONTH', p.RENEWAL_MONTH) = DATE_TRUNC('MONTH', f.RENEWAL_MONTH)
    AND p.HORIZON = f.HORIZON
    AND p.SPLIT   = f.SPLIT
WHERE p.SPLIT='VALIDATION'
  AND f.COHORT='MATURED'
  AND p.ATR > 0
  AND f.TARGET__RENEWAL_RATE IS NOT NULL
""")
df["ACTUAL_DOLLAR"] = df["ACTUAL_RATE"] * df["ATR"]
df["HORIZON"] = df["HORIZON"].astype(int)
for c in ("P_LOGO_CHURN", "P_FULL_RENEWAL", "P_DOLLAR_CHURN", "E_PARTIAL_RATE",
          "PRED_RENEW_RATE_FINAL", "ATR", "ACTUAL_RATE"):
    df[c] = pd.to_numeric(df[c], errors="coerce")
df = df.dropna(subset=["P_LOGO_CHURN", "P_FULL_RENEWAL", "ATR", "ACTUAL_RATE"])
print(f"  Loaded {len(df):,} rows  total ATR=${df['ATR'].sum()/1e6:.1f}M\n")

knot_df = fetch_dataframe(f"SELECT MODEL_TARGET, SEGMENT, HORIZON, KNOT_X_JSON, KNOT_Y_JSON FROM {KNOTS}")
knots = {}
for _, row in knot_df.iterrows():
    knots[(str(row["MODEL_TARGET"]), str(row["SEGMENT"]), int(row["HORIZON"]))] = {
        "x": json.loads(row["KNOT_X_JSON"]),
        "y": json.loads(row["KNOT_Y_JSON"]),
    }

# Apply calibration
p_logo_cal = df["P_LOGO_CHURN"].values.copy().astype(float)
p_full_cal = df["P_FULL_RENEWAL"].values.copy().astype(float)
for (seg, h), grp in df.groupby(["SEGMENT", "HORIZON"]):
    h_int = int(min(max(h, 0), 6))
    mask  = df.index.get_indexer(grp.index)
    for target, src_col, arr in [
        ("P_LOGO_CHURN", "P_LOGO_CHURN", p_logo_cal),
        ("P_FULL_RENEWAL", "P_FULL_RENEWAL", p_full_cal),
    ]:
        k = knots.get((target, seg, h_int))
        if k:
            arr[mask] = np.interp(grp[src_col].values.astype(float), k["x"], k["y"]).clip(0, 1)
df["P_LOGO_CAL"] = p_logo_cal
df["P_FULL_CAL"] = p_full_cal
# Renormalize
tot = df["P_LOGO_CAL"] + df["P_FULL_CAL"]
scale = np.where(tot > 1.0, 1.0 / np.where(tot > 0, tot, 1.0), 1.0)
df["P_LOGO_CAL"] = (df["P_LOGO_CAL"] * scale).clip(0, 1)
df["P_FULL_CAL"] = (df["P_FULL_CAL"] * scale).clip(0, 1)
df["P_PARTIAL_CAL"] = (1.0 - df["P_LOGO_CAL"] - df["P_FULL_CAL"]).clip(0, 1)

# ── 3 prediction strategies ──────────────────────────────────────────────────
ATR = df["ATR"].values

# A: Current PRED_RENEW_RATE_FINAL
df["PRED_DOL_A"] = df["PRED_RENEW_RATE_FINAL"].clip(0, 1) * ATR

# B: Calibrated expected value
df["PRED_DOL_B"] = (
    df["P_FULL_CAL"] * 1.0
    + df["P_PARTIAL_CAL"] * df["E_PARTIAL_RATE"].fillna(0.5)
    + df["P_LOGO_CAL"] * 0.0
) * ATR

# C: Bimodal decision (mirror app logic)
p_full = df["P_FULL_CAL"].values
p_logo = df["P_LOGO_CAL"].values
pred_c   = np.full(len(df), np.nan)
class_c  = np.full(len(df), "Uncertain", dtype=object)

m_renew  = p_full >= 0.70
m_churn  = (p_logo >= 0.40) & ~m_renew
m_likely = (p_full >= 0.45) & ~m_renew & ~m_churn
m_risk   = (p_logo >= 0.20) & ~m_renew & ~m_churn & ~m_likely
m_other  = ~m_renew & ~m_churn & ~m_likely & ~m_risk

pred_c[m_renew]  = ATR[m_renew]                         ; class_c[m_renew]  = "Will Renew"
pred_c[m_churn]  = 0.0                                  ; class_c[m_churn]  = "Will Churn"
pred_c[m_likely] = ATR[m_likely] * 0.95                 ; class_c[m_likely] = "Likely Renew"
pred_c[m_risk]   = ATR[m_risk]   * p_full[m_risk]       ; class_c[m_risk]   = "At Risk"
pred_c[m_other]  = ATR[m_other]  * p_full[m_other]      ; class_c[m_other]  = "Uncertain"

df["PRED_DOL_C"]   = pred_c
df["BIMODAL_CLASS"] = class_c

# ── Metrics ──────────────────────────────────────────────────────────────────
print("=" * 90)
print("PER-CONTRACT ACCURACY  (lower = better)")
print("=" * 90)
metrics = []
for label, col in [
    ("A. CURRENT (PRED_RENEW_RATE_FINAL)", "PRED_DOL_A"),
    ("B. CALIBRATED EXPECTED VALUE",       "PRED_DOL_B"),
    ("C. BIMODAL DECISION",                "PRED_DOL_C"),
]:
    err_dol = df[col] - df["ACTUAL_DOLLAR"]
    rate_err_pp = (df[col] / ATR - df["ACTUAL_RATE"]) * 100
    metrics.append({
        "approach": label,
        "MAE $ per-contract": int(err_dol.abs().mean()),
        "MAE pp per-contract": round(rate_err_pp.abs().mean(), 1),
        "Median |err| $": int(err_dol.abs().median()),
        "Aggregate $ off ($M)": round((df[col].sum() - df["ACTUAL_DOLLAR"].sum()) / 1e6, 2),
        "Aggregate rate off (pp)": round((df[col].sum() / ATR.sum() - df["ACTUAL_DOLLAR"].sum() / ATR.sum()) * 100, 2),
    })
print(pd.DataFrame(metrics).to_string(index=False))

# Hit rate: does predicted class match actual outcome class?
df["ACTUAL_CLASS"] = pd.cut(
    df["ACTUAL_RATE"],
    bins=[-0.01, 0.01, 0.99, 1.01],
    labels=["Full Churn", "Partial", "Full Renew"],
).astype(str)

print("\n" + "=" * 90)
print("BIMODAL DECISION — class distribution & hit rate")
print("=" * 90)
dist = (
    df.groupby("BIMODAL_CLASS", observed=True)
    .agg(
        N=("CONTRACT_ID", "count"),
        ATR_M=("ATR", lambda x: round(x.sum() / 1e6, 1)),
        avg_actual_rate=("ACTUAL_RATE", "mean"),
        median_actual_rate=("ACTUAL_RATE", "median"),
        pct_full_renew=("ACTUAL_RATE", lambda x: round((x == 1.0).mean() * 100, 1)),
        pct_full_churn=("ACTUAL_RATE", lambda x: round((x == 0.0).mean() * 100, 1)),
    )
    .reset_index()
)
print(dist.to_string(index=False))

# Confidence calibration: when model says "Will Renew", what % actually renew?
print("\n" + "=" * 90)
print("MODEL CONFIDENCE vs ACTUAL OUTCOME  (key trust metric)")
print("=" * 90)
trust = []
for cls in ["Will Renew", "Likely Renew", "At Risk", "Will Churn", "Uncertain"]:
    sub = df[df["BIMODAL_CLASS"] == cls]
    if len(sub) == 0:
        continue
    full_renew_pct = (sub["ACTUAL_RATE"] == 1.0).mean() * 100
    full_churn_pct = (sub["ACTUAL_RATE"] == 0.0).mean() * 100
    trust.append({
        "class": cls,
        "n_contracts": len(sub),
        "% ATR": round(sub["ATR"].sum() / df["ATR"].sum() * 100, 1),
        "% actually FULL RENEW": round(full_renew_pct, 1),
        "% actually FULL CHURN": round(full_churn_pct, 1),
        "% actually partial": round(100 - full_renew_pct - full_churn_pct, 1),
        "avg actual rate": round(sub["ACTUAL_RATE"].mean(), 3),
    })
print(pd.DataFrame(trust).to_string(index=False))

# Decompose MAE by class for C
print("\n" + "=" * 90)
print("BIMODAL DECISION — MAE decomposition by predicted class")
print("=" * 90)
decomp = []
for cls in df["BIMODAL_CLASS"].unique():
    sub = df[df["BIMODAL_CLASS"] == cls]
    decomp.append({
        "class": cls,
        "n": len(sub),
        "MAE_$": int((sub["PRED_DOL_C"] - sub["ACTUAL_DOLLAR"]).abs().mean()),
        "MAE_pp": round(((sub["PRED_DOL_C"] / sub["ATR"] - sub["ACTUAL_RATE"]).abs() * 100).mean(), 1),
    })
print(pd.DataFrame(decomp).to_string(index=False))

# Save
out_path = _HERE / "BIMODAL_DECISION_VALIDATION.txt"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(pd.DataFrame(metrics).to_string(index=False))
    f.write("\n\n" + dist.to_string(index=False))
    f.write("\n\n" + pd.DataFrame(trust).to_string(index=False))
    f.write("\n\n" + pd.DataFrame(decomp).to_string(index=False))

print(f"\nResults saved to: {out_path}")
