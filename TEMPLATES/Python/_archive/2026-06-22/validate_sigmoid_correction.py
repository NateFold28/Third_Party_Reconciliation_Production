"""
validate_sigmoid_correction.py
================================
Validates whether a sigmoid (bimodal-aware) correction to ML_FORECAST improves
contract-level renewal-rate accuracy over the current linear/continuous model.

Design:
  FIT WINDOW   → earliest matured months up to 2024-12    (learn the curve shape)
  HOLD WINDOW  → 2025-01 to 2025-12                        (unseen year, honest OOS)
  RECENT TEST  → 2026-01 to latest matured month           (current-regime test)

Questions answered:
  A. Current model MAE / Bias / ECE vs hold and recent windows
  B. Best-fit sigmoid (per portfolio and per segment)
  C. OOS sigmoid MAE — does it beat the current model?
  D. Piecewise correction: for CHURN_PCT > threshold → floor to partial-mean
  E. Portfolio-level (ATR-weighted) rate improvement
  F. Segment-by-segment comparison table
  G. Recommendation: apply sigmoid or not, and optimal parameters

Usage:
    cd c:\\Users\\Nate.Fold\\projects
    .venv\\Scripts\\python.exe TEMPLATES\\Python\\validate_sigmoid_correction.py

Outputs:
    • Console tables
    • validate_sigmoid_correction.png  (4-panel chart)
    • validate_sigmoid_correction_params.json  (params to paste into app)
"""

from __future__ import annotations

import json
import sys
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')

import numpy as np
import pandas as pd
from connection import get_snowflake_connection, fetch_dataframe

pd.set_option("display.float_format", "{:,.3f}".format)
pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 45)
pd.set_option("display.max_rows", 120)

SEP = "=" * 90

# ── Windows (can be adjusted here without touching the rest of the script) ──
FIT_END     = "2024-12-31"   # inclusive upper bound for sigmoid fitting
HOLD_START  = "2025-01-01"
HOLD_END    = "2025-12-31"
RECENT_START = "2026-01-01"  # through latest matured month

# Bimodal threshold: contracts above this CHURN_PCT (0-100 scale) are considered
# "high-churn zone" where the cliff correction matters most.
CHURN_CLIFF_PCT = 60.0

# Grid-search space for sigmoid a / b parameters
A_RANGE = np.arange(2.0, 20.0, 0.5)
B_RANGE = np.arange(0.30, 0.90, 0.02)

SEGMENTS = ["Core", "Emerging", "Growth", "ScreenConnect Only", "Strategic"]


def hdr(title: str, sub: str = "") -> None:
    print(f"\n{SEP}\n{title}")
    if sub:
        print(f"  {sub}")
    print(SEP)


# ── Sigmoid math ──────────────────────────────────────────────────────────────
def sigmoid_renewal(churn_prob_0_1: np.ndarray, a: float, b: float) -> np.ndarray:
    """Map churn probability [0,1] → renewal rate [0,100] via steep sigmoid."""
    z = a * (churn_prob_0_1 - b)
    return 100.0 * (1.0 - 1.0 / (1.0 + np.exp(-np.clip(z, -40, 40))))


def fit_sigmoid(churn_pct: np.ndarray, actual_rate_pct: np.ndarray,
                atr_weights: np.ndarray | None = None) -> tuple[float, float, float]:
    """Grid-search best (a, b) for sigmoid_renewal minimising ATR-weighted MAE.
    Returns (best_a, best_b, best_mae_pp).
    """
    cp = churn_pct / 100.0
    w  = atr_weights if atr_weights is not None else np.ones(len(cp))
    w  = w / w.sum()  # normalise so loss is comparable

    best_mae, best_a, best_b = 999.0, 5.0, 0.5
    for a in A_RANGE:
        for b in B_RANGE:
            pred = sigmoid_renewal(cp, a, b)
            mae  = float(np.sum(w * np.abs(pred - actual_rate_pct)))
            if mae < best_mae:
                best_mae, best_a, best_b = mae, a, b
    return best_a, best_b, best_mae


