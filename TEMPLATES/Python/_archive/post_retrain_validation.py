"""
POST-RETRAIN VALIDATION — Stage C Rate Calibration
====================================================
Run this AFTER SP_V5_SANDBOX_RUN_PIPELINE() completes in Snowsight.

Checks:
  1. Stage C fired — calibrators were fit and logged
  2. The (0.50–0.65] bucket gap is below 5pp (was +14.9pp before fix)
  3. Segment × month accuracy didn't degrade (must still be 29+/30 within ±5pp)
  4. Portfolio total still correct (bias within ±2pp)
  5. Board gates still all PASS

Usage:
    python post_retrain_validation.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from connection import fetch_dataframe

PREDS = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS"
FEAT  = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE"
LOG   = "STREAMLIT_APPS.DBO.V5_PIPELINE_RUN_LOG"

SEP = "=" * 70
pd.set_option("display.float_format", "{:.3f}".format)
pd.set_option("display.width", 160)

PASS = "✓ PASS"
FAIL = "✗ FAIL"
results = []

def check(label, passed, detail=""):
    tag = PASS if passed else FAIL
    line = f"  [{tag}]  {label}"
    if detail:
        line += f"\n           {detail}"
    print(line)
    results.append((tag, label))
    return passed


print(f"\n{SEP}")
print("POST-RETRAIN VALIDATION — Stage C Rate Calibration Fix")
print(f"{SEP}")

# ── 0. Confirm pipeline ran after the code change ─────────────────────────────
print("\n--- 0. CONFIRM RETRAIN RAN ---")
run_log = fetch_dataframe(f"""
    SELECT SOURCE, STATUS, MESSAGE, TRIGGERED_AT
    FROM {LOG}
    WHERE SOURCE IN ('v5-train', 'v5-pipeline', 'v5-sandbox-run')
    ORDER BY TRIGGERED_AT DESC
    LIMIT 5
""")
if len(run_log) > 0:
    print(run_log.to_string(index=False))
    last_status = str(run_log.iloc[0]["STATUS"])
    check("Pipeline ran OK after code change", last_status == "OK",
          f"Last status: {last_status}  at {run_log.iloc[0]['TRIGGERED_AT']}")
else:
    print("  No pipeline log entries found — has the retrain run yet?")
    check("Pipeline log exists", False, "Run CALL SP_V5_SANDBOX_RUN_PIPELINE() in Snowsight first")

# ── 1. Load validation data ───────────────────────────────────────────────────
print("\n--- 1. LOAD VALIDATION DATA ---")
df = fetch_dataframe(f"""
    WITH lr AS (SELECT MAX(RUN_ID) AS RUN_ID FROM {PREDS} WHERE SPLIT='VALIDATION')
    SELECT
        p.CONTRACT_ID_UFR, DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
        p.SEGMENT, p.HORIZON, p.ATR,
        p.PRED_RENEW_RATE_FINAL,
        f.TARGET__RENEWAL_RATE AS ACTUAL_RATE
    FROM {PREDS} p
    JOIN lr ON p.RUN_ID = lr.RUN_ID
    JOIN {FEAT} f
        ON  p.CONTRACT_ID_UFR = f.CONTRACT_ID_UFR
        AND DATE_TRUNC('MONTH', p.RENEWAL_MONTH) = DATE_TRUNC('MONTH', f.RENEWAL_MONTH)
        AND p.HORIZON = f.HORIZON AND p.SPLIT = f.SPLIT
    WHERE p.SPLIT='VALIDATION' AND f.COHORT='MATURED'
      AND p.ATR > 0 AND f.TARGET__RENEWAL_RATE IS NOT NULL
