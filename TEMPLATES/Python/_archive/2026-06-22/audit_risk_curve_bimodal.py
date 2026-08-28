"""
audit_risk_curve_bimodal.py
============================
Answers the question: is the renewal outcome distribution bimodal, and
should the model use a nonlinear risk curve instead of a continuous rate?

Questions answered:
  Q1.  What fraction of contracts are Full Renew vs Partial vs Full Churn?
  Q2.  What does the actual rate distribution look like? (bimodal check)
  Q3.  Does our risk score separate the three buckets cleanly?
  Q4.  At each risk percentile decile, what % are full-churn vs partial vs full-renew?
  Q5.  What is the empirical nonlinear curve: risk_pctl → E[renewal_rate]?
  Q6.  For our worst misses (|error| > 20pp), what are their common features?
  Q7.  Curve-fit comparison: current linear vs nonlinear (sigmoid / piecewise) MAE
  Q8.  What churn-prob threshold would maximise F1 for labeling "will churn"?

Usage:
    cd c:\\Users\\Nate.Fold\\projects
    .venv\\Scripts\\python.exe TEMPLATES\\Python\\audit_risk_curve_bimodal.py

Output: prints tables + saves audit_risk_curve_bimodal.png
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
pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 120)

SEP = "=" * 90

def hdr(title: str, sub: str = ""):
    print(f"\n{SEP}\n{title}")
    if sub:
        print(f"  {sub}")
    print(SEP)

# ---------------------------------------------------------------------------
# Pull matured contract-level predictions + actuals
# ---------------------------------------------------------------------------
# Use V5_SANDBOX_APP_CONTRACT_DETAIL which already has:
#   CHURN_PCT (0-100), ATR, ACTUAL_RETAINED_ARR, IS_MATURED_MONTH,
#   SEGMENT, CONTRACT_RISK_PCTL_IN_SEG, ML_FORECAST
# Filter to MATURED = TRUE so we have ground truth.
# ---------------------------------------------------------------------------
conn = get_snowflake_connection()

PULL_SQL = """
SELECT
    d.CONTRACT_ID,
    d.RENEWAL_MONTH,
    d.SEGMENT,
    d.PRODUCT_PORTFOLIO,
    d.ATR,
    d.ACTUAL_RETAINED_ARR,
    d.ML_FORECAST,
    d.CHURN_PCT,                         -- model churn probability (0-100 scale)
    d.RETENTION_PCT,                     -- model retention probability (0-100 scale)
    d.CONTRACT_RISK_PCTL_IN_SEG,         -- within-segment risk percentile (0-100, higher = riskier)
    d.RENEWAL_DATE,
    -- Derived outcome labels
    CASE
        WHEN d.ATR <= 0 THEN NULL
        WHEN d.ACTUAL_RETAINED_ARR / d.ATR >= 0.95  THEN 'Full Renew'
        WHEN d.ACTUAL_RETAINED_ARR / d.ATR <= 0.05  THEN 'Full Churn'
        ELSE 'Partial'
    END AS OUTCOME_BUCKET,
    d.ACTUAL_RETAINED_ARR / NULLIF(d.ATR, 0) * 100.0  AS ACTUAL_RATE_PCT,
    d.ML_FORECAST          / NULLIF(d.ATR, 0) * 100.0  AS PRED_RATE_PCT,
    (d.ML_FORECAST / NULLIF(d.ATR, 0) * 100.0)
        - (d.ACTUAL_RETAINED_ARR / NULLIF(d.ATR, 0) * 100.0)  AS ERROR_PP   -- positive = over-predict
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
WHERE d.IS_MATURED_MONTH = TRUE
  AND d.ATR > 0
  AND d.ACTUAL_RETAINED_ARR IS NOT NULL
  AND d.CHURN_PCT IS NOT NULL