def eval_model(churn_pct: np.ndarray, pred_rate_pct: np.ndarray,
               actual_rate_pct: np.ndarray,
               atr: np.ndarray | None = None,
               label: str = "Model") -> dict:
    """Compute MAE, bias, ATR-weighted MAE, and ECE for a set of predictions."""
    mask = ~(np.isnan(churn_pct) | np.isnan(pred_rate_pct) | np.isnan(actual_rate_pct))
    cp, pp, ar = churn_pct[mask], pred_rate_pct[mask], actual_rate_pct[mask]
    w = (atr[mask] if atr is not None else np.ones(len(cp)))
    w_norm = w / w.sum()

    mae      = float(np.mean(np.abs(pp - ar)))
    bias     = float(np.mean(pp - ar))
    atr_mae  = float(np.sum(w_norm * np.abs(pp - ar)))
    atr_bias = float(np.sum(w_norm * (pp - ar)))

    # ECE: 10-bin calibration error on churn probability
    bins      = np.linspace(0, 100, 11)
    bin_idx   = np.digitize(cp, bins) - 1
    bin_idx   = np.clip(bin_idx, 0, 9)
    ece_sum   = 0.0
    ece_count = 0
    for i in range(10):
        m = bin_idx == i
        if m.sum() > 0:
            pred_churn_frac = cp[m].mean() / 100.0
            actual_churn_frac = (1.0 - ar[m] / 100.0).mean()  # fraction that actually churned
            ece_sum += abs(pred_churn_frac - actual_churn_frac) * m.sum()
            ece_count += m.sum()
    ece = ece_sum / ece_count * 100.0 if ece_count > 0 else np.nan

    return {
        "label":    label,
        "n":        int(mask.sum()),
        "mae_pp":   round(mae,      2),
        "bias_pp":  round(bias,     2),
        "atr_mae":  round(atr_mae,  2),
        "atr_bias": round(atr_bias, 2),
        "ece_pp":   round(ece,      2) if not np.isnan(ece) else None,
    }


# ── Pull data ─────────────────────────────────────────────────────────────────
hdr("Pulling matured contract data from V5_SANDBOX_APP_CONTRACT_DETAIL…")
conn = get_snowflake_connection()

PULL_SQL = """
SELECT
    d.CONTRACT_ID,
    DATE_TRUNC('MONTH', d.RENEWAL_DATE)::DATE          AS RENEWAL_MONTH,
    d.SEGMENT,
    d.PRODUCT_PORTFOLIO,
    d.ATR,
    COALESCE(d.ACTUAL_RETAINED_ARR, 0)                 AS ACTUAL_RETAINED_ARR,
    d.ML_FORECAST,
    d.CHURN_PCT,
    d.RETENTION_PCT,
    d.CONTRACT_RISK_PCTL_IN_SEG,
    d.ACTUAL_RETAINED_ARR / NULLIF(d.ATR, 0) * 100.0  AS ACTUAL_RATE_PCT,
    d.ML_FORECAST          / NULLIF(d.ATR, 0) * 100.0  AS PRED_RATE_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
WHERE d.IS_MATURED_MONTH = TRUE
  AND d.ATR > 0
  AND d.ACTUAL_RETAINED_ARR IS NOT NULL
  AND d.CHURN_PCT IS NOT NULL
ORDER BY d.RENEWAL_DATE, d.ATR DESC
"""

df = fetch_dataframe(PULL_SQL, conn=conn)
df["RENEWAL_MONTH"] = pd.to_datetime(df["RENEWAL_MONTH"])
print(f"  Rows pulled : {len(df):,}")
print(f"  Contracts   : {df['CONTRACT_ID'].nunique():,}")
print(f"  Date range  : {df['RENEWAL_MONTH'].min().date()} → {df['RENEWAL_MONTH'].max().date()}")
print(f"  Segments    : {sorted(df['SEGMENT'].dropna().unique())}")

if df.empty:
    print("No matured data found. Exiting.")
    sys.exit(1)

# ── Split into fit / hold / recent ───────────────────────────────────────────
fit_mask    = df["RENEWAL_MONTH"] <= FIT_END
hold_mask   = (df["RENEWAL_MONTH"] >= HOLD_START) & (df["RENEWAL_MONTH"] <= HOLD_END)
recent_mask = df["RENEWAL_MONTH"] >= RECENT_START

df_fit    = df[fit_mask].copy()
df_hold   = df[hold_mask].copy()
df_recent = df[recent_mask].copy()

print(f"\n  Fit window   ({FIT_END[:7]} cutoff): {len(df_fit):,} rows")
print(f"  Hold window  ({HOLD_START[:7]} – {HOLD_END[:7]}): {len(df_hold):,} rows")
print(f"  Recent test  ({RECENT_START[:7]} – latest):  {len(df_recent):,} rows")


# ═══════════════════════════════════════════════════════════════════════════
# A — Current model accuracy baseline
# ═══════════════════════════════════════════════════════════════════════════
hdr("A — Current Model Accuracy", "MAE / Bias / ECE on each window")

for window_label, wdf in [
    ("Fit window (all pre-2025)",    df_fit),
    ("Hold window (2025 full year)", df_hold),
    ("Recent test (2026-YTD)",       df_recent),
    ("Combined (all matured)",       df),
]:
    if wdf.empty:
        print(f"  {window_label}: no data")
        continue
    r = eval_model(
        wdf["CHURN_PCT"].to_numpy(),
        wdf["PRED_RATE_PCT"].to_numpy(),
        wdf["ACTUAL_RATE_PCT"].to_numpy(),
        atr=wdf["ATR"].to_numpy(),
        label=window_label,
    )
    print(f"\n  {window_label}")
    print(f"    N={r['n']:,}  |  Contract MAE={r['mae_pp']:.2f}pp  |  "
          f"Bias={r['bias_pp']:.2f}pp  |  ATR-wtd MAE={r['atr_mae']:.2f}pp  |  "
          f"ATR-wtd Bias={r['atr_bias']:.2f}pp  |  ECE={r['ece_pp']}pp")


