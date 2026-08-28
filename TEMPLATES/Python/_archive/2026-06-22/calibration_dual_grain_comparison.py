"""
Dual-grain calibration validation:
  - Portfolio level: SUM(forecast) / SUM(ATR) vs actuals — what the board sees
  - Contract level:  per-contract |forecast_rate - actual_rate| — what drives watchlist accuracy

Shows all 4 scenarios at both grains so you can see if a scenario fixes portfolio
but over-corrects individual contracts (or vice versa).
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

# ── 1. Portfolio grain: segment × month aggregates ────────────────────────────
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
seg_mo = pd.DataFrame(cur.fetchall(),
                      columns=["MO","SEGMENT","ATR","ACTUAL","ML_POSTCAL"])

# ── 2. Contract grain: individual contract rows (last 12 settled months) ──────
cur.execute("""
    SELECT
        DATE_TRUNC('month', RENEWAL_MONTH) AS MO,
        CONTRACT_ID,
        SEGMENT,
        ATR,
        COALESCE(ACTUAL_RETAINED_ARR, 0)   AS ACTUAL,
        COALESCE(ML_FORECAST, 0)           AS ML_POSTCAL
    FROM V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURE = TRUE AND IS_MATURED_MONTH = TRUE
      AND COALESCE(ATR, 0) > 1000
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
      AND RENEWAL_MONTH >= DATEADD('month', -12, DATE_TRUNC('month', CURRENT_DATE()))
    ORDER BY 1, 2
""")
contracts = pd.DataFrame(cur.fetchall(),
                         columns=["MO","CONTRACT_ID","SEGMENT","ATR","ACTUAL","ML_POSTCAL"])

# ── 3. Forward contracts Jul-Sep 2026 (contract grain) ───────────────────────
cur.execute("""
    SELECT
        DATE_TRUNC('month', RENEWAL_MONTH) AS MO,
        CONTRACT_ID,
        PARTNER,
        SEGMENT,
        ATR,
        COALESCE(ML_FORECAST, 0)           AS ML_POSTCAL,
        CHURN_PCT,
        CONTRACT_RISK_TIER_RELATIVE        AS TIER
    FROM V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-09-30'
      AND IS_MATURE = FALSE
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
      AND COALESCE(ATR, 0) > 1000
    ORDER BY ATR DESC
""")
fwd_c = pd.DataFrame(cur.fetchall(),
                     columns=["MO","CONTRACT_ID","PARTNER","SEGMENT","ATR",
                               "ML_POSTCAL","CHURN_PCT","TIER"])

# ── 4. Current policy ─────────────────────────────────────────────────────────
cur.execute("""
    SELECT SEGMENT, OFFSET_PP FROM V5_CALIBRATION_POLICY
    WHERE CURRENT_DATE() BETWEEN EFFECTIVE_DATE AND COALESCE(EXPIRY_DATE,'9999-12-31')
    QUALIFY ROW_NUMBER() OVER (PARTITION BY SEGMENT ORDER BY EFFECTIVE_DATE DESC) = 1
