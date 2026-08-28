"""
Calibration refresh proposal — Python equivalent of SP_V5_PROPOSE_QUARTERLY_CALIBRATION.

Uses V5_SANDBOX_APP_CONTRACT_DETAIL directly (V5_APP_BACKTEST doesn't exist).

What this does:
  1. Computes per-segment recency-weighted bias (12-month halflife) from matured months
  2. Proposes new correction offsets on top of current policy
  3. Simulates forward Jul-Sep 2026 under CURRENT vs PROPOSED calibration side-by-side
  4. Shows dynamic auto-refresh mechanism

Safe to run — READ ONLY until you explicitly approve the UPDATE block at the end.
"""
import sys
from pathlib import Path
from datetime import date, timedelta
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection

HALFLIFE_MONTHS = 12   # recency half-life — tunes how fast we follow new regime
MIN_MONTHS      = 6    # minimum matured months per segment before proposing
MIN_ATR_M       = 0.5  # minimum ATR $M per (segment, month) row

conn = get_snowflake_connection(); cur = conn.cursor()
for s in ["USE ROLE STREAMLIT_USER","USE WAREHOUSE REPORTING_WH",
          "USE DATABASE STREAMLIT_APPS","USE SCHEMA DBO"]:
    cur.execute(s)
print("Connected\n")

# ── 1. Load matured monthly actuals vs ML_FORECAST (post-cal) ────────────────
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
hist = pd.DataFrame(cur.fetchall(), columns=["MO","SEGMENT","ATR","ACTUAL","ML_POSTCAL"])
for c in ["ATR","ACTUAL","ML_POSTCAL"]:
    hist[c] = pd.to_numeric(hist[c], errors="coerce").fillna(0)
hist["ACT_RATE"] = hist["ACTUAL"] / hist["ATR"] * 100
hist["ML_RATE"]  = hist["ML_POSTCAL"] / hist["ATR"] * 100
hist["ERROR_PP"] = hist["ML_RATE"] - hist["ACT_RATE"]   # positive = over-predicting renewals
hist["MO_STR"]   = hist["MO"].astype(str).str[:7]
today = pd.Timestamp.today().normalize()

# ── 2. Load current active policy ────────────────────────────────────────────
cur.execute("""
    SELECT SEGMENT, OFFSET_PP, EFFECTIVE_DATE, EXPIRY_DATE
    FROM V5_CALIBRATION_POLICY
    WHERE CURRENT_DATE() BETWEEN EFFECTIVE_DATE AND COALESCE(EXPIRY_DATE, '9999-12-31')
    QUALIFY ROW_NUMBER() OVER (PARTITION BY SEGMENT ORDER BY EFFECTIVE_DATE DESC) = 1
""")
policy_rows = cur.fetchall()
current_policy = {r[0]: r[1] for r in policy_rows}
segs = hist["SEGMENT"].unique().tolist()

# ── 3. Load forward months (Jul-Sep 2026) ────────────────────────────────────
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
    GROUP BY 1, 2
    ORDER BY 1, 2