# ═══════════════════════════════════════════════════════════════════════════
# B — Bimodal distribution verification
# ═══════════════════════════════════════════════════════════════════════════
hdr("B — Bimodal Distribution Check", "Fraction of contracts in 0-10%, 90-100%, middle")

bins   = [0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100.01]
labels = ["0-5%","5-10%","10-20%","20-30%","30-40%","40-50%",
          "50-60%","60-70%","70-80%","80-90%","90-95%","95-100%"]
df["RATE_BUCKET"] = pd.cut(df["ACTUAL_RATE_PCT"].clip(0, 100.001),
                            bins=bins, labels=labels, right=False)
hist = df["RATE_BUCKET"].value_counts().sort_index()
pct  = (hist / len(df) * 100).round(1)
q2   = pd.DataFrame({"Contracts": hist, "%": pct})
print(q2.to_string())

low_mass  = float(pct[pct.index.isin(["0-5%", "5-10%"])].sum())
high_mass = float(pct[pct.index.isin(["90-95%", "95-100%"])].sum())
mid_mass  = 100.0 - low_mass - high_mass
print(f"\n  Bimodal mass: 0-10% = {low_mass:.1f}%  |  90-100% = {high_mass:.1f}%  |  middle = {mid_mass:.1f}%")

# Partial-mean for high-churn contracts (used in piecewise correction)
partial_mean = float(df.loc[(df["ACTUAL_RATE_PCT"] > 5) & (df["ACTUAL_RATE_PCT"] < 95),
                              "ACTUAL_RATE_PCT"].mean())
print(f"\n  Partial-mean (5-95% range): {partial_mean:.1f}%")
high_churn_mean = float(df.loc[df["CHURN_PCT"] > CHURN_CLIFF_PCT, "ACTUAL_RATE_PCT"].mean())
print(f"  Mean actual rate when CHURN_PCT>{CHURN_CLIFF_PCT:.0f}%: {high_churn_mean:.1f}%")


# ═══════════════════════════════════════════════════════════════════════════
# C — Portfolio-wide sigmoid fit (on fit window, test on hold + recent)
# ═══════════════════════════════════════════════════════════════════════════
hdr("C — Portfolio Sigmoid Fit + OOS Evaluation")

if len(df_fit) > 100:
    best_a, best_b, fit_mae = fit_sigmoid(
        df_fit["CHURN_PCT"].to_numpy(),
        df_fit["ACTUAL_RATE_PCT"].to_numpy(),
        atr_weights=df_fit["ATR"].to_numpy(),
    )
    print(f"  Portfolio sigmoid (fit on pre-2025):")
    print(f"    a = {best_a:.1f}  |  b = {best_b:.2f}  |  fit ATR-MAE = {fit_mae:.2f}pp")
    print(f"    Interpretation: cliff at CHURN_PCT = {best_b*100:.0f}%  |  "
          f"k (steepness) = {best_a:.1f}")
else:
    print("  Insufficient fit data for sigmoid fitting. Using empirical defaults (a=14.5, b=0.70).")
    best_a, best_b = 14.5, 0.70
    fit_mae = 0.0

# Apply portfolio sigmoid to hold and recent windows
for window_label, wdf in [
    ("Hold window (2025 OOS)",  df_hold),
    ("Recent test (2026-YTD)", df_recent),
    ("Combined (all matured)", df),
]:
    if wdf.empty:
        continue
    sig_pred = sigmoid_renewal(wdf["CHURN_PCT"].to_numpy() / 100.0, best_a, best_b)
    r_cur = eval_model(
        wdf["CHURN_PCT"].to_numpy(),
        wdf["PRED_RATE_PCT"].to_numpy(),
        wdf["ACTUAL_RATE_PCT"].to_numpy(),
        atr=wdf["ATR"].to_numpy(),
        label="Current model",
    )
    r_sig = eval_model(
        wdf["CHURN_PCT"].to_numpy(),
        sig_pred,
        wdf["ACTUAL_RATE_PCT"].to_numpy(),
        atr=wdf["ATR"].to_numpy(),
        label="Sigmoid",
    )
    delta = round(r_cur["atr_mae"] - r_sig["atr_mae"], 2)
    direction = "↑ WORSE" if delta < 0 else f"↓ BETTER (+{delta:.2f}pp)"
    print(f"\n  {window_label}")
    print(f"    Current   → ATR-MAE={r_cur['atr_mae']:.2f}pp  |  ATR-Bias={r_cur['atr_bias']:.2f}pp")
    print(f"    Sigmoid   → ATR-MAE={r_sig['atr_mae']:.2f}pp  |  ATR-Bias={r_sig['atr_bias']:.2f}pp  |  "
          f"Δ = {direction}")