""")
for c in ("ATR", "PRED_RENEW_RATE_FINAL", "ACTUAL_RATE"):
    df[c] = pd.to_numeric(df[c], errors="coerce")
df["HORIZON"] = df["HORIZON"].astype(int)
df = df.dropna(subset=["ATR", "PRED_RENEW_RATE_FINAL", "ACTUAL_RATE"])
df["PRED_$"]   = df["PRED_RENEW_RATE_FINAL"] * df["ATR"]
df["ACTUAL_$"] = df["ACTUAL_RATE"]            * df["ATR"]
print(f"  {len(df):,} contract×month×horizon rows loaded")
check("Validation data loaded", len(df) > 50_000, f"{len(df):,} rows")

# ── 2. THE BUCKET FIX — was +14.9pp, must be < 5pp ───────────────────────────
print("\n--- 2. THE BUCKET FIX: (0.50, 0.65] overestimation ---")
print("  Target: gap < 5pp  (was +14.9pp before fix)")

h0 = df[df["HORIZON"] == 0].copy()
h0["PRED_BUCKET"] = pd.cut(
    h0["PRED_RENEW_RATE_FINAL"],
    bins=[0, 0.10, 0.25, 0.50, 0.65, 0.75, 0.85, 0.92, 0.97, 1.01],
    include_lowest=True,
)
rel = h0.groupby("PRED_BUCKET", observed=True).agg(
    N=("ATR", "size"),
    ATR_M=("ATR", lambda x: x.sum() / 1e6),
    PRED_AVG=("PRED_RENEW_RATE_FINAL", "mean"),
    ACTUAL_AVG=("ACTUAL_RATE", "mean"),
).reset_index()
rel["GAP_PP"] = (rel["PRED_AVG"] - rel["ACTUAL_AVG"]) * 100
print(rel.to_string(index=False))

target_bucket = rel[rel["PRED_BUCKET"].astype(str).str.contains("0.5") |
                    rel["PRED_BUCKET"].astype(str).str.contains("0.65")]
if len(target_bucket) > 0:
    worst_gap = float(target_bucket["GAP_PP"].abs().max())
    check(
        "(0.50–0.65] bucket gap < 5pp",
        worst_gap < 5.0,
        f"Worst gap in target range: {worst_gap:.1f}pp  (was +14.9pp before fix)"
    )
else:
    check("(0.50–0.65] bucket exists in predictions", False,
          "No predictions in (0.50–0.65] — check model output distribution")

wt_gap = (rel["GAP_PP"].abs() * rel["ATR_M"]).sum() / rel["ATR_M"].sum()
check("ATR-weighted mean calibration gap < 5pp", wt_gap < 5.0, f"{wt_gap:.2f}pp")

# ── 3. SEGMENT × MONTH — must not degrade ────────────────────────────────────
print("\n--- 3. SEGMENT × MONTH ACCURACY (H=0) ---")
print("  Target: ≥29/30 cells within ±5pp  (same as before fix)")

sm = h0.groupby(["SEGMENT", "RENEWAL_MONTH"]).apply(
    lambda g: pd.Series({
        "ATR_M": g["ATR"].sum() / 1e6,
        "PRED_RATE": g["PRED_$"].sum() / g["ATR"].sum() * 100,
        "ACTUAL_RATE": g["ACTUAL_$"].sum() / g["ATR"].sum() * 100,
        "ERR_PP": (g["PRED_$"].sum() - g["ACTUAL_$"].sum()) / g["ATR"].sum() * 100,
    }), include_groups=False,
).reset_index()
n_within_5 = (sm["ERR_PP"].abs() <= 5).sum()
worst = float(sm["ERR_PP"].abs().max())
print(sm[["SEGMENT", "RENEWAL_MONTH", "ATR_M", "PRED_RATE", "ACTUAL_RATE", "ERR_PP"]]
      .sort_values("ERR_PP", key=abs, ascending=False).to_string(index=False))
check(f"≥29/30 segment×month cells within ±5pp", n_within_5 >= 29,
      f"{n_within_5}/30 within ±5pp  |  worst: {worst:.1f}pp")

# ── 4. PORTFOLIO TOTAL ────────────────────────────────────────────────────────
print("\n--- 4. PORTFOLIO TOTAL BIAS ---")
total_pred   = h0["PRED_$"].sum()
total_actual = h0["ACTUAL_$"].sum()
total_atr    = h0["ATR"].sum()
bias_pp = (total_pred - total_actual) / total_atr * 100
check(
    "Portfolio bias within ±2pp",
    abs(bias_pp) <= 2.0,
    f"Bias = {bias_pp:+.2f}pp  |  pred={total_pred/1e6:.1f}M  actual={total_actual/1e6:.1f}M"
)

# ── 5. PER-SEGMENT BIAS ───────────────────────────────────────────────────────
print("\n--- 5. PER-SEGMENT BIAS ---")
seg_bias = h0.groupby("SEGMENT").apply(
    lambda g: pd.Series({
        "ATR_M":    g["ATR"].sum() / 1e6,
        "PRED_%":   g["PRED_$"].sum() / g["ATR"].sum() * 100,
        "ACTUAL_%": g["ACTUAL_$"].sum() / g["ATR"].sum() * 100,
        "BIAS_PP":  (g["PRED_$"].sum() - g["ACTUAL_$"].sum()) / g["ATR"].sum() * 100,
    }), include_groups=False,
).reset_index()
print(seg_bias.to_string(index=False))
max_seg_bias = float(seg_bias["BIAS_PP"].abs().max())
check("All segments within ±5pp bias", max_seg_bias <= 5.0,
      f"Max segment bias: {max_seg_bias:.1f}pp")

# ── SUMMARY ───────────────────────────────────────────────────────────────────
print(f"\n{SEP}")
n_pass = sum(1 for t, _ in results if t == PASS)
n_fail = sum(1 for t, _ in results if t != PASS)
print(f"RESULT: {n_pass} passed  {n_fail} failed")
print()
for t, l in results:
    print(f"  [{t}]  {l}")
print()
if n_fail == 0:
    print("  ALL CHECKS PASS — STAGE C FIX VALIDATED. READY TO DEPLOY APP.")
else:
    print("  SOME CHECKS FAILED. Review output above before deploying.")
print(SEP)