""")
fwd = pd.DataFrame(cur.fetchall(), columns=["MO","SEGMENT","ATR","ML_POSTCAL"])
for c in ["ATR","ML_POSTCAL"]:
    fwd[c] = pd.to_numeric(fwd[c], errors="coerce").fillna(0)
fwd["ML_RATE"] = fwd["ML_POSTCAL"] / fwd["ATR"] * 100
fwd["MO_STR"]  = fwd["MO"].astype(str).str[:7]
conn.close()

print(f"Loaded {len(hist):,} segment-month rows across "
      f"{hist['MO_STR'].nunique()} months\n")

# ── 4. Compute recency-weighted bias per segment ──────────────────────────────
def recency_weight(mo, today, halflife_months):
    months_ago = (today.year - mo.year) * 12 + (today.month - mo.month)
    return np.exp(-np.log(2) / halflife_months * months_ago)

hist["RECENCY_WT"] = hist["MO"].apply(
    lambda m: recency_weight(pd.Timestamp(m), today, HALFLIFE_MONTHS))
hist["WT_ATR"]         = hist["RECENCY_WT"] * hist["ATR"]
hist["WT_ATR_ERR"]     = hist["RECENCY_WT"] * hist["ATR"] * hist["ERROR_PP"]
hist["FLAT_ATR_ERR"]   = hist["ATR"] * hist["ERROR_PP"]

proposals = []
for seg in segs:
    sub = hist[hist["SEGMENT"] == seg]
    if len(sub) < MIN_MONTHS:
        continue

    flat_bias     = sub["FLAT_ATR_ERR"].sum() / sub["ATR"].sum()
    recency_bias  = sub["WT_ATR_ERR"].sum()   / sub["WT_ATR"].sum()
    recency_adj   = recency_bias - flat_bias
    current_off   = current_policy.get(seg, 0.0)
    proposed_off  = -recency_bias          # flip: if bias +8pp, correct by -8pp
    change        = proposed_off - current_off

    # Drift flag
    if abs(recency_adj) > 1.5:
        drift = "REGIME_SHIFTED"
    elif abs(recency_adj) > 0.75:
        drift = "DRIFTING"
    else:
        drift = "STABLE"

    proposals.append({
        "SEGMENT":          seg,
        "N_MONTHS":         len(sub),
        "FLAT_BIAS_PP":     round(flat_bias, 2),
        "RECENCY_BIAS_PP":  round(recency_bias, 2),
        "RECENCY_ADJ_PP":   round(recency_adj, 2),
        "CURRENT_OFFSET_PP":round(current_off, 2),
        "PROPOSED_OFFSET_PP":round(proposed_off, 2),
        "ADDL_CORRECTION_PP":round(change, 2),
        "DRIFT_FLAG":       drift,
    })

prop_df = pd.DataFrame(proposals).sort_values("SEGMENT")

# ── 5. Print proposals ───────────────────────────────────────────────────────
print("=" * 90)
print("CALIBRATION REFRESH PROPOSALS  (halflife=12mo, recency-weighted)")
print("=" * 90)
print(f"{'Segment':<22}  {'N Months':>8}  {'Flat Bias':>9}  {'Rec Bias':>9}  "
      f"{'Cur Offset':>10}  {'Prop Offset':>11}  {'Addl Corr':>10}  {'Flag'}")
print("-" * 90)
for _, r in prop_df.iterrows():
    print(f"{r['SEGMENT']:<22}  {r['N_MONTHS']:>8}  {r['FLAT_BIAS_PP']:>+9.2f}pp"
          f"  {r['RECENCY_BIAS_PP']:>+9.2f}pp  {r['CURRENT_OFFSET_PP']:>+10.2f}pp"
          f"  {r['PROPOSED_OFFSET_PP']:>+11.2f}pp  {r['ADDL_CORRECTION_PP']:>+10.2f}pp"
          f"  {r['DRIFT_FLAG']}")

# ── 6. Simulate forward months under current vs proposed calibration ──────────
print()
print("=" * 90)
print("FORWARD FORECAST SIMULATION — Jul-Sep 2026  (Current vs Proposed calibration)")
print("=" * 90)
print("  NOTE: Proposed rate = Current ML rate + additional correction per segment")
print()

prop_map = prop_df.set_index("SEGMENT")["ADDL_CORRECTION_PP"].to_dict()

fwd["PROP_CORRECTION_PP"] = fwd["SEGMENT"].map(prop_map).fillna(0)
fwd["PROP_ML_RATE"]       = fwd["ML_RATE"] + fwd["PROP_CORRECTION_PP"]
fwd["CUR_ML_FCST_$"]      = fwd["ML_POSTCAL"]
fwd["PROP_ML_FCST_$"]     = fwd["ATR"] * fwd["PROP_ML_RATE"] / 100
fwd["DIFF_$"]             = fwd["PROP_ML_FCST_$"] - fwd["CUR_ML_FCST_$"]

# By segment × month
print(f"{'Month':<8}  {'Segment':<22}  {'ATR $M':>7}  "
      f"{'Cur Rate%':>9}  {'Prop Rate%':>10}  {'Chg pp':>7}  "
      f"{'Cur $M':>8}  {'Prop $M':>8}  {'Diff $M':>8}")
print("-" * 95)
for _, r in fwd.sort_values(["MO","SEGMENT"]).iterrows():
    print(f"{r['MO_STR']:<8}  {r['SEGMENT']:<22}  ${r['ATR']/1e6:>6.2f}M"
          f"  {r['ML_RATE']:>8.2f}%  {r['PROP_ML_RATE']:>9.2f}%"
          f"  {r['PROP_CORRECTION_PP']:>+6.2f}pp"
          f"  ${r['CUR_ML_FCST_$']/1e6:>6.2f}M"
          f"  ${r['PROP_ML_FCST_$']/1e6:>6.2f}M"
          f"  ${r['DIFF_$']/1e6:>+6.2f}M")

# Portfolio totals by month
print()
print("PORTFOLIO TOTALS:")
print(f"{'Month':<8}  {'ATR $M':>8}  {'Cur Rate%':>9}  {'Prop Rate%':>10}  "
      f"{'Cur $M':>8}  {'Prop $M':>8}  {'Diff $M':>8}")
print("-" * 70)
for mo, g in fwd.groupby("MO_STR"):
    atr   = g["ATR"].sum()
    cur_f = g["CUR_ML_FCST_$"].sum()
    pro_f = g["PROP_ML_FCST_$"].sum()
    print(f"{mo:<8}  ${atr/1e6:>7.2f}M  {cur_f/atr*100:>8.2f}%  {pro_f/atr*100:>9.2f}%"
          f"  ${cur_f/1e6:>7.2f}M  ${pro_f/1e6:>7.2f}M  ${(pro_f-cur_f)/1e6:>+6.2f}M")

total_cur  = fwd["CUR_ML_FCST_$"].sum()
total_prop = fwd["PROP_ML_FCST_$"].sum()
total_atr  = fwd["ATR"].sum()
print(f"\n  Q3-2026 total  Current: ${total_cur/1e6:.2f}M ({total_cur/total_atr*100:.2f}%)")
print(f"  Q3-2026 total Proposed: ${total_prop/1e6:.2f}M ({total_prop/total_atr*100:.2f}%)")
print(f"  Q3 difference: ${(total_prop-total_cur)/1e6:+.2f}M")

# ── 7. Back-test the proposed calibration on the last 12 matured months ──────
print()
print("=" * 90)
print("BACK-TEST: How much would proposed calibration have improved last 12 months?")
print("=" * 90)
cutoff = pd.Timestamp(hist["MO"].max()) - pd.DateOffset(months=11)
last12 = hist[pd.to_datetime(hist["MO"]) >= cutoff].copy()
last12["PROP_CORRECTION_PP"] = last12["SEGMENT"].map(prop_map).fillna(0)
last12["PROP_ML_RATE"]       = last12["ML_RATE"] + last12["PROP_CORRECTION_PP"]
last12["PROP_ML_FCST_$"]     = last12["ATR"] * last12["PROP_ML_RATE"] / 100

monthly = (last12.groupby("MO_STR")
               .agg(ATR=("ATR","sum"),
                    ACTUAL=("ACTUAL","sum"),
                    CUR_F=("ML_POSTCAL","sum"),
                    PROP_F=("PROP_ML_FCST_$","sum"))
               .reset_index()
               .sort_values("MO_STR"))
monthly["ACT_RATE"]  = monthly["ACTUAL"] / monthly["ATR"] * 100
monthly["CUR_RATE"]  = monthly["CUR_F"]  / monthly["ATR"] * 100
monthly["PROP_RATE"] = monthly["PROP_F"] / monthly["ATR"] * 100
monthly["CUR_BIAS"]  = monthly["CUR_RATE"]  - monthly["ACT_RATE"]
monthly["PROP_BIAS"] = monthly["PROP_RATE"] - monthly["ACT_RATE"]
monthly["WINNER"]    = monthly.apply(
    lambda r: "Proposed" if abs(r["PROP_BIAS"]) < abs(r["CUR_BIAS"]) else "Current", axis=1)

print(f"{'Month':<8}  {'Act%':>6}  {'Cur%':>6}  {'Prop%':>6}  "
      f"{'Cur Bias':>9}  {'Prop Bias':>10}  {'Winner'}")
print("-" * 68)
for _, r in monthly.iterrows():
    print(f"{r['MO_STR']:<8}  {r['ACT_RATE']:>6.2f}%  {r['CUR_RATE']:>6.2f}%  {r['PROP_RATE']:>6.2f}%"
          f"  {r['CUR_BIAS']:>+8.2f}pp  {r['PROP_BIAS']:>+9.2f}pp  {r['WINNER']}")
print("-" * 68)
prop_wins = (monthly["WINNER"] == "Proposed").sum()
cur_wins  = (monthly["WINNER"] == "Current").sum()
print(f"  Proposed wins: {prop_wins} / {len(monthly)}   Current wins: {cur_wins} / {len(monthly)}")
print(f"  Mean bias — Current:  {monthly['CUR_BIAS'].mean():>+.2f}pp")
print(f"  Mean bias — Proposed: {monthly['PROP_BIAS'].mean():>+.2f}pp")

# ── 8. Dynamic rotation schedule ─────────────────────────────────────────────
print()
print("=" * 90)
print("DYNAMIC CALIBRATION SCHEDULE (how this stays accurate over time)")
print("=" * 90)
eff_date  = date.today()
exp_date  = eff_date + timedelta(days=90)
print(f"""
  Mechanism:
    - V5_CALIBRATION_POLICY stores per-segment offset + effective/expiry dates
    - Each policy row expires after 90 days (quarterly rotation)
    - SP_V5_SANDBOX_DAILY_BLEND applies active policy offsets when rebuilding
      app tables each morning
    - To refresh: run this script quarterly → review proposals → apply SQL below

  Proposed SQL to apply (run in Snowsight AFTER reviewing above):
  ──────────────────────────────────────────────────────────────────────────""")

print(f"  -- Step 1: expire current rows")
print(f"  UPDATE STREAMLIT_APPS.DBO.V5_CALIBRATION_POLICY")
print(f"    SET EXPIRY_DATE = '{eff_date}'")
print(f"    WHERE EXPIRY_DATE > '{eff_date}' OR EXPIRY_DATE IS NULL;")
print()
print(f"  -- Step 2: insert proposed rows (effective today, expires {exp_date})")
print(f"  INSERT INTO STREAMLIT_APPS.DBO.V5_CALIBRATION_POLICY")
print(f"    (SEGMENT, OFFSET_PP, EFFECTIVE_DATE, EXPIRY_DATE) VALUES")
rows_out = []
for _, r in prop_df.iterrows():
    rows_out.append(
        f"    ('{r['SEGMENT']}', {r['PROPOSED_OFFSET_PP']:.3f}, '{eff_date}', '{exp_date}')")
print(",\n".join(rows_out) + ";")
print()
print(f"  -- Step 3: rebuild app tables")
print(f"  CALL STREAMLIT_APPS.DBO.SP_V5_SANDBOX_DAILY_REFRESH();")
print()
print("  *** REVIEW proposals above before running. No changes made yet. ***")