""")
current_policy = {r[0]: float(r[1]) for r in cur.fetchall()}
conn.close()

for df in [seg_mo, contracts]:
    for c in ["ATR","ACTUAL","ML_POSTCAL"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
for c in ["ATR","ML_POSTCAL","CHURN_PCT"]:
    fwd_c[c] = pd.to_numeric(fwd_c[c], errors="coerce").fillna(0)

seg_mo["MO"]    = pd.to_datetime(seg_mo["MO"])
contracts["MO"] = pd.to_datetime(contracts["MO"])
fwd_c["MO"]     = pd.to_datetime(fwd_c["MO"])

seg_mo["ML_RATE"]    = seg_mo["ML_POSTCAL"]    / seg_mo["ATR"]    * 100
seg_mo["ACT_RATE"]   = seg_mo["ACTUAL"]        / seg_mo["ATR"]    * 100
seg_mo["ERROR_PP"]   = seg_mo["ML_RATE"]       - seg_mo["ACT_RATE"]
contracts["ML_RATE"] = contracts["ML_POSTCAL"] / contracts["ATR"] * 100
contracts["ACT_RATE"]= contracts["ACTUAL"]     / contracts["ATR"] * 100
fwd_c["ML_RATE"]     = fwd_c["ML_POSTCAL"]     / fwd_c["ATR"]     * 100
fwd_c["MO_STR"]      = fwd_c["MO"].dt.strftime("%Y-%m")

today    = pd.Timestamp.today().normalize()
segs     = sorted(seg_mo["SEGMENT"].unique())
FALLBACK = 0.0

def recency_bias(seg, halflife):
    sub = seg_mo[seg_mo["SEGMENT"] == seg]
    if len(sub) == 0:
        return FALLBACK
    months_ago = ((today.year  - sub["MO"].dt.year)  * 12 +
                  (today.month - sub["MO"].dt.month))
    wt  = np.exp(-np.log(2) / halflife * months_ago)
    num = (wt * sub["ATR"] * sub["ERROR_PP"]).sum()
    den = (wt * sub["ATR"]).sum()
    return num / den if den > 0 else FALLBACK

def flat_n_bias(seg, n_months):
    cutoff = today - pd.DateOffset(months=n_months)
    sub    = seg_mo[(seg_mo["SEGMENT"] == seg) & (seg_mo["MO"] >= cutoff)]
    if len(sub) == 0:
        return FALLBACK
    num = (sub["ATR"] * sub["ERROR_PP"]).sum()
    den = sub["ATR"].sum()
    return num / den if den > 0 else FALLBACK

# ── Build addl corrections for each scenario (ON TOP of current policy) ───────
scenarios = {}
for sc, fn in [
    ("A_Current", lambda seg: 0.0),
    ("B_HL12",    lambda seg: -recency_bias(seg, 12) - current_policy.get(seg, 0.0)),
    ("C_HL3",     lambda seg: -recency_bias(seg, 3)  - current_policy.get(seg, 0.0)),
    ("D_Flat6",   lambda seg: -flat_n_bias(seg, 6)   - current_policy.get(seg, 0.0)),
]:
    scenarios[sc] = {seg: fn(seg) for seg in segs}

SC_LABELS = {"A_Current":"A:Current","B_HL12":"B:HL12","C_HL3":"C:HL3","D_Flat6":"D:Flat6"}

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: PORTFOLIO GRAIN — monthly totals
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 105)
print("SECTION 1 — PORTFOLIO GRAIN  (what the board renewal rate % shows)")
print("=" * 105)

last12_mos = sorted(seg_mo["MO"].unique())[-12:]
bt = seg_mo[seg_mo["MO"].isin(last12_mos)].copy()

port_rows = []
for mo, grp in bt.groupby(bt["MO"].dt.strftime("%Y-%m")):
    atr    = grp["ATR"].sum()
    actual = grp["ACTUAL"].sum()
    act_r  = actual / atr * 100 if atr else 0
    row    = {"MO": mo, "ATR": atr, "ACT_RATE": act_r}
    for sc, addl in scenarios.items():
        fcst = sum(r["ATR"] * (r["ML_RATE"] + addl.get(r["SEGMENT"], 0.0)) / 100
                   for _, r in grp.iterrows())
        row[sc + "_RATE"] = fcst / atr * 100 if atr else 0
        row[sc + "_BIAS"] = row[sc + "_RATE"] - act_r
    port_rows.append(row)
port = pd.DataFrame(port_rows).sort_values("MO")

print(f"\n  {'Month':<8}  {'Act%':>6}  "
      + "  ".join(f"{SC_LABELS[sc]:>11}" for sc in scenarios))
print("  " + "-" * 72)
for _, r in port.iterrows():
    biases = {sc: r[sc+"_BIAS"] for sc in scenarios}
    best   = min(biases, key=lambda s: abs(biases[s]))
    line   = f"  {r['MO']:<8}  {r['ACT_RATE']:>5.2f}%  "
    line  += "  ".join(
        f"{biases[sc]:>+9.2f}pp" + ("*" if sc == best else " ")
        for sc in scenarios)
    print(line)
print("  " + "-" * 72)
print(f"  {'MEAN':<8}  {'':>6}  "
      + "  ".join(f"  {port[sc+'_BIAS'].mean():>+8.2f}pp " for sc in scenarios))

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: CONTRACT GRAIN — per-contract MAE by month
# ═══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 105)
print("SECTION 2 — CONTRACT GRAIN  (per-contract MAE — what drives watchlist accuracy)")
print("=" * 105)

ctr_rows = []
for mo, grp in contracts.groupby(contracts["MO"].dt.strftime("%Y-%m")):
    atr    = grp["ATR"].sum()
    act_r  = grp["ACTUAL"].sum() / atr * 100 if atr else 0
    row    = {"MO": mo, "N": len(grp), "ACT_RATE": act_r}
    for sc, addl in scenarios.items():
        adj_rate  = grp["ML_RATE"] + grp["SEGMENT"].map(addl).fillna(0.0)
        act_rate  = grp["ACT_RATE"]
        # Contract MAE (pp)
        c_mae     = (adj_rate - act_rate).abs().mean()
        # ATR-weighted MAE $
        atr_wt_err= (grp["ATR"] * (adj_rate - act_rate).abs() / 100).mean()
        # Within-10pp accuracy
        within10  = ((adj_rate - act_rate).abs() <= 10).mean() * 100
        row[sc+"_MAE_PP"] = c_mae
        row[sc+"_MAE_$"]  = atr_wt_err
        row[sc+"_W10"]    = within10
    ctr_rows.append(row)
ctr = pd.DataFrame(ctr_rows).sort_values("MO")

print(f"\n  Contract MAE (pp) — lower is better")
print(f"  {'Month':<8}  {'N Ctrs':>6}  "
      + "  ".join(f"{SC_LABELS[sc]:>10}" for sc in scenarios))
print("  " + "-" * 68)
for _, r in ctr.iterrows():
    maes = {sc: r[sc+"_MAE_PP"] for sc in scenarios}
    best = min(maes, key=lambda s: maes[s])
    line = f"  {r['MO']:<8}  {r['N']:>6}  "
    line += "  ".join(
        f"{maes[sc]:>9.2f}pp" + ("*" if sc == best else " ")
        for sc in scenarios)
    print(line)
print("  " + "-" * 68)
print(f"  {'MEAN':<8}  {'':>6}  "
      + "  ".join(f" {ctr[sc+'_MAE_PP'].mean():>9.2f}pp " for sc in scenarios))

print(f"\n  Within-10pp accuracy % — higher is better")
print(f"  {'Month':<8}  "
      + "  ".join(f"{SC_LABELS[sc]:>10}" for sc in scenarios))
print("  " + "-" * 55)
for _, r in ctr.iterrows():
    w10s = {sc: r[sc+"_W10"] for sc in scenarios}
    best = max(w10s, key=lambda s: w10s[s])
    line = f"  {r['MO']:<8}  "
    line += "  ".join(
        f"{w10s[sc]:>9.1f}%" + ("*" if sc == best else " ")
        for sc in scenarios)
    print(line)
print("  " + "-" * 55)
print(f"  {'MEAN':<8}  "
      + "  ".join(f" {ctr[sc+'_W10'].mean():>9.1f}%  " for sc in scenarios))

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: PER-SEGMENT accuracy — does one scenario hurt a specific segment?
# ═══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 105)
print("SECTION 3 — PER-SEGMENT contract MAE (pp) — last 12 months")
print("  Catches over-correction: a scenario may improve portfolio but hurt a segment")
print("=" * 105)
print(f"\n  {'Segment':<22}  {'N':>5}  "
      + "  ".join(f"{SC_LABELS[sc]:>10}" for sc in scenarios))
print("  " + "-" * 75)
for seg in segs:
    sub = contracts[contracts["SEGMENT"] == seg]
    if len(sub) == 0:
        continue
    row_parts = [f"  {seg:<22}  {len(sub):>5}  "]
    maes = {}
    for sc, addl in scenarios.items():
        adj   = sub["ML_RATE"] + addl.get(seg, 0.0)
        mae   = (adj - sub["ACT_RATE"]).abs().mean()
        maes[sc] = mae
    best = min(maes, key=lambda s: maes[s])
    row_parts += [
        f"{maes[sc]:>9.2f}pp" + ("*" if sc == best else " ")
        for sc in scenarios]
    print("".join(row_parts))

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: FORWARD Jul-Sep — contract + portfolio view per scenario
# ═══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 105)
print("SECTION 4 — FORWARD Jul-Sep 2026: portfolio rate% AND contract distribution")
print("=" * 105)

for sc, addl in scenarios.items():
    fwd_c[sc+"_RATE"] = fwd_c["ML_RATE"] + fwd_c["SEGMENT"].map(addl).fillna(0.0)
    fwd_c[sc+"_FCST"] = fwd_c["ATR"] * fwd_c[sc+"_RATE"] / 100

print(f"\n  Portfolio renewal rate% by month:")
print(f"  {'Month':<8}  {'ATR $M':>7}  "
      + "  ".join(f"{SC_LABELS[sc]:>11}" for sc in scenarios))
print("  " + "-" * 72)
for mo, grp in fwd_c.groupby("MO_STR"):
    atr = grp["ATR"].sum()
    line = f"  {mo:<8}  ${atr/1e6:>6.2f}M  "
    line += "  ".join(
        f"{grp[sc+'_FCST'].sum()/atr*100:>10.2f}%  " for sc in scenarios)
    print(line)

total_atr = fwd_c["ATR"].sum()
print(f"\n  Q3 totals:")
print(f"  {'':8}  ${total_atr/1e6:>6.2f}M  "
      + "  ".join(
          f"${fwd_c[sc+'_FCST'].sum()/1e6:>6.2f}M({fwd_c[sc+'_FCST'].sum()/total_atr*100:.1f}%)  "
          for sc in scenarios))

print(f"\n  Contract distribution (median renewal rate% per scenario):")
print(f"  {'Segment':<22}  {'N':>4}  "
      + "  ".join(f"{SC_LABELS[sc]:>12}" for sc in scenarios))
print("  " + "-" * 75)
for seg in segs:
    sub = fwd_c[fwd_c["SEGMENT"] == seg]
    if len(sub) == 0:
        continue
    line = f"  {seg:<22}  {len(sub):>4}  "
    line += "  ".join(f"{sub[sc+'_RATE'].median():>11.1f}%  " for sc in scenarios)
    print(line)

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: FINAL SCORECARD
# ═══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 105)
print("FINAL SCORECARD — Portfolio vs Contract grain alignment")
print("=" * 105)
print(f"\n  {'Metric':<38}  "
      + "  ".join(f"{SC_LABELS[sc]:>12}" for sc in scenarios))
print("  " + "-" * 90)

metrics = [
    ("Portfolio mean bias pp (last 12mo)",
     lambda sc: port[sc+"_BIAS"].mean()),
    ("Portfolio std dev pp (last 12mo)",
     lambda sc: port[sc+"_BIAS"].std()),
    ("Portfolio |bias|>5pp months",
     lambda sc: (port[sc+"_BIAS"].abs() > 5).sum()),
    ("Contract MAE pp (last 12mo)",
     lambda sc: ctr[sc+"_MAE_PP"].mean()),
    ("Contract within-10pp accuracy %",
     lambda sc: ctr[sc+"_W10"].mean()),
    ("Q3 forecast rate%",
     lambda sc: fwd_c[sc+"_FCST"].sum() / total_atr * 100),
    ("Q3 forecast $ vs current ($M)",
     lambda sc: (fwd_c[sc+"_FCST"].sum() - fwd_c["A_Current_FCST"].sum()) / 1e6),
]
for label, fn in metrics:
    vals = {sc: fn(sc) for sc in scenarios}
    # best = smallest abs for bias, largest for accuracy, depends on metric
    if "bias" in label.lower() or "|bias|" in label.lower() or "vs current" in label.lower():
        best = min(vals, key=lambda s: abs(vals[s]))
    else:
        best = max(vals, key=lambda s: vals[s]) if "accuracy" in label.lower() \
               else min(vals, key=lambda s: abs(vals[s]))
    line = f"  {label:<38}  "
    line += "  ".join(
        f"{vals[sc]:>+11.2f}" + ("*" if sc == best else " ")
        for sc in scenarios)
    print(line)

print(f"""
  KEY:  * = best value for that metric
  Portfolio grain = what the board renewal rate headline shows
  Contract grain  = what drives individual watchlist flag accuracy

  ALIGNMENT CHECK: If portfolio bias improves but contract MAE worsens,
  the correction is over-fitting to segment averages at the cost of
  contract-level precision. Ideal scenario improves BOTH.
""")

# Apply SQL
print("=" * 105)
print("APPLY SQL — copy chosen scenario into Snowsight")
print("=" * 105)
eff = date.today()
exp = eff + timedelta(days=90)
for sc_name, sc_key in [("C — HL=3", "C_HL3"), ("D — Flat 6mo", "D_Flat6")]:
    print(f"\n  -- {sc_name}")
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
print("\n  *** No changes made — review scorecard above before applying. ***")
