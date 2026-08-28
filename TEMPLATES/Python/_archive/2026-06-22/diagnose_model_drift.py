"""
Diagnose whether the model's forecast bias is growing over time (drift)
or stable noise. Shows actual vs EV renewal rate by month and year.
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

cur.execute("""
    SELECT DATE_TRUNC('month', RENEWAL_MONTH) AS MO,
           SUM(ATR)                            AS ATR,
           SUM(COALESCE(ACTUAL_RETAINED_ARR,0)) AS ACTUAL,
           SUM(COALESCE(ML_FORECAST,0))          AS EV
    FROM V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURE = TRUE AND IS_MATURED_MONTH = TRUE
      AND COALESCE(ATR, 0) > 1000
      AND CHURN_PCT IS NOT NULL
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    GROUP BY 1 ORDER BY 1
""")
df = pd.DataFrame(cur.fetchall(), columns=["MO","ATR","ACTUAL","EV"])
conn.close()

for c in ["ATR","ACTUAL","EV"]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

df["ACT_RATE"] = df["ACTUAL"] / df["ATR"] * 100
df["EV_RATE"]  = df["EV"]     / df["ATR"] * 100
df["BIAS_PP"]  = df["EV_RATE"] - df["ACT_RATE"]
df["MO_STR"]   = df["MO"].astype(str).str[:7]
df["YR"]       = df["MO_STR"].str[:4]

active = df[df["ATR"] > 5e6]

print("Month      Act Rate%  EV Rate%   Bias pp")
print("-" * 48)
for _, r in active.tail(24).iterrows():
    flag = " <<< LARGE" if abs(r["BIAS_PP"]) > 8 else ""
    print(f"{r['MO_STR']}   {r['ACT_RATE']:>7.2f}%   {r['EV_RATE']:>7.2f}%   {r['BIAS_PP']:>+6.2f}pp{flag}")

print()
print("Year   Avg actual rate%   Avg EV rate%   Avg bias pp")
print("-" * 55)
for yr, g in active.groupby("YR"):
    print(f"{yr}   {g['ACT_RATE'].mean():>10.2f}%       {g['EV_RATE'].mean():>8.2f}%    {g['BIAS_PP'].mean():>+7.2f}pp")

# Rolling 6-month average to show trend direction
print()
print("Rolling 6-month avg bias (shows drift direction):")
print("-" * 45)
active = active.reset_index(drop=True)
for i in range(5, len(active)):
    window = active.iloc[i-5:i+1]
    mo = active.iloc[i]["MO_STR"]
    print(f"  6mo ending {mo}:  avg bias {window['BIAS_PP'].mean():>+6.2f}pp   actual rate {window['ACT_RATE'].mean():.2f}%")
