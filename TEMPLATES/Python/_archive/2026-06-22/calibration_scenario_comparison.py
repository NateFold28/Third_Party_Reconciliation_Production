"""
Parallel calibration scenario analysis — for board prep July 2026.

Compares FOUR approaches side-by-side:
  A) Current policy (May 28 offsets)
  B) Incremental recency-weighted correction (HL=12mo) — from propose_calibration_refresh
  C) Aggressive correction (HL=3mo) — tracks only last 3 months of bias
  D) Full flat correction — direct mean of last 6 settled months only

For each scenario:
  - Backtest bias on last 12 closed months
  - Simulated Jul-Sep 2026 renewal rate % and dollar totals
  - Stability risk (how much variance in the correction)
"""
import sys
from pathlib import Path
from datetime import date, timedelta
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection(); cur = conn.cursor()
for s in ["USE ROLE STREAMLIT_USER", "USE WAREHOUSE REPORTING_WH",
          "USE DATABASE STREAMLIT_APPS", "USE SCHEMA DBO"]:
    cur.execute(s)
print("Connected\n")

# ── Load segment-month history ────────────────────────────────────────────────
cur.execute("""
    SELECT
        DATE_TRUNC('month', RENEWAL_MONTH)    AS MO,
        SEGMENT,
        SUM(ATR)                              AS ATR,
        SUM(COALESCE(ACTUAL_RETAINED_ARR, 0)) AS ACTUAL,
        SUM(COALESCE(ML_FORECAST, 0))         AS ML_POSTCAL
    FROM V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURE = TRUE AND IS_MATURED_MONTH = TRUE
      AND COALESCE(ATR, 0) > 1000
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    GROUP BY 1, 2
    HAVING SUM(ATR) >= 500000
    ORDER BY 1, 2
""")
hist = pd.DataFrame(cur.fetchall(),
                    columns=["MO","SEGMENT","ATR","ACTUAL","ML_POSTCAL"])
for c in ["ATR","ACTUAL","ML_POSTCAL"]:
    hist[c] = pd.to_numeric(hist[c], errors="coerce").fillna(0)
hist["ACT_RATE"] = hist["ACTUAL"]    / hist["ATR"] * 100
hist["ML_RATE"]  = hist["ML_POSTCAL"]/ hist["ATR"] * 100
hist["ERROR_PP"] = hist["ML_RATE"]   - hist["ACT_RATE"]
hist["MO"]       = pd.to_datetime(hist["MO"])
hist["MO_STR"]   = hist["MO"].dt.strftime("%Y-%m")

# ── Load current policy ───────────────────────────────────────────────────────
cur.execute("""
    SELECT SEGMENT, OFFSET_PP FROM V5_CALIBRATION_POLICY
    WHERE CURRENT_DATE() BETWEEN EFFECTIVE_DATE AND COALESCE(EXPIRY_DATE,'9999-12-31')
    QUALIFY ROW_NUMBER() OVER (PARTITION BY SEGMENT ORDER BY EFFECTIVE_DATE DESC) = 1
""")
current_policy = {r[0]: float(r[1]) for r in cur.fetchall()}

# ── Load forward Jul-Sep 2026 ─────────────────────────────────────────────────
cur.execute("""
    SELECT
        DATE_TRUNC('month', RENEWAL_MONTH) AS MO,
        SEGMENT,
        SUM(ATR)                           AS ATR,
        SUM(COALESCE(ML_FORECAST, 0))      AS ML_POSTCAL
    FROM V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-09-30'
      AND IS_MATURE = FALSE
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    GROUP BY 1, 2 ORDER BY 1, 2
""")
fwd = pd.DataFrame(cur.fetchall(),
                   columns=["MO","SEGMENT","ATR","ML_POSTCAL"])
for c in ["ATR","ML_POSTCAL"]:
    fwd[c] = pd.to_numeric(fwd[c], errors="coerce").fillna(0)
fwd["MO"]       = pd.to_datetime(fwd["MO"])
fwd["MO_STR"]   = fwd["MO"].dt.strftime("%Y-%m")
fwd["ML_RATE"]  = fwd["ML_POSTCAL"] / fwd["ATR"] * 100
conn.close()

today    = pd.Timestamp.today().normalize()
segs     = sorted(hist["SEGMENT"].unique())

