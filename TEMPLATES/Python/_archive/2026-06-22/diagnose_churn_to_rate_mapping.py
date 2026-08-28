"""
diagnose_churn_to_rate_mapping.py
===================================
Answers the exact question: is CHURN_PCT → renewal rate truly linear, and
does a sigmoid fit on RECENT data (not stale pre-2025 era) improve accuracy?

The prior sigmoid test failed because it fit on 2021-2024 (avg rate ~79%)
and tested on 2025-2026 (avg rate ~72%). This script tests three things:

  Test 1 — Is the current mapping linear?
    Compare:
      A. Current model (ML_FORECAST / ATR)
      B. Naive linear: rate = (1 - CHURN_PCT/100) × 100
    If (B) beats (A), the calibration inflation is the core problem.

  Test 2 — Recent-era sigmoid (fit 2024, test 2025-2026)
    Fit the sigmoid ONLY on 2024 data (within same rate-regime as 2025+).
    Test on 2025 and 2026 separately.
    This is the honest test of whether a sigmoid helps in the current era.

  Test 3 — Per-segment recent sigmoid
    Fit per-segment on 2024 data, test on 2025+.
    Segments with bi-modal distributions may benefit even if portfolio doesn't.

  Test 4 — CHURN_PCT distribution analysis
    Does the model's CHURN_PCT actually follow a bimodal distribution?
    Or is the bimodality purely in the outcome space, not the probability space?

Usage:
    cd c:\\Users\\Nate.Fold\\projects
    .venv\\Scripts\\python.exe TEMPLATES\\Python\\diagnose_churn_to_rate_mapping.py

Output: console + diagnose_churn_to_rate_mapping.png
"""

from __future__ import annotations

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


def hdr(title: str, sub: str = "") -> None:
    print(f"\n{SEP}\n{title}")
    if sub:
        print(f"  {sub}")
    print(SEP)


def sigmoid_renewal(cp_0_1: np.ndarray, a: float, b: float) -> np.ndarray:
    z = a * (cp_0_1 - b)
    return 100.0 * (1.0 - 1.0 / (1.0 + np.exp(-np.clip(z, -40, 40))))


def fit_sigmoid(churn_pct: np.ndarray, actual_rate: np.ndarray,
                atr: np.ndarray | None = None) -> tuple[float, float, float]:
    """ATR-weighted grid search. Returns (a, b, mae)."""
    cp = churn_pct / 100.0
    w  = atr if atr is not None else np.ones(len(cp))
    w  = w / w.sum()
    best_mae, best_a, best_b = 999.0, 5.0, 0.5
    for a in np.arange(2.0, 25.0, 0.5):
        for b in np.arange(0.20, 0.95, 0.02):
            pred = sigmoid_renewal(cp, a, b)
            mae  = float(np.sum(w * np.abs(pred - actual_rate)))
            if mae < best_mae:
                best_mae, best_a, best_b = mae, a, b
    return best_a, best_b, best_mae


def eval_all(churn_pct: np.ndarray, pred_rate: np.ndarray,
             actual_rate: np.ndarray, atr: np.ndarray,
             sigmoid_params: tuple[float, float] | None = None,
             label: str = "") -> dict:
    valid = ~(np.isnan(churn_pct) | np.isnan(actual_rate))
    cp, pr, ar, w = churn_pct[valid], pred_rate[valid], actual_rate[valid], atr[valid]
    wn = w / w.sum()

    # Current model
    cur_mae  = float(np.sum(wn * np.abs(pr - ar)))
    cur_bias = float(np.sum(wn * (pr - ar)))

    # Naive linear: rate = (1 - churn_prob) × 100
    lin_pred = np.clip(100.0 - cp, 0.0, 100.0)
    lin_mae  = float(np.sum(wn * np.abs(lin_pred - ar)))
    lin_bias = float(np.sum(wn * (lin_pred - ar)))

    # Sigmoid (if params provided)
    if sigmoid_params:
        sig_pred = sigmoid_renewal(cp / 100.0, *sigmoid_params)
        sig_mae  = float(np.sum(wn * np.abs(sig_pred - ar)))
        sig_bias = float(np.sum(wn * (sig_pred - ar)))
    else:
        sig_pred, sig_mae, sig_bias = np.full_like(cp, np.nan), np.nan, np.nan

    return {
        "label": label, "n": int(valid.sum()),
        "cur_mae": round(cur_mae, 2), "cur_bias": round(cur_bias, 2),
        "lin_mae": round(lin_mae, 2), "lin_bias": round(lin_bias, 2),
        "sig_mae": round(sig_mae, 2) if not np.isnan(sig_mae) else None,
        "sig_bias": round(sig_bias, 2) if not np.isnan(sig_bias) else None,
    }