# ═══════════════════════════════════════════════════════════════════════════
# D — Piecewise correction: for CHURN_PCT > CLIFF → floor to partial-mean
# ═══════════════════════════════════════════════════════════════════════════
hdr("D — Piecewise Correction Evaluation", f"CHURN_PCT > {CHURN_CLIFF_PCT:.0f}% → partial-mean ({high_churn_mean:.1f}%)")

# Piecewise: high-churn zone uses historical mean; low-churn zone keeps current model
def piecewise_pred(churn_pct: np.ndarray, current_pred: np.ndarray,
                   cliff: float, partial_mean: float) -> np.ndarray:
    out = current_pred.copy()
    high = churn_pct > cliff
    out[high] = partial_mean
    return out

# Piecewise + Sigmoid hybrid:
# Below cliff: sigmoid   |   Above cliff: partial-mean floor
def hybrid_pred(churn_pct: np.ndarray, a: float, b: float,
                cliff: float, partial_mean: float) -> np.ndarray:
    cp = churn_pct / 100.0
    sig = sigmoid_renewal(cp, a, b)
    high = churn_pct > cliff
    sig[high] = partial_mean
    return sig

for window_label, wdf in [
    ("Hold window (2025 OOS)",  df_hold),
    ("Recent test (2026-YTD)", df_recent),
]:
    if wdf.empty:
        continue
    cp   = wdf["CHURN_PCT"].to_numpy()
    ar   = wdf["ACTUAL_RATE_PCT"].to_numpy()
    pp   = wdf["PRED_RATE_PCT"].to_numpy()
    atr  = wdf["ATR"].to_numpy()

    pw   = piecewise_pred(cp, pp, CHURN_CLIFF_PCT, high_churn_mean)
    hyb  = hybrid_pred(cp, best_a, best_b, CHURN_CLIFF_PCT, high_churn_mean)
    sig  = sigmoid_renewal(cp / 100.0, best_a, best_b)

    r_cur = eval_model(cp, pp,  ar, atr=atr, label="Current")
    r_pw  = eval_model(cp, pw,  ar, atr=atr, label="Piecewise")
    r_sig = eval_model(cp, sig, ar, atr=atr, label="Sigmoid")
    r_hyb = eval_model(cp, hyb, ar, atr=atr, label="Hybrid")

    print(f"\n  {window_label}  (N={r_cur['n']:,})")
    print(f"  {'Method':<20} {'ATR-MAE':>9} {'ATR-Bias':>10} {'ECE':>9}")
    print(f"  {'-'*50}")
    for r in [r_cur, r_pw, r_sig, r_hyb]:
        ece = f"{r['ece_pp']:.2f}pp" if r['ece_pp'] is not None else "n/a"
        print(f"  {r['label']:<20} {r['atr_mae']:>8.2f}pp {r['atr_bias']:>9.2f}pp {ece:>9}")


# ═══════════════════════════════════════════════════════════════════════════
# E — Portfolio-level (ATR-weighted RATE) improvement
# ═══════════════════════════════════════════════════════════════════════════
hdr("E — Portfolio Rate Improvement", "Does the correction reduce portfolio-level bias?")

for window_label, wdf in [
    ("Hold window (2025)",      df_hold),
    ("Recent test (2026-YTD)", df_recent),
    ("All matured",             df),
]:
    if wdf.empty:
        continue
    total_atr = wdf["ATR"].sum()
    actual_rate  = wdf["ACTUAL_RETAINED_ARR"].sum() / total_atr * 100.0

    pred_rate_cur = wdf["ML_FORECAST"].sum() / total_atr * 100.0
    sig_arr  = sigmoid_renewal(wdf["CHURN_PCT"].to_numpy() / 100.0, best_a, best_b)
    pred_rate_sig = float((sig_arr * wdf["ATR"].to_numpy() / 100.0).sum()) / total_atr * 100.0

    pw_arr   = piecewise_pred(wdf["CHURN_PCT"].to_numpy(),
                               wdf["PRED_RATE_PCT"].to_numpy(),
                               CHURN_CLIFF_PCT, high_churn_mean)
    hyb_arr  = hybrid_pred(wdf["CHURN_PCT"].to_numpy(), best_a, best_b,
                            CHURN_CLIFF_PCT, high_churn_mean)
    pred_rate_pw  = float((pw_arr  * wdf["ATR"].to_numpy() / 100.0).sum()) / total_atr * 100.0
    pred_rate_hyb = float((hyb_arr * wdf["ATR"].to_numpy() / 100.0).sum()) / total_atr * 100.0

    print(f"\n  {window_label}  (ATR=${total_atr:,.0f})")
    print(f"    Actual portfolio rate:    {actual_rate:.2f}%")
    print(f"    Current model:            {pred_rate_cur:.2f}%  (bias {pred_rate_cur-actual_rate:+.2f}pp)")
    print(f"    Sigmoid:                  {pred_rate_sig:.2f}%  (bias {pred_rate_sig-actual_rate:+.2f}pp)")
    print(f"    Piecewise:                {pred_rate_pw:.2f}%   (bias {pred_rate_pw-actual_rate:+.2f}pp)")
    print(f"    Hybrid (sig+piecewise):   {pred_rate_hyb:.2f}%  (bias {pred_rate_hyb-actual_rate:+.2f}pp)")