ORDER BY d.RENEWAL_MONTH, d.ATR DESC
"""

hdr("Pulling matured contract data from Snowflake…")
df = fetch_dataframe(PULL_SQL, conn=conn)
print(f"  Rows pulled: {len(df):,}  |  Unique contracts: {df['CONTRACT_ID'].nunique():,}")
print(f"  Renewal months: {df['RENEWAL_MONTH'].min()} → {df['RENEWAL_MONTH'].max()}")

if df.empty:
    print("No matured data found. Exiting.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Q1.  Outcome distribution
# ---------------------------------------------------------------------------
hdr("Q1 — Outcome Bucket Distribution", "Full Renew / Partial / Full Churn")
bucket_counts = df["OUTCOME_BUCKET"].value_counts(dropna=False)
bucket_pct    = (bucket_counts / len(df) * 100).round(1)
q1 = pd.DataFrame({"Count": bucket_counts, "%": bucket_pct})
print(q1.to_string())

# Dollar breakdown
hdr("Q1b — ATR-weighted outcome mix", "What % of $ is full-churn vs partial vs full-renew?")
dollar_mix = df.groupby("OUTCOME_BUCKET", dropna=False)["ATR"].sum().sort_values(ascending=False)
dollar_pct  = (dollar_mix / dollar_mix.sum() * 100).round(1)
print(pd.DataFrame({"ATR ($)": dollar_mix, "ATR %": dollar_pct}).to_string())

# ---------------------------------------------------------------------------
# Q2.  Distribution of actual renewal rates — bimodal check
# ---------------------------------------------------------------------------
hdr("Q2 — Actual Renewal Rate Distribution", "10-bucket histogram (% of contracts)")
bins   = np.arange(0, 110, 10)
labels = [f"{b}-{b+10}%" for b in bins[:-1]]
df["RATE_BUCKET"] = pd.cut(df["ACTUAL_RATE_PCT"].clip(0, 100), bins=bins, labels=labels, right=False)
rate_hist = df["RATE_BUCKET"].value_counts().sort_index()
rate_pct  = (rate_hist / len(df) * 100).round(1)
q2 = pd.DataFrame({"Contracts": rate_hist, "%": rate_pct})
print(q2.to_string())
print(f"\n  Bimodal check → 0-10% bucket: {rate_pct.iloc[0]:.1f}%   90-100% bucket: {rate_pct.iloc[-1]:.1f}%")
print(f"  Middle (10-90%) bucket: {rate_pct.iloc[1:-1].sum():.1f}%")

# ---------------------------------------------------------------------------
# Q3.  Risk score separation across outcome buckets
# ---------------------------------------------------------------------------
hdr("Q3 — CHURN_PCT by Outcome Bucket", "Does risk score cleanly separate the buckets?")
risk_by_bucket = df.groupby("OUTCOME_BUCKET")["CHURN_PCT"].describe(percentiles=[0.25, 0.5, 0.75]).round(1)
print(risk_by_bucket.to_string())

hdr("Q3b — CONTRACT_RISK_PCTL_IN_SEG by Outcome Bucket")
pctl_by_bucket = df.groupby("OUTCOME_BUCKET")["CONTRACT_RISK_PCTL_IN_SEG"].describe(percentiles=[0.25, 0.5, 0.75]).round(1)
print(pctl_by_bucket.to_string())

# ---------------------------------------------------------------------------
# Q4.  Decile breakdown: at each risk decile, what is the outcome mix?
# ---------------------------------------------------------------------------
hdr("Q4 — Risk Decile → Outcome Mix", "Each row = a risk decile (D1=riskiest)")
df["RISK_DECILE"] = pd.qcut(
    df["CHURN_PCT"].rank(method="first"),
    q=10,
    labels=[f"D{i}" for i in range(1, 11)]
)
decile_mix = (
    df.groupby(["RISK_DECILE", "OUTCOME_BUCKET"], observed=True)
      .size()
      .unstack(fill_value=0)
)
# Add row totals and percentages
decile_mix["Total"] = decile_mix.sum(axis=1)
for col in ["Full Churn", "Partial", "Full Renew"]:
    if col in decile_mix.columns:
        decile_mix[f"{col} %"] = (decile_mix[col] / decile_mix["Total"] * 100).round(1)
print(decile_mix.to_string())

# Average predicted rate and actual rate per decile
hdr("Q4b — Risk Decile → Avg Predicted Rate vs Avg Actual Rate")
decile_rates = df.groupby("RISK_DECILE", observed=True).agg(
    Pred_Rate_Pct=("PRED_RATE_PCT", "mean"),
    Actual_Rate_Pct=("ACTUAL_RATE_PCT", "mean"),
    Avg_Churn_Prob=("CHURN_PCT", "mean"),
    N=("CONTRACT_ID", "count"),
    ATR_M=("ATR", lambda x: x.sum() / 1e6),
).round(2)
decile_rates["Bias_PP"] = (decile_rates["Pred_Rate_Pct"] - decile_rates["Actual_Rate_Pct"]).round(2)
print(decile_rates.to_string())

# ---------------------------------------------------------------------------
# Q5.  Empirical nonlinear curve: risk percentile → E[renewal_rate]
# ---------------------------------------------------------------------------
hdr("Q5 — Empirical Risk Curve", "Ventile (20 buckets) of CHURN_PCT → mean actual rate")
df["RISK_VENTILE"] = pd.qcut(
    df["CHURN_PCT"].rank(method="first"),
    q=20,
    labels=[f"V{i:02d}" for i in range(1, 21)]
)
risk_curve = df.groupby("RISK_VENTILE", observed=True).agg(
    Avg_Churn_Prob=("CHURN_PCT", "mean"),
    Avg_Actual_Rate=("ACTUAL_RATE_PCT", "mean"),
    Avg_Pred_Rate=("PRED_RATE_PCT", "mean"),
    N=("CONTRACT_ID", "count"),
).round(2)
risk_curve["Bias_PP"] = (risk_curve["Avg_Pred_Rate"] - risk_curve["Avg_Actual_Rate"]).round(2)
print(risk_curve.to_string())

# ---------------------------------------------------------------------------
# Q6.  Worst misses: error > 20pp (over-prediction only)
# ---------------------------------------------------------------------------
hdr("Q6 — Worst Misses (over-predicted by >20pp)", "Common characteristics")
big_miss = df[df["ERROR_PP"] > 20].copy()
print(f"  Total big misses: {len(big_miss):,}  ({len(big_miss)/len(df)*100:.1f}% of matured)")
print(f"  Total ATR at risk in misses: ${big_miss['ATR'].sum():,.0f}")
if not big_miss.empty:
    print("\n  By Segment:")
    print(big_miss.groupby("SEGMENT")["CONTRACT_ID"].count().sort_values(ascending=False).to_string())
    print("\n  By Outcome Bucket (what actually happened to these over-predicted contracts):")
    print(big_miss["OUTCOME_BUCKET"].value_counts().to_string())
    print("\n  Avg CHURN_PCT for big misses vs all matured:")
    print(f"    Big misses avg churn prob: {big_miss['CHURN_PCT'].mean():.1f}%")
    print(f"    All matured avg churn prob: {df['CHURN_PCT'].mean():.1f}%")
    print("\n  Size distribution of big misses (ATR):")
    print(big_miss["ATR"].describe().round(0).to_string())
    print("\n  Rate breakdown: predicted vs actual for big misses:")
    print(f"    Avg predicted rate: {big_miss['PRED_RATE_PCT'].mean():.1f}%")
    print(f"    Avg actual rate:    {big_miss['ACTUAL_RATE_PCT'].mean():.1f}%")
    print(f"    Avg error:          {big_miss['ERROR_PP'].mean():.1f}pp")

# ---------------------------------------------------------------------------
# Q7.  Curve-fit comparison: linear vs nonlinear MAE
# ---------------------------------------------------------------------------
hdr("Q7 — Linear vs Nonlinear Curve MAE", "How much better would a sigmoid curve do?")
valid = df[["CHURN_PCT", "ACTUAL_RATE_PCT", "PRED_RATE_PCT"]].dropna()

# Current model MAE
current_mae  = np.mean(np.abs(valid["PRED_RATE_PCT"] - valid["ACTUAL_RATE_PCT"]))
current_bias = np.mean(valid["PRED_RATE_PCT"] - valid["ACTUAL_RATE_PCT"])

# Naive: predict the mean for everyone
naive_mae = np.mean(np.abs(valid["ACTUAL_RATE_PCT"].mean() - valid["ACTUAL_RATE_PCT"]))

# Empirical nonlinear: fit a 2nd-order polynomial on CHURN_PCT → ACTUAL_RATE_PCT
churn_scaled = valid["CHURN_PCT"] / 100.0
poly_coef = np.polyfit(churn_scaled, valid["ACTUAL_RATE_PCT"], deg=2)
poly_pred  = np.polyval(poly_coef, churn_scaled)
poly_mae   = np.mean(np.abs(poly_pred - valid["ACTUAL_RATE_PCT"]))
poly_bias  = np.mean(poly_pred - valid["ACTUAL_RATE_PCT"])

# Sigmoid curve: rate = 100 * (1 - sigmoid(a*(churn_prob - b)))
# Fit using simple grid search (no scipy dependency needed)
best_sig_mae, best_a, best_b = 999.0, 5.0, 0.5
for a in np.arange(2, 15, 0.5):
    for b in np.arange(0.1, 0.9, 0.05):
        sig = 1 / (1 + np.exp(-a * (churn_scaled - b)))
        sig_rate = 100.0 * (1 - sig)
        sig_mae  = float(np.mean(np.abs(sig_rate - valid["ACTUAL_RATE_PCT"])))
        if sig_mae < best_sig_mae:
            best_sig_mae, best_a, best_b = sig_mae, a, b

sig_pred  = 100.0 * (1 - 1 / (1 + np.exp(-best_a * (churn_scaled - best_b))))
sig_bias  = float(np.mean(sig_pred - valid["ACTUAL_RATE_PCT"]))

print(f"  N contracts: {len(valid):,}")
print(f"\n  {'Method':<30}  {'MAE (pp)':>10}  {'Bias (pp)':>10}")
print(f"  {'-'*52}")
print(f"  {'Naive (predict mean)':<30}  {naive_mae:>10.2f}  {'n/a':>10}")
print(f"  {'Current model (PRED_RATE_PCT)':<30}  {current_mae:>10.2f}  {current_bias:>10.2f}")
print(f"  {'Polynomial (degree 2)':<30}  {poly_mae:>10.2f}  {poly_bias:>10.2f}")
print(f"  {'Sigmoid (best-fit)':<30}  {best_sig_mae:>10.2f}  {sig_bias:>10.2f}")
print(f"\n  Best-fit sigmoid: rate = 100 × (1 − σ({best_a:.1f}×(p_churn − {best_b:.2f})))")
print(f"  Interpretation: at p_churn = {best_b:.0%} the curve crosses 50% renewal rate")

# ---------------------------------------------------------------------------
# Q8.  Optimal churn threshold for binary labeling
# ---------------------------------------------------------------------------
hdr("Q8 — Optimal Churn Threshold for Binary Classification")
# True label: ACTUAL_RATE_PCT < 50% → "churned"
df["TRUE_CHURN"] = (df["ACTUAL_RATE_PCT"] < 50.0).astype(int)
thresholds = np.arange(5, 90, 5)
results = []
for thresh in thresholds:
    pred_churn = (df["CHURN_PCT"] > thresh).astype(int)
    tp = int(((pred_churn == 1) & (df["TRUE_CHURN"] == 1)).sum())
    fp = int(((pred_churn == 1) & (df["TRUE_CHURN"] == 0)).sum())
    fn = int(((pred_churn == 0) & (df["TRUE_CHURN"] == 1)).sum())
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    results.append({"Threshold": f">{thresh}%", "Precision": prec, "Recall": rec, "F1": f1, "Flagged": int(pred_churn.sum())})
q8 = pd.DataFrame(results)
best_idx = q8["F1"].idxmax()
print(q8.round(3).to_string(index=False))
print(f"\n  *** Best F1 threshold: CHURN_PCT {q8.loc[best_idx,'Threshold']}  "
      f"(F1={q8.loc[best_idx,'F1']:.3f}, Precision={q8.loc[best_idx,'Precision']:.3f}, "
      f"Recall={q8.loc[best_idx,'Recall']:.3f}) ***")

# ---------------------------------------------------------------------------
# Save chart
# ---------------------------------------------------------------------------
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Risk Curve & Bimodal Analysis — V5 Matured Contracts", fontsize=13, fontweight="bold")

    # Panel 1: renewal rate histogram
    ax1 = axes[0, 0]
    vals = df["ACTUAL_RATE_PCT"].clip(0, 100).dropna()
    ax1.hist(vals, bins=40, color="#38BDF8", edgecolor="white", linewidth=0.3)
    ax1.set_title("Actual Renewal Rate Distribution")
    ax1.set_xlabel("Actual Renewal Rate %")
    ax1.set_ylabel("Contracts")
    ax1.axvline(5,  color="red",   linestyle="--", alpha=0.7, label="<5% = Full Churn")
    ax1.axvline(95, color="green", linestyle="--", alpha=0.7, label=">95% = Full Renew")
    ax1.legend(fontsize=8)

    # Panel 2: empirical risk curve (ventile)
    ax2 = axes[0, 1]
    ax2.scatter(risk_curve["Avg_Churn_Prob"], risk_curve["Avg_Actual_Rate"],
                color="#38BDF8", s=60, zorder=3, label="Actual (empirical)")
    ax2.scatter(risk_curve["Avg_Churn_Prob"], risk_curve["Avg_Pred_Rate"],
                color="#F5B94A", s=40, alpha=0.7, zorder=2, label="Predicted (model)")
    x_range = np.linspace(0, 100, 200)
    sig_line = 100.0 * (1 - 1 / (1 + np.exp(-best_a * (x_range / 100.0 - best_b))))
    ax2.plot(x_range, sig_line, color="#FB923C", linewidth=1.5, linestyle="--", label=f"Sigmoid fit (MAE={best_sig_mae:.1f}pp)")
    ax2.set_title("Risk Score → Renewal Rate (Empirical vs Model vs Sigmoid)")
    ax2.set_xlabel("CHURN_PCT (model churn probability %)")
    ax2.set_ylabel("Renewal Rate %")
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)

    # Panel 3: decile bias
    ax3 = axes[1, 0]
    x_d = range(1, 11)
    ax3.bar(x_d, decile_rates["Bias_PP"], color=["#EF4444" if b > 0 else "#22C55E" for b in decile_rates["Bias_PP"]])
    ax3.axhline(0, color="white", linewidth=0.8)
    ax3.set_title("Over/Under Prediction Bias by Risk Decile\n(D1=Riskiest, positive=over-predict)")
    ax3.set_xlabel("Risk Decile")
    ax3.set_ylabel("Bias (pp)")
    ax3.set_xticks(list(x_d))
    ax3.set_xticklabels([f"D{i}" for i in x_d])
    ax3.grid(True, alpha=0.2, axis="y")

    # Panel 4: F1 curve
    ax4 = axes[1, 1]
    ax4.plot([int(r.split(">")[1].replace("%","")) for r in q8["Threshold"]], q8["F1"],
             color="#38BDF8", marker="o", markersize=5, linewidth=1.8, label="F1")
    ax4.plot([int(r.split(">")[1].replace("%","")) for r in q8["Threshold"]], q8["Precision"],
             color="#22C55E", linestyle="--", linewidth=1.2, label="Precision")
    ax4.plot([int(r.split(">")[1].replace("%","")) for r in q8["Threshold"]], q8["Recall"],
             color="#F5B94A", linestyle="--", linewidth=1.2, label="Recall")
    best_thresh_val = int(q8.loc[best_idx, "Threshold"].split(">")[1].replace("%",""))
    ax4.axvline(best_thresh_val, color="white", linestyle=":", alpha=0.7)
    ax4.set_title(f"Churn Threshold vs F1/Precision/Recall\n(Best: >{best_thresh_val}%)")
    ax4.set_xlabel("CHURN_PCT Threshold")
    ax4.set_ylabel("Score")
    ax4.legend(fontsize=8)
    ax4.grid(True, alpha=0.2)

    plt.tight_layout()
    out_path = r"c:\Users\Nate.Fold\projects\TEMPLATES\Python\audit_risk_curve_bimodal.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"\n  Chart saved: {out_path}")
except Exception as e:
    print(f"\n  Chart skipped: {e}")

hdr("DONE — Summary of Findings")
print(f"  Full Renew share (contract count): {bucket_pct.get('Full Renew', 0):.1f}%")
print(f"  Full Churn share (contract count): {bucket_pct.get('Full Churn', 0):.1f}%")
print(f"  Partial share (contract count):    {bucket_pct.get('Partial', 0):.1f}%")
print(f"  Current model MAE:    {current_mae:.2f}pp  |  Bias: {current_bias:.2f}pp")
print(f"  Sigmoid curve MAE:    {best_sig_mae:.2f}pp")
print(f"  MAE improvement (sigmoid - current): {best_sig_mae - current_mae:+.2f}pp")
print(f"  Best binary churn threshold: CHURN_PCT {q8.loc[best_idx,'Threshold']}")
print()