# ── Pull data ──────────────────────────────────────────────────────────────
hdr("Pulling matured data from V5_SANDBOX_APP_CONTRACT_DETAIL…")
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
    d.RETENTION_PCT,
    d.ACTUAL_RETAINED_ARR / NULLIF(d.ATR, 0) * 100.0  AS ACTUAL_RATE_PCT,
    d.ML_FORECAST          / NULLIF(d.ATR, 0) * 100.0  AS PRED_RATE_PCT,
    -- Direct linear baseline: what does (1 - P_CHURN) × 100 predict?
    -- CHURN_PCT in the app is P_CHURN_CAL × 100 (before level-shift calibration)
    -- PRED_RATE_PCT is RETENTION_RATIO × 100 (after level-shift calibration)
    -- Comparing these two tells us how much the calibration layer inflates the prediction.
    (100.0 - d.CHURN_PCT) AS LINEAR_PRED_RATE_PCT,      -- naive linear benchmark
    (d.ML_FORECAST / NULLIF(d.ATR, 0) * 100.0)
        - (100.0 - d.CHURN_PCT)                         AS CALIBRATION_INFLATION_PP
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
WHERE d.IS_MATURED_MONTH = TRUE
  AND d.ATR > 0
  AND d.ACTUAL_RETAINED_ARR IS NOT NULL
  AND d.CHURN_PCT IS NOT NULL