# ═══════════════════════════════════════════════════════════════════════════
# F — Per-segment sigmoid fit + comparison
# ═══════════════════════════════════════════════════════════════════════════
hdr("F — Per-Segment Sigmoid Fit + OOS Comparison",
    "Fit on pre-2025, test on 2025 and 2026-YTD")

seg_params: dict[str, dict] = {}
seg_rows = []

for seg in SEGMENTS:
    df_seg_fit  = df_fit[df_fit["SEGMENT"] == seg]
    df_seg_hold = df_hold[df_hold["SEGMENT"] == seg]
    df_seg_rec  = df_recent[df_recent["SEGMENT"] == seg]

    if len(df_seg_fit) < 50:
        print(f"  {seg}: insufficient fit data ({len(df_seg_fit)} rows). Using portfolio params.")
        seg_params[seg] = {"a": best_a, "b": best_b, "source": "portfolio_fallback"}
        continue

    # Fit segment sigmoid
    s_a, s_b, s_fit_mae = fit_sigmoid(
        df_seg_fit["CHURN_PCT"].to_numpy(),
        df_seg_fit["ACTUAL_RATE_PCT"].to_numpy(),
        atr_weights=df_seg_fit["ATR"].to_numpy(),
    )
    seg_params[seg] = {"a": s_a, "b": s_b, "fit_mae": s_fit_mae, "source": "segment_fit"}

    # Evaluate on hold window
    for eval_label, eval_df in [("Hold-2025", df_seg_hold), ("Recent-2026", df_seg_rec)]:
        if eval_df.empty:
            continue
        sig_pred = sigmoid_renewal(eval_df["CHURN_PCT"].to_numpy() / 100.0, s_a, s_b)
        r_cur = eval_model(
            eval_df["CHURN_PCT"].to_numpy(),
            eval_df["PRED_RATE_PCT"].to_numpy(),
            eval_df["ACTUAL_RATE_PCT"].to_numpy(),
            atr=eval_df["ATR"].to_numpy(),
            label="Current",
        )
        r_sig = eval_model(
            eval_df["CHURN_PCT"].to_numpy(),
            sig_pred,
            eval_df["ACTUAL_RATE_PCT"].to_numpy(),
            atr=eval_df["ATR"].to_numpy(),
            label="Sigmoid",
        )
        delta_mae  = r_cur["atr_mae"] - r_sig["atr_mae"]
        seg_rows.append({
            "Segment":    seg,
            "Window":     eval_label,
            "N":          r_cur["n"],
            "Cur_MAE":    r_cur["atr_mae"],
            "Sig_MAE":    r_sig["atr_mae"],
            "Delta_MAE":  round(delta_mae, 2),
            "Cur_Bias":   r_cur["atr_bias"],
            "Sig_Bias":   r_sig["atr_bias"],
            "Sig_a":      s_a,
            "Sig_b":      s_b,
            "Direction":  "BETTER" if delta_mae > 0 else "WORSE",
        })

if seg_rows:
    seg_df = pd.DataFrame(seg_rows)
    print("\n")
    print(seg_df[["Segment","Window","N","Cur_MAE","Sig_MAE","Delta_MAE",
                   "Cur_Bias","Sig_Bias","Sig_a","Sig_b","Direction"]].to_string(index=False))


# ═══════════════════════════════════════════════════════════════════════════
# G — Decile bias comparison (current vs sigmoid)
# ═══════════════════════════════════════════════════════════════════════════
hdr("G — Decile Bias: Current vs Sigmoid vs Hybrid", "Using combined matured set")

df["RISK_DECILE"] = pd.qcut(
    df["CHURN_PCT"].rank(method="first"), q=10,
    labels=[f"D{i}" for i in range(1, 11)]
)
sig_all = sigmoid_renewal(df["CHURN_PCT"].to_numpy() / 100.0, best_a, best_b)
hyb_all = hybrid_pred(df["CHURN_PCT"].to_numpy(), best_a, best_b,
                       CHURN_CLIFF_PCT, high_churn_mean)
df["SIG_PRED"]  = sig_all
df["HYB_PRED"]  = hyb_all