# ── Recency-weighted bias calculator ─────────────────────────────────────────
def recency_weighted_bias(seg_hist, halflife_months):
    months_ago = ((today.year  - seg_hist["MO"].dt.year)  * 12 +
                  (today.month - seg_hist["MO"].dt.month))
    wt  = np.exp(-np.log(2) / halflife_months * months_ago)
    num = (wt * seg_hist["ATR"] * seg_hist["ERROR_PP"]).sum()
    den = (wt * seg_hist["ATR"]).sum()
    return num / den if den > 0 else 0.0

# ── Flat recent bias (last N months) ─────────────────────────────────────────
def flat_recent_bias(seg_hist, n_months):
    cutoff = today - pd.DateOffset(months=n_months)
    sub    = seg_hist[seg_hist["MO"] >= cutoff]
    if len(sub) == 0:
        return 0.0
    num = (sub["ATR"] * sub["ERROR_PP"]).sum()
    den = sub["ATR"].sum()
    return num / den if den > 0 else 0.0

# ── Build scenario offsets ────────────────────────────────────────────────────
#  Offset = additional correction ON TOP of current policy
#  i.e., new_total_offset = current_policy + addl_correction
#  Final rate = ML_POSTCAL_rate + addl_correction

scenarios = {
    "A_Current":     {},           # no additional correction (baseline)
    "B_HL12":        {},           # HL=12 recency weighted (already computed)
    "C_HL3":         {},           # HL=3 aggressive
    "D_Flat6mo":     {},           # flat last-6-months bias
}

for seg in segs:
    sub = hist[hist["SEGMENT"] == seg]
    # Scenario A — current policy, no additional correction
    scenarios["A_Current"][seg] = 0.0

    # Scenario B — HL=12 (what propose_calibration_refresh.py showed)
    b_addl = -recency_weighted_bias(sub, 12) - current_policy.get(seg, 0.0)
    scenarios["B_HL12"][seg] = b_addl

    # Scenario C — HL=3 (very responsive)
    c_total = -recency_weighted_bias(sub, 3)
    c_addl  = c_total - current_policy.get(seg, 0.0)
    scenarios["C_HL3"][seg] = c_addl

    # Scenario D — flat last-6-months only
    d_total = -flat_recent_bias(sub, 6)
    d_addl  = d_total - current_policy.get(seg, 0.0)
    scenarios["D_Flat6mo"][seg] = d_addl

# ── Print offset table ────────────────────────────────────────────────────────
print("=" * 95)
print("CALIBRATION SCENARIO OFFSETS  (additional correction on top of current policy)")
print("=" * 95)
print(f"  NOTE: Current policy already has: "
      f"{', '.join(f'{s}={v:+.1f}pp' for s,v in sorted(current_policy.items()))}")
print()
print(f"  {'Segment':<22}  {'Cur Offset':>10}  {'B: +HL12':>9}  {'C: +HL3':>8}  "
      f"{'D: +Flat6':>9}  │  {'B Total':>8}  {'C Total':>8}  {'D Total':>8}")
print("  " + "-" * 90)
for seg in segs:
    cur_off = current_policy.get(seg, 0.0)
    b = scenarios["B_HL12"][seg]
    c = scenarios["C_HL3"][seg]
    d = scenarios["D_Flat6mo"][seg]
    print(f"  {seg:<22}  {cur_off:>+10.2f}pp  {b:>+9.2f}pp  {c:>+8.2f}pp"
          f"  {d:>+9.2f}pp  │  {cur_off+b:>+8.2f}pp  {cur_off+c:>+8.2f}pp"
          f"  {cur_off+d:>+8.2f}pp")

# ── Backtest all scenarios on last 12 months ──────────────────────────────────
print()
print("=" * 95)
print("BACKTEST — Last 12 closed months  (portfolio level)")
print("=" * 95)
last12_mos = sorted(hist["MO"].unique())[-12:]
bt = hist[hist["MO"].isin(last12_mos)].copy()