ORDER BY d.RENEWAL_DATE
"""

df = fetch_dataframe(SQL, conn=conn)
df["RENEWAL_MONTH"] = pd.to_datetime(df["RENEWAL_MONTH"])
print(f"  Rows: {len(df):,}  |  Range: {df['RENEWAL_MONTH'].min().date()} → {df['RENEWAL_MONTH'].max().date()}")

# ── Windows ────────────────────────────────────────────────────────────────
w_fit   = df[df["RENEWAL_MONTH"] <= "2023-12-31"]  # fit: 2021-2023 (old era)
w_2024  = df[(df["RENEWAL_MONTH"] >= "2024-01-01") & (df["RENEWAL_MONTH"] <= "2024-12-31")]  # recent fit era
w_2025  = df[(df["RENEWAL_MONTH"] >= "2025-01-01") & (df["RENEWAL_MONTH"] <= "2025-12-31")]  # hold
w_2026  = df[df["RENEWAL_MONTH"] >= "2026-01-01"]  # recent test

print(f"\n  Pre-2024 fit window: {len(w_fit):,} rows")
print(f"  2024 (recent fit):   {len(w_2024):,} rows")
print(f"  2025 (hold):         {len(w_2025):,} rows")
print(f"  2026 (recent test):  {len(w_2026):,} rows")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1 — Is the current mapping already non-linear (vs naive linear)?
# ═══════════════════════════════════════════════════════════════════════════
hdr("TEST 1 — Current Model vs Naive Linear  (1 − CHURN_PCT/100) × 100",
    "If naive linear beats the current model, the calibration is inflating predictions")

print(f"\n  {'Window':<30} {'N':>6}  {'Cur ATR-MAE':>12}  {'Lin ATR-MAE':>12}  {'Δ (cur−lin)':>12}  {'Cur Bias':>10}  {'Lin Bias':>10}")
print(f"  {'-'*96}")

for label, wdf in [
    ("Pre-2024 (old era)",  w_fit),
    ("2024",                w_2024),
    ("2025 holdout",        w_2025),
    ("2026 recent",         w_2026),
    ("All matured",         df),
]:
    if wdf.empty:
        continue
    r = eval_all(
        wdf["CHURN_PCT"].to_numpy(),
        wdf["PRED_RATE_PCT"].to_numpy(),
        wdf["ACTUAL_RATE_PCT"].to_numpy(),
        wdf["ATR"].to_numpy(),
        label=label,
    )
    delta = r["cur_mae"] - r["lin_mae"]
    direction = "✓ Lin better" if delta > 0 else "✗ Cur better"
    print(f"  {r['label']:<30} {r['n']:>6}  {r['cur_mae']:>11.2f}pp  "
          f"{r['lin_mae']:>11.2f}pp  {delta:>+11.2f}pp  {r['cur_bias']:>+9.2f}pp  {r['lin_bias']:>+9.2f}pp  {direction}")

# Calibration inflation analysis
hdr("TEST 1b — Calibration Inflation per Decile",
    "PRED_RATE_PCT − LINEAR_PRED_RATE_PCT shows how much the level-shift inflates each decile")

df["RISK_DECILE"] = pd.qcut(df["CHURN_PCT"].rank(method="first"), q=10,
                             labels=[f"D{i}" for i in range(1, 11)])
infl = df.groupby("RISK_DECILE", observed=True).agg(
    N=("CONTRACT_ID", "count"),
    Avg_CHURN_PCT=("CHURN_PCT", "mean"),
    Avg_PRED=("PRED_RATE_PCT", "mean"),
    Avg_LINEAR=("LINEAR_PRED_RATE_PCT", "mean"),
    Avg_ACTUAL=("ACTUAL_RATE_PCT", "mean"),
    Avg_INFL=("CALIBRATION_INFLATION_PP", "mean"),
).round(2)
infl["Cur_Bias"]  = (infl["Avg_PRED"]   - infl["Avg_ACTUAL"]).round(2)
infl["Lin_Bias"]  = (infl["Avg_LINEAR"] - infl["Avg_ACTUAL"]).round(2)
print("\n  NOTE: CALIBRATION_INFLATION = PRED_RATE_PCT − LINEAR_PRED_RATE_PCT")
print("  Positive = calibration layer INFLATES above the linear (1−p_churn) baseline\n")
print(infl[["N","Avg_CHURN_PCT","Avg_ACTUAL","Avg_LINEAR","Lin_Bias",
             "Avg_PRED","Avg_INFL","Cur_Bias"]].to_string())


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2 — Recent-era sigmoid (fit on 2024, test on 2025 and 2026)
# ═══════════════════════════════════════════════════════════════════════════
hdr("TEST 2 — Recent-era sigmoid: fit on 2024, test on 2025 and 2026",
    "This is the honest test — same rate-regime for fit and test")

if len(w_2024) >= 100:
    print(f"\n  Fitting sigmoid on 2024 ({len(w_2024):,} contracts)…")
    s_a, s_b, s_fit_mae = fit_sigmoid(
        w_2024["CHURN_PCT"].to_numpy(),
        w_2024["ACTUAL_RATE_PCT"].to_numpy(),
        atr=w_2024["ATR"].to_numpy(),
    )
    print(f"  Best-fit (2024 era): a={s_a:.1f}  b={s_b:.2f}  fit ATR-MAE={s_fit_mae:.2f}pp")
    print(f"  Interpretation: cliff at CHURN_PCT={s_b*100:.0f}%  |  steepness={s_a:.1f}")
    print(f"  Formula: rate = 100 × (1 − σ({s_a:.1f} × (p_churn − {s_b:.2f})))")

    for label, wdf in [("2025 holdout", w_2025), ("2026 recent", w_2026)]:
        if wdf.empty:
            continue
        r = eval_all(
            wdf["CHURN_PCT"].to_numpy(),
            wdf["PRED_RATE_PCT"].to_numpy(),
            wdf["ACTUAL_RATE_PCT"].to_numpy(),
            wdf["ATR"].to_numpy(),
            sigmoid_params=(s_a, s_b),
            label=label,
        )
        print(f"\n  {label}  (N={r['n']:,})")
        print(f"    {'Method':<25} {'ATR-MAE':>9}  {'ATR-Bias':>10}")
        print(f"    {'-'*48}")
        print(f"    {'Current model':<25} {r['cur_mae']:>8.2f}pp {r['cur_bias']:>9.2f}pp")
        print(f"    {'Naive linear':<25} {r['lin_mae']:>8.2f}pp {r['lin_bias']:>9.2f}pp")
        print(f"    {'Sigmoid (2024-fit)':<25} {r['sig_mae']:>8.2f}pp {r['sig_bias']:>9.2f}pp")
        deltas = {
            "cur vs linear": r["cur_mae"] - r["lin_mae"],
            "cur vs sigmoid": r["cur_mae"] - r["sig_mae"],
            "lin vs sigmoid": r["lin_mae"] - r["sig_mae"],
        }
        best = min(deltas, key=lambda k: -deltas[k])
        print(f"    → Best: {best.split(' vs ')[1]} (Δ={deltas[best]:+.2f}pp vs current)")
else:
    s_a, s_b = 10.0, 0.55
    print("  Insufficient 2024 data for recent-era sigmoid fit.")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3 — Per-segment recent sigmoid
# ═══════════════════════════════════════════════════════════════════════════
hdr("TEST 3 — Per-Segment Recent Sigmoid (fit 2024, test 2025)",
    "Segments with true bimodal CHURN_PCT distributions may benefit differently")

seg_sig_rows = []
for seg in df["SEGMENT"].dropna().unique():
    df_seg_2024 = w_2024[w_2024["SEGMENT"] == seg]
    df_seg_2025 = w_2025[w_2025["SEGMENT"] == seg]
    if len(df_seg_2024) < 30 or df_seg_2025.empty:
        continue

    # Check how bimodal the CHURN_PCT distribution IS for this segment
    cp_vals = df_seg_2024["CHURN_PCT"].to_numpy()
    low_frac  = float((cp_vals < 20).mean())   # confidently-safe zone
    high_frac = float((cp_vals > 70).mean())   # confidently-risky zone
    mid_frac  = float(((cp_vals >= 20) & (cp_vals <= 70)).mean())  # uncertain zone
    bimodal_score = low_frac + high_frac  # higher = more bimodal in prob space

    sa, sb, _ = fit_sigmoid(
        df_seg_2024["CHURN_PCT"].to_numpy(),
        df_seg_2024["ACTUAL_RATE_PCT"].to_numpy(),
        atr=df_seg_2024["ATR"].to_numpy(),
    )

    r = eval_all(
        df_seg_2025["CHURN_PCT"].to_numpy(),
        df_seg_2025["PRED_RATE_PCT"].to_numpy(),
        df_seg_2025["ACTUAL_RATE_PCT"].to_numpy(),
        df_seg_2025["ATR"].to_numpy(),
        sigmoid_params=(sa, sb),
        label=seg,
    )
    seg_sig_rows.append({
        "Segment":        seg,
        "N_fit":          len(df_seg_2024),
        "N_test":         r["n"],
        "CHURN_<20%":     round(low_frac * 100, 1),
        "CHURN_>70%":     round(high_frac * 100, 1),
        "CHURN_mid":      round(mid_frac * 100, 1),
        "Bimodal_Score":  round(bimodal_score, 2),
        "Sig_a":          sa,
        "Sig_b":          round(sb, 2),
        "Cur_ATR_MAE":    r["cur_mae"],
        "Lin_ATR_MAE":    r["lin_mae"],
        "Sig_ATR_MAE":    r["sig_mae"],
        "Cur_Bias":       r["cur_bias"],
        "Lin_Bias":       r["lin_bias"],
        "Sig_Bias":       r["sig_bias"],
        "Best":           "Sigmoid" if (r["sig_mae"] is not None and r["sig_mae"] < r["cur_mae"] and r["sig_mae"] < r["lin_mae"])
                          else ("Linear" if r["lin_mae"] < r["cur_mae"] else "Current"),
    })

if seg_sig_rows:
    sdf = pd.DataFrame(seg_sig_rows).sort_values("Bimodal_Score", ascending=False)
    print("\n  Higher Bimodal_Score = more contracts at extreme churn probabilities")
    print("  (theory: sigmoid should help segments where CHURN_PCT itself is bimodal)\n")
    print(sdf.to_string(index=False))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4 — Is CHURN_PCT bimodal, or just the outcomes?
# ═══════════════════════════════════════════════════════════════════════════
hdr("TEST 4 — CHURN_PCT Distribution: Is the PROBABILITY bimodal?",
    "Bimodal outcomes doesn't mean bimodal probabilities — let's check")

bins_cp = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100.01]
lbl_cp  = ["0-10","10-20","20-30","30-40","40-50","50-60","60-70","70-80","80-90","90-100"]

df["CHURN_BIN"] = pd.cut(df["CHURN_PCT"].clip(0, 100.001), bins=bins_cp, labels=lbl_cp, right=False)
cp_hist = df["CHURN_BIN"].value_counts().sort_index()
cp_pct  = (cp_hist / len(df) * 100).round(1)
cp_mean_actual = df.groupby("CHURN_BIN", observed=True)["ACTUAL_RATE_PCT"].mean().round(1)
cp_mean_pred   = df.groupby("CHURN_BIN", observed=True)["PRED_RATE_PCT"].mean().round(1)
cp_mean_linear = df.groupby("CHURN_BIN", observed=True)["LINEAR_PRED_RATE_PCT"].mean().round(1)
cp_mean_atrinfl = df.groupby("CHURN_BIN", observed=True)["CALIBRATION_INFLATION_PP"].mean().round(1)

q4 = pd.DataFrame({
    "N": cp_hist,
    "%_of_contracts": cp_pct,
    "Actual_Rate": cp_mean_actual,
    "Current_Pred": cp_mean_pred,
    "Linear_Pred (1-p)": cp_mean_linear,
    "Calib_Inflation_PP": cp_mean_atrinfl,
})
print("\n  NOTE: Linear_Pred = (1 - CHURN_PCT/100) × 100  (no model, just prob arithmetic)")
print("  Calib_Inflation = how much the calibration layer adds ON TOP of the linear baseline\n")
print(q4.to_string())

print(f"\n  CHURN_PCT bimodality:")
low_p  = float(cp_pct[cp_pct.index.isin(["0-10"])].sum())
high_p = float(cp_pct[cp_pct.index.isin(["90-100"])].sum())
mid_p  = 100.0 - low_p - high_p
print(f"    Contracts with CHURN_PCT 0-10%:  {low_p:.1f}%  ← these are 'safe' (and predicted correctly)")
print(f"    Contracts with CHURN_PCT 90-100%: {high_p:.1f}%  ← these are 'dangerous'")
print(f"    Contracts with CHURN_PCT 10-90%:  {mid_p:.1f}%  ← these are the 'uncertain' zone")
print(f"\n  Compare ACTUAL bimodality in OUTCOME:")
low_a  = float((df["ACTUAL_RATE_PCT"] < 10).mean() * 100)
high_a = float((df["ACTUAL_RATE_PCT"] > 90).mean() * 100)
mid_a  = 100.0 - low_a - high_a
print(f"    Actual rate < 10%:  {low_a:.1f}%  ← full churn events")
print(f"    Actual rate > 90%:  {high_a:.1f}%  ← full renew events")
print(f"    Actual rate 10-90%: {mid_a:.1f}%  ← partial outcomes")
print(f"\n  KEY QUESTION: Do the 'uncertain' contracts (CHURN_PCT 20-80%) actually")
print(f"  have bimodal OUTCOMES? If yes → sigmoid helps. If no → linear is fine.")

uncertain = df[(df["CHURN_PCT"] >= 20) & (df["CHURN_PCT"] <= 80)]
u_low  = float((uncertain["ACTUAL_RATE_PCT"] < 10).mean() * 100)
u_high = float((uncertain["ACTUAL_RATE_PCT"] > 90).mean() * 100)
u_mid  = 100.0 - u_low - u_high
print(f"\n  Outcome distribution for UNCERTAIN contracts (CHURN_PCT 20-80%):")
print(f"    Actually churned  (<10% renew):   {u_low:.1f}%")
print(f"    Actually renewed  (>90% renew):   {u_high:.1f}%")
print(f"    Partial outcome   (10-90% renew): {u_mid:.1f}%")
print(f"    → If the uncertain zone is still bimodal in OUTCOMES, then expected rate ≈ (1-p)×100")
print(f"    → If outcomes are mixed, the linear model is correct and sigmoid over-corrects")


# ═══════════════════════════════════════════════════════════════════════════
# Summary chart
# ═══════════════════════════════════════════════════════════════════════════
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    fig.suptitle(
        "Churn→Rate Mapping Diagnosis\n"
        "Is the sigmoid really needed, or is it a calibration inflation problem?",
        fontsize=12, fontweight="bold"
    )

    # ── Panel 1: CHURN_PCT distribution (bimodal check) ───────────────────
    ax = axes[0, 0]
    ax.bar(range(len(cp_hist)), cp_hist.values, color="#38BDF8", edgecolor="white", linewidth=0.4)
    ax.set_xticks(range(len(cp_hist)))
    ax.set_xticklabels(cp_hist.index, rotation=30, ha="right", fontsize=8)
    ax.set_title("CHURN_PCT Distribution\n(Is probability bimodal?)")
    ax.set_ylabel("Contracts")
    ax.set_xlabel("CHURN_PCT bin")

    # ── Panel 2: CHURN_PCT → actual rate (empirical) ─────────────────────
    ax = axes[0, 1]
    df["RISK_VENTILE"] = pd.qcut(df["CHURN_PCT"].rank(method="first"), q=20,
                                  labels=[f"V{i:02d}" for i in range(1, 21)])
    vc = df.groupby("RISK_VENTILE", observed=True).agg(
        CP=("CHURN_PCT", "mean"),
        AR=("ACTUAL_RATE_PCT", "mean"),
        PR=("PRED_RATE_PCT", "mean"),
        LR=("LINEAR_PRED_RATE_PCT", "mean"),
    )
    x_range = np.linspace(0, 100, 200)
    lin_line = np.clip(100 - x_range, 0, 100)

    ax.scatter(vc["CP"], vc["AR"], color="#F43F5E", s=55, zorder=5, label="Actual (ventile)")
    ax.scatter(vc["CP"], vc["PR"], color="#F5B94A", s=35, alpha=0.8, zorder=4, label="Current model")
    ax.plot(x_range, lin_line, color="#22C55E", linewidth=1.8, linestyle="--",
            label="Naive linear (1−p)×100", zorder=3)
    # Add recent sigmoid
    if len(w_2024) >= 100:
        sig_line = sigmoid_renewal(x_range / 100.0, s_a, s_b)
        ax.plot(x_range, sig_line, color="#FB923C", linewidth=2.0, linestyle=":",
                label=f"Sigmoid 2024-fit (a={s_a:.0f}, b={s_b:.2f})", zorder=3)
    ax.set_title("CHURN_PCT → Renewal Rate\n(Empirical vs Current vs Linear vs Sigmoid)")
    ax.set_xlabel("CHURN_PCT %"); ax.set_ylabel("Renewal Rate %")
    ax.legend(fontsize=7); ax.grid(True, alpha=0.2)

    # ── Panel 3: Calibration inflation by decile ──────────────────────────
    ax = axes[0, 2]
    x_d = np.arange(1, 11)
    w = 0.35
    bars1 = ax.bar(x_d - w/2, infl["Cur_Bias"].values,  width=w, color="#F5B94A", alpha=0.85, label="Current bias vs actual")
    bars2 = ax.bar(x_d + w/2, infl["Lin_Bias"].values,  width=w, color="#22C55E", alpha=0.85, label="Linear (1-p) bias vs actual")
    ax.axhline(0, color="white", linewidth=0.8)
    ax.set_xticks(x_d); ax.set_xticklabels([f"D{i}" for i in range(1, 11)])
    ax.set_title("Bias vs Actual: Current vs Naive Linear\n(Green=linear closer to 0 = calibration problem, not shape)")
    ax.set_xlabel("Risk Decile"); ax.set_ylabel("Bias (pp)")
    ax.legend(fontsize=8); ax.grid(True, alpha=0.2, axis="y")

    # ── Panel 4: Calibration inflation added by level-shift ───────────────
    ax = axes[1, 0]
    ax.bar(x_d, infl["Avg_INFL"].values, color="#FB923C", alpha=0.85, label="Calib inflation (pp)")
    ax.axhline(0, color="white", linewidth=0.8)
    ax.set_xticks(x_d); ax.set_xticklabels([f"D{i}" for i in range(1, 11)])
    ax.set_title("Calibration Inflation per Decile\n(PRED_RATE_PCT − Naive_Linear)")
    ax.set_xlabel("Risk Decile"); ax.set_ylabel("Inflation added (pp)")
    ax.grid(True, alpha=0.2, axis="y")

    # ── Panel 5: per-window comparison table ─────────────────────────────
    ax = axes[1, 1]
    win_labels = ["Pre-2024", "2024", "2025 Hold", "2026 Recent"]
    win_cur  = []
    win_lin  = []
    win_sig  = []
    for wdf in [w_fit, w_2024, w_2025, w_2026]:
        if wdf.empty:
            win_cur.append(0); win_lin.append(0); win_sig.append(0)
            continue
        cp_ = wdf["CHURN_PCT"].to_numpy()
        ar_ = wdf["ACTUAL_RATE_PCT"].to_numpy()
        pr_ = wdf["PRED_RATE_PCT"].to_numpy()
        w_  = wdf["ATR"].to_numpy()
        wn_ = w_ / w_.sum()
        win_cur.append(float(np.sum(wn_ * np.abs(pr_ - ar_))))
        lin_ = np.clip(100.0 - cp_, 0, 100)
        win_lin.append(float(np.sum(wn_ * np.abs(lin_ - ar_))))
        if len(w_2024) >= 100:
            sig_ = sigmoid_renewal(cp_ / 100.0, s_a, s_b)
            win_sig.append(float(np.sum(wn_ * np.abs(sig_ - ar_))))
        else:
            win_sig.append(0)
    xw = np.arange(len(win_labels))
    bw = 0.28
    ax.bar(xw - bw, win_cur, width=bw, color="#F5B94A", alpha=0.85, label="Current")
    ax.bar(xw,      win_lin, width=bw, color="#22C55E", alpha=0.85, label="Linear (1-p)")
    if any(v > 0 for v in win_sig):
        ax.bar(xw + bw, win_sig, width=bw, color="#FB923C", alpha=0.85, label="Sigmoid (2024-fit)")
    ax.set_xticks(xw); ax.set_xticklabels(win_labels)
    ax.set_title("ATR-MAE by Window and Method")
    ax.set_ylabel("ATR-weighted MAE (pp)"); ax.legend(fontsize=8); ax.grid(True, alpha=0.2, axis="y")

    # ── Panel 6: Uncertain zone outcome bimodality ────────────────────────
    ax = axes[1, 2]
    uc_vals = uncertain["ACTUAL_RATE_PCT"].clip(0, 100).dropna()
    ax.hist(uc_vals, bins=40, color="#FB923C", edgecolor="white", linewidth=0.3)
    ax.axvline(10, color="red",   linestyle="--", alpha=0.8, linewidth=1.2, label="10% = full churn")
    ax.axvline(90, color="green", linestyle="--", alpha=0.8, linewidth=1.2, label="90% = full renew")
    ax.set_title("Actual Outcomes for UNCERTAIN Contracts\n(CHURN_PCT 20-80%)")
    ax.set_xlabel("Actual Renewal Rate %"); ax.set_ylabel("Contracts")
    ax.legend(fontsize=8)

    plt.tight_layout()
    out = r"c:\Users\Nate.Fold\projects\TEMPLATES\Python\diagnose_churn_to_rate_mapping.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\n  Chart saved → {out}")
except Exception as e:
    print(f"\n  Chart error: {e}")

print(f"\n{SEP}\nDone.\n{SEP}")