decile_compare = df.groupby("RISK_DECILE", observed=True).agg(
    N=("CONTRACT_ID", "count"),
    Avg_Churn_Prob=("CHURN_PCT", "mean"),
    Actual_Rate=("ACTUAL_RATE_PCT", "mean"),
    Cur_Rate=("PRED_RATE_PCT", "mean"),
    Sig_Rate=("SIG_PRED", "mean"),
    Hyb_Rate=("HYB_PRED", "mean"),
).round(2)

decile_compare["Cur_Bias"]  = (decile_compare["Cur_Rate"] - decile_compare["Actual_Rate"]).round(2)
decile_compare["Sig_Bias"]  = (decile_compare["Sig_Rate"] - decile_compare["Actual_Rate"]).round(2)
decile_compare["Hyb_Bias"]  = (decile_compare["Hyb_Rate"] - decile_compare["Actual_Rate"]).round(2)
decile_compare["Sig_Delta"] = (decile_compare["Cur_Bias"].abs() - decile_compare["Sig_Bias"].abs()).round(2)
print(decile_compare[["N","Avg_Churn_Prob","Actual_Rate","Cur_Rate","Cur_Bias",
                        "Sig_Rate","Sig_Bias","Hyb_Rate","Hyb_Bias","Sig_Delta"]].to_string())


# ═══════════════════════════════════════════════════════════════════════════
# H — Binary threshold comparison (what % of churned contracts does CHURN_PCT capture?)
# ═══════════════════════════════════════════════════════════════════════════
hdr("H — Binary Threshold Lift", "How cleanly does CHURN_PCT separate actual churn/renew?")

df["TRUE_CHURN"] = (df["ACTUAL_RATE_PCT"] < 50.0).astype(int)
thresholds = np.arange(10, 90, 5)
thresh_rows = []
for thresh in thresholds:
    pred_c = (df["CHURN_PCT"] > thresh).astype(int)
    tp = int(((pred_c == 1) & (df["TRUE_CHURN"] == 1)).sum())
    fp = int(((pred_c == 1) & (df["TRUE_CHURN"] == 0)).sum())
    fn = int(((pred_c == 0) & (df["TRUE_CHURN"] == 1)).sum())
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    # When flagged as churn, what % actually churn?
    flagged_mean = float(df.loc[df["CHURN_PCT"] > thresh, "ACTUAL_RATE_PCT"].mean())
    thresh_rows.append({
        "Threshold": f">{thresh}%",
        "Flagged_N": int(pred_c.sum()),
        "Precision": round(prec, 3),
        "Recall":    round(rec,  3),
        "F1":        round(f1,   3),
        "Avg_Act_Rate_When_Flagged": round(flagged_mean, 1),
    })

thresh_df = pd.DataFrame(thresh_rows)
best_idx  = thresh_df["F1"].idxmax()
print(thresh_df.to_string(index=False))
best_thresh = int(thresh_df.loc[best_idx, "Threshold"].replace(">","").replace("%",""))
print(f"\n  *** Best F1 threshold: CHURN_PCT > {best_thresh}%  "
      f"(F1={thresh_df.loc[best_idx,'F1']:.3f}) ***")


# ═══════════════════════════════════════════════════════════════════════════
# I — Recommendation + parameter export
# ═══════════════════════════════════════════════════════════════════════════
hdr("I — Recommendation + Exportable Parameters")

# Compute overall improvement metrics
if not df_hold.empty:
    cp   = df_hold["CHURN_PCT"].to_numpy()
    ar   = df_hold["ACTUAL_RATE_PCT"].to_numpy()
    pp   = df_hold["PRED_RATE_PCT"].to_numpy()
    atr  = df_hold["ATR"].to_numpy()
    sig  = sigmoid_renewal(cp / 100.0, best_a, best_b)
    hyb  = hybrid_pred(cp, best_a, best_b, CHURN_CLIFF_PCT, high_churn_mean)
    r_cur = eval_model(cp, pp,  ar, atr=atr)
    r_sig = eval_model(cp, sig, ar, atr=atr)
    r_hyb = eval_model(cp, hyb, ar, atr=atr)

    print(f"\n  PRIMARY VALIDATION (2025 holdout — truly unseen):")
    print(f"    Current model:        ATR-MAE = {r_cur['atr_mae']:.2f}pp  |  ATR-Bias = {r_cur['atr_bias']:.2f}pp")
    print(f"    Sigmoid correction:   ATR-MAE = {r_sig['atr_mae']:.2f}pp  |  ATR-Bias = {r_sig['atr_bias']:.2f}pp")
    print(f"    Hybrid correction:    ATR-MAE = {r_hyb['atr_mae']:.2f}pp  |  ATR-Bias = {r_hyb['atr_bias']:.2f}pp")

    sig_improves  = r_sig["atr_mae"] < r_cur["atr_mae"]
    hyb_improves  = r_hyb["atr_mae"] < r_cur["atr_mae"]
    best_approach = "Hybrid" if (hyb_improves and r_hyb["atr_mae"] <= r_sig["atr_mae"]) else \
                    ("Sigmoid" if sig_improves else "Current (no change)")
    delta_chosen  = r_cur["atr_mae"] - (r_hyb["atr_mae"] if best_approach == "Hybrid" else r_sig["atr_mae"])
    print(f"\n  VERDICT: {best_approach} is recommended (Δ = {delta_chosen:.2f}pp ATR-weighted MAE)")