results = {sc: [] for sc in scenarios}
for mo, grp in bt.groupby("MO_STR"):
    atr    = grp["ATR"].sum()
    actual = grp["ACTUAL"].sum()
    act_r  = actual / atr * 100 if atr else 0

    sc_vals = {}
    for sc, addl_map in scenarios.items():
        fcst_d = 0.0
        for _, row in grp.iterrows():
            addl = addl_map.get(row["SEGMENT"], 0.0)
            fcst_d += row["ATR"] * (row["ML_RATE"] + addl) / 100
        sc_r = fcst_d / atr * 100 if atr else 0
        sc_vals[sc] = {"rate": sc_r, "bias": sc_r - act_r, "fcst": fcst_d}
    # best scenario (lowest |bias|)
    best = min(sc_vals, key=lambda s: abs(sc_vals[s]["bias"]))
    for sc in scenarios:
        results[sc].append({
            "MO": mo, "ACT_RATE": act_r, "RATE": sc_vals[sc]["rate"],
            "BIAS": sc_vals[sc]["bias"], "FCST": sc_vals[sc]["fcst"],
            "ATR": atr, "BEST": (sc == best)
        })

# Print month-by-month comparison
sc_labels = {"A_Current":"A:Current","B_HL12":"B:HL12","C_HL3":"C:HL3","D_Flat6mo":"D:Flat6"}
print(f"  {'Month':<8}  {'Actual%':>7}  "
      + "  ".join(f"{sc_labels[sc]:>10}" for sc in scenarios)
      + "  Winner")
print("  " + "-" * 78)
all_results = {sc: pd.DataFrame(results[sc]) for sc in scenarios}
months_ref  = all_results["A_Current"]["MO"].tolist()
for idx, mo in enumerate(months_ref):
    act_r = all_results["A_Current"].loc[idx, "ACT_RATE"]
    biases = {sc: all_results[sc].loc[idx, "BIAS"] for sc in scenarios}
    best   = min(biases, key=lambda s: abs(biases[s]))
    row_str = f"  {mo:<8}  {act_r:>6.2f}%  "
    row_str += "  ".join(
        f"{biases[sc]:>+9.2f}pp" + ("*" if sc == best else " ")
        for sc in scenarios
    )
    print(row_str)

print("  " + "-" * 78)
print(f"  {'MEAN'::<8}  {'':>7}  ", end="")
for sc in scenarios:
    mean_b = all_results[sc]["BIAS"].mean()
    print(f"{mean_b:>+9.2f}pp   ", end="")
print()
print()
print(f"  {'WINS'::<8}  {'':>7}  ", end="")
for sc in scenarios:
    wins = all_results[sc]["BEST"].sum() if "BEST" in all_results[sc].columns else 0
    wins = sum(1 for idx, mo in enumerate(months_ref)
               if min({s: abs(all_results[s].loc[idx,"BIAS"]) for s in scenarios},
                      key=lambda s: abs(all_results[s].loc[idx,"BIAS"])) == sc)
    print(f"  {wins:>3}/12 months   ", end="")
print()

# ── Forward simulation Jul-Sep 2026 ──────────────────────────────────────────
print()
print("=" * 95)
print("Q3-2026 FORWARD FORECAST — Jul, Aug, Sep 2026  (portfolio level)")
print("=" * 95)
print(f"  {'Month':<8}  {'ATR $M':>7}  "
      + "  ".join(f"{sc_labels[sc]:>11}" for sc in scenarios))
print("  " + "-" * 78)

q3_totals = {sc: {"atr": 0, "fcst": 0} for sc in scenarios}
for mo, grp in fwd.groupby("MO_STR"):
    atr = grp["ATR"].sum()
    sc_row = {}
    for sc, addl_map in scenarios.items():
        fcst = sum(row["ATR"] * (row["ML_RATE"] + addl_map.get(row["SEGMENT"], 0.0)) / 100
                   for _, row in grp.iterrows())
        sc_row[sc] = fcst / atr * 100 if atr else 0
        q3_totals[sc]["atr"]  += atr
        q3_totals[sc]["fcst"] += fcst
    row_str = f"  {mo:<8}  ${atr/1e6:>6.2f}M  "
    row_str += "  ".join(f"{sc_row[sc]:>10.2f}%  " for sc in scenarios)
    print(row_str)

