"""
Check actual calibration-adjusted bias in the app table (ML_FORECAST = post-calibration).
Compares the numbers the app actually shows vs actuals.
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection(); cur = conn.cursor()
for s in ["USE ROLE STREAMLIT_USER","USE WAREHOUSE REPORTING_WH",
          "USE DATABASE STREAMLIT_APPS","USE SCHEMA DBO"]:
    cur.execute(s)

# ── Post-calibration bias by segment for matured 2026 months ─────────────────
cur.execute("""
    SELECT
        DATE_TRUNC('month', RENEWAL_MONTH) AS MO,
        SEGMENT,
        SUM(ATR)                              AS ATR,
        SUM(COALESCE(ACTUAL_RETAINED_ARR, 0)) AS ACTUAL,
        SUM(COALESCE(ML_FORECAST, 0))         AS ML_ADJ
    FROM V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURE = TRUE AND IS_MATURED_MONTH = TRUE
      AND COALESCE(ATR, 0) > 1000
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
      AND RENEWAL_MONTH >= '2026-01-01'
    GROUP BY 1, 2
    ORDER BY 1, 2
""")
df = pd.DataFrame(cur.fetchall(), columns=["MO","SEG","ATR","ACTUAL","ML_ADJ"])
for c in ["ATR","ACTUAL","ML_ADJ"]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
df["ACT_RATE"] = df["ACTUAL"] / df["ATR"] * 100
df["ML_RATE"]  = df["ML_ADJ"] / df["ATR"] * 100
df["BIAS"]     = df["ML_RATE"] - df["ACT_RATE"]
df["MO_STR"]   = df["MO"].astype(str).str[:7]

print("=== POST-CALIBRATION BIAS BY SEGMENT (2026 YTD matured months) ===")
print(f"{'Month':<8}  {'Segment':<22}  {'ATR $M':>7}  {'Act%':>7}  {'ML%':>7}  {'Bias pp':>8}")
print("-" * 68)
for _, r in df.iterrows():
    flag = " <<" if abs(r["BIAS"]) > 5 else ""
    print(f"{r['MO_STR']:<8}  {r['SEG']:<22}  ${r['ATR']/1e6:>6.2f}M"
          f"  {r['ACT_RATE']:>6.2f}%  {r['ML_RATE']:>6.2f}%  {r['BIAS']:>+7.2f}pp{flag}")

# Portfolio rollup
print()
print("=== PORTFOLIO TOTAL (calibration-adjusted ML_FORECAST vs Actual) ===")
port = (df.groupby("MO_STR")
          .agg(ATR=("ATR","sum"), ACTUAL=("ACTUAL","sum"), ML_ADJ=("ML_ADJ","sum"))
          .reset_index())
port["ACT_RATE"] = port["ACTUAL"] / port["ATR"] * 100
port["ML_RATE"]  = port["ML_ADJ"] / port["ATR"] * 100
port["BIAS"]     = port["ML_RATE"] - port["ACT_RATE"]
print(f"{'Month':<8}  {'Act%':>7}  {'ML%':>7}  {'Bias pp':>8}  {'ATR $M':>8}")
print("-" * 48)
for _, r in port.iterrows():
    flag = " <<" if abs(r["BIAS"]) > 5 else ""
    print(f"{r['MO_STR']:<8}  {r['ACT_RATE']:>6.2f}%  {r['ML_RATE']:>6.2f}%"
          f"  {r['BIAS']:>+7.2f}pp{flag}  ${r['ATR']/1e6:>7.2f}M")

mean_bias = port["BIAS"].mean()
print(f"\n  Mean bias (calibration-adjusted): {mean_bias:>+.2f}pp")
print(f"  Active offsets (May 28 policy): Core=-4.5pp, Growth=-3.75pp, Strategic=-5.5pp, Emerging=0, SCO=0")

# ── Also pull the full historical post-cal bias (last 12 months) ──────────────
print()
print("=== LAST 12 MATURED MONTHS — portfolio bias after calibration ===")
cur.execute("""
    SELECT
        DATE_TRUNC('month', RENEWAL_MONTH) AS MO,
        SUM(ATR)                              AS ATR,
        SUM(COALESCE(ACTUAL_RETAINED_ARR, 0)) AS ACTUAL,
        SUM(COALESCE(ML_FORECAST, 0))         AS ML_ADJ
    FROM V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURE = TRUE AND IS_MATURED_MONTH = TRUE
      AND COALESCE(ATR, 0) > 1000
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    GROUP BY 1
    ORDER BY 1 DESC
    LIMIT 12
""")
h = pd.DataFrame(cur.fetchall(), columns=["MO","ATR","ACTUAL","ML_ADJ"])
for c in ["ATR","ACTUAL","ML_ADJ"]:
    h[c] = pd.to_numeric(h[c], errors="coerce").fillna(0)
h["ACT_RATE"] = h["ACTUAL"] / h["ATR"] * 100
h["ML_RATE"]  = h["ML_ADJ"] / h["ATR"] * 100
h["BIAS"]     = h["ML_RATE"] - h["ACT_RATE"]
h["MO_STR"]   = h["MO"].astype(str).str[:7]
h = h.sort_values("MO")
print(f"{'Month':<8}  {'Act%':>7}  {'ML%':>7}  {'Bias pp':>8}")
print("-" * 38)
for _, r in h.iterrows():
    flag = " <<" if abs(r["BIAS"]) > 5 else ""
    print(f"{r['MO_STR']:<8}  {r['ACT_RATE']:>6.2f}%  {r['ML_RATE']:>6.2f}%  {r['BIAS']:>+7.2f}pp{flag}")
print(f"\n  Mean last-12 post-cal bias: {h['BIAS'].mean():>+.2f}pp")

conn.close()