params_out = {
    "run_date":        str(pd.Timestamp.today().date()),
    "fit_window_end":  FIT_END,
    "hold_window":     f"{HOLD_START} → {HOLD_END}",
    "recent_window":   f"{RECENT_START} → latest matured",
    "portfolio_sigmoid": {
        "a":      float(best_a),
        "b":      float(best_b),
        "fit_mae_pp": float(fit_mae),
        "description": f"renewal_rate = 100 * (1 - sigmoid({best_a:.1f} * (p_churn - {best_b:.2f})))"
    },
    "piecewise_cliff_pct":  float(CHURN_CLIFF_PCT),
    "partial_mean_pct":     float(high_churn_mean),
    "best_binary_threshold_pct": float(best_thresh),
    "segment_sigmoids":     seg_params,
}

params_path = r"c:\Users\Nate.Fold\projects\TEMPLATES\Python\validate_sigmoid_correction_params.json"
try:
    with open(params_path, "w") as f:
        json.dump(params_out, f, indent=2)
    print(f"\n  Parameters exported → {params_path}")
except Exception as e:
    print(f"\n  Could not write params file: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Chart: 4-panel visualisation
# ═══════════════════════════════════════════════════════════════════════════
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    fig.suptitle(
        f"Sigmoid Correction Validation — V5 Matured Contracts\n"
        f"(fit pre-2025 | hold 2025 | recent 2026-YTD)",
        fontsize=12, fontweight="bold"
    )

    # ── Panel 1: Bimodal histogram ─────────────────────────────────────────
    ax = axes[0, 0]
    vals = df["ACTUAL_RATE_PCT"].clip(0, 100).dropna()
    ax.hist(vals, bins=40, color="#38BDF8", edgecolor="white", linewidth=0.4)
    ax.axvline(10, color="red",   linestyle="--", alpha=0.8, linewidth=1.2, label="10% (full churn)")
    ax.axvline(90, color="green", linestyle="--", alpha=0.8, linewidth=1.2, label="90% (full renew)")
    ax.set_title("Actual Renewal Rate Distribution")
    ax.set_xlabel("Actual Renewal Rate %"); ax.set_ylabel("Contracts")
    ax.legend(fontsize=8)

    # ── Panel 2: Risk curve empirical vs sigmoid vs current ───────────────
    ax = axes[0, 1]
    df["RISK_VENTILE"] = pd.qcut(df["CHURN_PCT"].rank(method="first"), q=20,
                                  labels=[f"V{i:02d}" for i in range(1, 21)])
    vc = df.groupby("RISK_VENTILE", observed=True).agg(
        Avg_Churn_Prob=("CHURN_PCT", "mean"),
        Avg_Actual=("ACTUAL_RATE_PCT", "mean"),
        Avg_Pred=("PRED_RATE_PCT", "mean"),
    )
    x_range = np.linspace(0, 100, 200)
    sig_line = sigmoid_renewal(x_range / 100.0, best_a, best_b)
    ax.scatter(vc["Avg_Churn_Prob"], vc["Avg_Actual"],  color="#38BDF8", s=55, zorder=4, label="Actual (ventile)")
    ax.scatter(vc["Avg_Churn_Prob"], vc["Avg_Pred"],    color="#F5B94A", s=35, alpha=0.8, zorder=3, label="Current model")
    ax.plot(x_range, sig_line, color="#FB923C", linewidth=2.0, linestyle="--",
            label=f"Sigmoid (a={best_a:.1f}, b={best_b:.2f})")
    ax.axvline(best_b * 100, color="#EF4444", linestyle=":", alpha=0.6, linewidth=1.2, label=f"Cliff @ {best_b*100:.0f}%")
    ax.set_title("Risk Score → Renewal Rate"); ax.set_xlabel("CHURN_PCT %"); ax.set_ylabel("Renewal Rate %")
    ax.legend(fontsize=7); ax.grid(True, alpha=0.2)

    # ── Panel 3: Decile bias comparison ───────────────────────────────────
    ax = axes[0, 2]
    x_d = np.arange(1, 11)
    width = 0.3
    ax.bar(x_d - width, decile_compare["Cur_Bias"],  width=width, color="#F5B94A", alpha=0.85, label="Current")
    ax.bar(x_d,         decile_compare["Sig_Bias"],  width=width, color="#38BDF8", alpha=0.85, label="Sigmoid")
    ax.bar(x_d + width, decile_compare["Hyb_Bias"],  width=width, color="#22C55E", alpha=0.85, label="Hybrid")
    ax.axhline(0, color="white", linewidth=0.8)
    ax.set_title("Decile Bias: Current vs Sigmoid vs Hybrid\n(D1=Riskiest)")
    ax.set_xlabel("Risk Decile"); ax.set_ylabel("Bias (pp)")
    ax.set_xticks(list(x_d)); ax.set_xticklabels([f"D{i}" for i in x_d])
    ax.legend(fontsize=8); ax.grid(True, alpha=0.2, axis="y")

    # ── Panel 4: ATR-MAE by segment and method ────────────────────────────
    ax = axes[1, 0]
    if seg_rows:
        sdf = pd.DataFrame(seg_rows)
        hold_sdf = sdf[sdf["Window"] == "Hold-2025"].copy()
        if not hold_sdf.empty:
            x_s = np.arange(len(hold_sdf))
            width = 0.35
            ax.bar(x_s - width/2, hold_sdf["Cur_MAE"], width=width, color="#F5B94A", alpha=0.85, label="Current MAE")
            ax.bar(x_s + width/2, hold_sdf["Sig_MAE"], width=width, color="#38BDF8", alpha=0.85, label="Sigmoid MAE")
            ax.set_title("Segment ATR-MAE: Current vs Sigmoid\n(Hold 2025)")
            ax.set_xlabel("Segment"); ax.set_ylabel("ATR-weighted MAE (pp)")
            ax.set_xticks(x_s); ax.set_xticklabels(hold_sdf["Segment"], rotation=20, ha="right")
            ax.legend(fontsize=8); ax.grid(True, alpha=0.2, axis="y")

    # ── Panel 5: F1 vs threshold ──────────────────────────────────────────
    ax = axes[1, 1]
    thresh_x = [int(r.replace(">","").replace("%","")) for r in thresh_df["Threshold"]]
    ax.plot(thresh_x, thresh_df["F1"],        color="#38BDF8", marker="o", markersize=4, linewidth=1.8, label="F1")
    ax.plot(thresh_x, thresh_df["Precision"], color="#22C55E", linestyle="--", linewidth=1.2, label="Precision")
    ax.plot(thresh_x, thresh_df["Recall"],    color="#F5B94A", linestyle="--", linewidth=1.2, label="Recall")
    ax.axvline(best_thresh, color="white", linestyle=":", alpha=0.7, label=f"Best threshold={best_thresh}%")
    ax.set_title("Binary Churn Threshold F1 Curve"); ax.set_xlabel("CHURN_PCT Threshold"); ax.set_ylabel("Score")
    ax.legend(fontsize=8); ax.grid(True, alpha=0.2)

    # ── Panel 6: Hold window scatter — current vs sigmoid error ──────────
    ax = axes[1, 2]
    if not df_hold.empty:
        cp_h  = df_hold["CHURN_PCT"].to_numpy()
        err_c = df_hold["PRED_RATE_PCT"].to_numpy() - df_hold["ACTUAL_RATE_PCT"].to_numpy()
        sig_h = sigmoid_renewal(cp_h / 100.0, best_a, best_b)
        err_s = sig_h - df_hold["ACTUAL_RATE_PCT"].to_numpy()
        # Bin by churn_pct
        bdf = pd.DataFrame({"CHURN_PCT": cp_h, "ERR_CUR": err_c, "ERR_SIG": err_s})
        bdf["BIN"] = pd.cut(bdf["CHURN_PCT"], bins=np.arange(0, 105, 10))
        bn = bdf.groupby("BIN", observed=True)[["ERR_CUR","ERR_SIG"]].mean()
        x_b = [(float(str(b).split(",")[0].strip("(")) + 10) / 2 for b in bn.index]
        ax.plot(x_b, bn["ERR_CUR"], color="#F5B94A", marker="o", markersize=5, linewidth=1.8, label="Current bias")
        ax.plot(x_b, bn["ERR_SIG"], color="#38BDF8", marker="s", markersize=5, linewidth=1.8, label="Sigmoid bias")
        ax.axhline(0, color="white", linewidth=0.8)
        ax.set_title("Bias by Churn Probability (2025 Holdout)"); ax.set_xlabel("CHURN_PCT bin"); ax.set_ylabel("Avg bias (pp)")
        ax.legend(fontsize=8); ax.grid(True, alpha=0.2)

    plt.tight_layout()
    out_path = r"c:\Users\Nate.Fold\projects\TEMPLATES\Python\validate_sigmoid_correction.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"\n  Chart saved → {out_path}")
except Exception as e:
    print(f"\n  Chart error: {e}")

print(f"\n{SEP}\nDone.\n{SEP}")