print()
q3_atr = q3_totals["A_Current"]["atr"]
print(f"  {'Q3 Total'::<8}  ${q3_atr/1e6:>6.2f}M  "
      + "  ".join(
          f"${q3_totals[sc]['fcst']/1e6:>6.2f}M({q3_totals[sc]['fcst']/q3_atr*100:.1f}%)  "
          for sc in scenarios))

print()
print(f"  Q3 dollar impact vs current:")
cur_q3 = q3_totals["A_Current"]["fcst"]
for sc in scenarios:
    diff = q3_totals[sc]["fcst"] - cur_q3
    if diff != 0:
        print(f"    {sc_labels[sc]}: {diff/1e6:>+.2f}M")

# ── Stability analysis ────────────────────────────────────────────────────────
print()
print("=" * 95)
print("STABILITY RISK — how much do proposed offsets vary month-to-month?")
print("(high variance = unreliable; stable = trustworthy for board)")
print("=" * 95)
print(f"  {'Scenario':<14}  {'Mean Bias':>10}  {'Std Dev':>8}  {'Min':>8}  {'Max':>8}  "
      f"{'|Bias|>5pp months':>18}")
print("  " + "-" * 75)
for sc in scenarios:
    biases = all_results[sc]["BIAS"]
    n_large = (biases.abs() > 5).sum()
    print(f"  {sc_labels[sc]:<14}  {biases.mean():>+9.2f}pp  {biases.std():>7.2f}pp"
          f"  {biases.min():>+7.2f}pp  {biases.max():>+7.2f}pp  {n_large:>10}/12")

# ── Recommendation ────────────────────────────────────────────────────────────
print()
print("=" * 95)
print("RECOMMENDATION SUMMARY")
print("=" * 95)
means = {sc: all_results[sc]["BIAS"].mean() for sc in scenarios}
stds  = {sc: all_results[sc]["BIAS"].std()  for sc in scenarios}
print(f"""
  A  Current policy:   {means['A_Current']:>+.2f}pp mean bias | std {stds['A_Current']:.2f}pp
     → Numbers the board would see TODAY without any change.

  B  HL=12 correction: {means['B_HL12']:>+.2f}pp mean bias | std {stds['B_HL12']:.2f}pp
     → Small incremental fix, low disruption, still significantly biased.

  C  HL=3 correction:  {means['C_HL3']:>+.2f}pp mean bias | std {stds['C_HL3']:.2f}pp
     → Aggressively tracks recent regime. Good if bias is persistent; risky if Apr-2026 
       (+5pp) is a sign the business is recovering.

  D  Flat 6-month:     {means['D_Flat6mo']:>+.2f}pp mean bias | std {stds['D_Flat6mo']:.2f}pp
     → Direct "what did the last 6 months look like" correction. Most interpretable.
""")

# ── Apply SQL for the best scenario ──────────────────────────────────────────
print("=" * 95)
print("APPLY SQL for Scenario C (HL=3) and D (Flat 6mo) — review before running")
print("=" * 95)
eff  = date.today()
exp  = eff + timedelta(days=90)
for sc_name, sc_key in [("C — HL=3 aggressive", "C_HL3"),
                         ("D — Flat 6-month",    "D_Flat6mo")]:
    print(f"\n  -- ====== {sc_name} ======")
    print(f"  UPDATE STREAMLIT_APPS.DBO.V5_CALIBRATION_POLICY")
    print(f"    SET EXPIRY_DATE = '{eff}'")
    print(f"    WHERE EXPIRY_DATE > '{eff}' OR EXPIRY_DATE IS NULL;")
    print(f"  INSERT INTO STREAMLIT_APPS.DBO.V5_CALIBRATION_POLICY")
    print(f"    (SEGMENT, OFFSET_PP, EFFECTIVE_DATE, EXPIRY_DATE) VALUES")
    rows = []
    for seg in segs:
        total = current_policy.get(seg, 0.0) + scenarios[sc_key].get(seg, 0.0)
        rows.append(f"    ('{seg}', {total:.3f}, '{eff}', '{exp}')")
    print(",\n".join(rows) + ";")
    print(f"  CALL STREAMLIT_APPS.DBO.SP_V5_SANDBOX_DAILY_REFRESH();")

print("""
  *** Read the RECOMMENDATION SUMMARY above before choosing a scenario. ***
  *** No changes have been made — these statements are ready to copy. ***
""")
