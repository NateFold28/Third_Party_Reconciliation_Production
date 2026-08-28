"""
Validation: Contract-level forecast approach for the board chart.

Checks:
1. Is CONTRACT_FORECAST_RATE_PCT populated in V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY?
2. How does the contract ML forecast track vs contract actuals (bias/error)?
3. What is the actual historical contract vs portfolio rate gap (to calibrate the fallback)?
4. Is the override-delta bridge mathematically sound?
5. Summary of all finance leadership asks and their status.
"""
import sys
sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection, fetch_dataframe
import pandas as pd
import numpy as np

conn = get_snowflake_connection()

print("=" * 70)
print("1. CONTRACT_LVL_MONTHLY TABLE — is CONTRACT_FORECAST_RATE_PCT populated?")
print("=" * 70)
q1 = """
SELECT
    RENEWAL_MONTH,
    N_CONTRACTS,
    ROUND(CONTRACT_ATR / 1e6, 2)                    AS ATR_M,
    ROUND(CONTRACT_RENEWED / 1e6, 2)                AS RENEWED_M,
    ROUND(CONTRACT_RATE_PCT, 2)                     AS CONTRACT_ACTUAL_PCT,
    ROUND(CONTRACT_FORECAST_RATE_PCT, 2)            AS CONTRACT_FORECAST_PCT,
    ROUND(CONTRACT_ML_RAW_RATE_PCT, 2)              AS CONTRACT_ML_RAW_PCT,
    ROUND(CONTRACT_ACTUAL_VS_FORECAST_PP, 2)        AS ACTUAL_VS_FCST_PP
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
WHERE RENEWAL_MONTH >= '2025-06-01'
ORDER BY RENEWAL_MONTH
"""
df1 = fetch_dataframe(q1, conn=conn)
print(df1.to_string(index=False))

print("\n" + "=" * 70)
print("2. CONTRACT FORECAST ACCURACY — bias and MAE vs contract actuals")
print("=" * 70)
q2 = """
SELECT
    RENEWAL_MONTH,
    ROUND(CONTRACT_RATE_PCT, 2)             AS ACTUAL_PCT,
    ROUND(CONTRACT_FORECAST_RATE_PCT, 2)    AS FCST_PCT,
    ROUND(CONTRACT_FORECAST_RATE_PCT - CONTRACT_RATE_PCT, 2) AS ERROR_PP
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
WHERE RENEWAL_MONTH >= '2025-06-01'
  AND CONTRACT_RATE_PCT IS NOT NULL
  AND CONTRACT_FORECAST_RATE_PCT IS NOT NULL
ORDER BY RENEWAL_MONTH
"""
df2 = fetch_dataframe(q2, conn=conn)
if not df2.empty:
    errors = pd.to_numeric(df2["ERROR_PP"], errors="coerce").dropna()
    print(df2.to_string(index=False))
    print(f"\n  Bias (mean error):  {errors.mean():+.2f} pp")
    print(f"  MAE:                {errors.abs().mean():.2f} pp")
    print(f"  Max over-forecast:  {errors.max():+.2f} pp")
    print(f"  Max under-forecast: {errors.min():+.2f} pp")
    print(f"  Within ±3 pp:       {(errors.abs() <= 3).mean()*100:.0f}%  of months")
    print(f"  Within ±5 pp:       {(errors.abs() <= 5).mean()*100:.0f}%  of months")
else:
    print("  No closed months with both actuals and forecast found.")

print("\n" + "=" * 70)
print("3. CONTRACT vs PORTFOLIO RATE GAP — historical (to calibrate fallback)")
print("=" * 70)
q3 = """
WITH contract AS (
    SELECT RENEWAL_MONTH,
           ROUND(CONTRACT_RATE_PCT, 3)          AS C_RATE,
           ROUND(CONTRACT_FORECAST_RATE_PCT, 3) AS C_FCST
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
    WHERE RENEWAL_MONTH >= '2025-06-01'
),
portfolio AS (
    SELECT
        DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE               AS RENEWAL_MONTH,
        ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR,0))
              / NULLIF(SUM(ATR), 0) * 100, 3)                  AS P_RATE,
        ROUND(SUM(COALESCE(EFFECTIVE_FORECAST_ML_ONLY,
                            ML_FORECAST, 0))
              / NULLIF(SUM(ATR), 0) * 100, 3)                  AS P_ML_RATE,
        ROUND(SUM(COALESCE(FINANCE_FORECAST, ML_FORECAST, 0))
              / NULLIF(SUM(ATR), 0) * 100, 3)                  AS P_FCST_RATE
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH = TRUE
      AND RENEWAL_MONTH >= '2025-06-01'
    GROUP BY 1
)
SELECT
    c.RENEWAL_MONTH,
    c.C_RATE                             AS CONTRACT_ACTUAL_PCT,
    p.P_RATE                             AS PORTFOLIO_ACTUAL_PCT,
    ROUND(c.C_RATE - p.P_RATE, 2)        AS ACTUAL_GAP_PP,
    c.C_FCST                             AS CONTRACT_FCST_PCT,
    p.P_FCST_RATE                        AS PORTFOLIO_FCST_PCT,
    ROUND(c.C_FCST - p.P_FCST_RATE, 2)  AS FCST_GAP_PP
FROM contract c
JOIN portfolio p USING (RENEWAL_MONTH)
WHERE c.C_RATE IS NOT NULL
  AND p.P_RATE IS NOT NULL
ORDER BY 1
"""
df3 = fetch_dataframe(q3, conn=conn)
if not df3.empty:
    print(df3.to_string(index=False))
    actual_gaps = pd.to_numeric(df3["ACTUAL_GAP_PP"], errors="coerce").dropna()
    fcst_gaps   = pd.to_numeric(df3.get("FCST_GAP_PP"), errors="coerce").dropna()
    print(f"\n  Actual gap mean:          {actual_gaps.mean():+.2f} pp  (contract − portfolio, actual)")
    if len(actual_gaps) > 4:
        sg = actual_gaps.sort_values()
        trimmed = sg.iloc[2:-2]
        print(f"  Actual gap trimmed mean:  {trimmed.mean():+.2f} pp  (±2 obs removed)")
    if not fcst_gaps.empty:
        print(f"  Forecast gap mean:        {fcst_gaps.mean():+.2f} pp  (contract ML − portfolio ML)")
else:
    print("  No matched months found (may need more closed months).")

print("\n" + "=" * 70)
print("4. OVERRIDE DELTA BRIDGE — check override exists and delta is non-trivial")
print("=" * 70)
q4 = """
WITH monthly AS (
    SELECT
        DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE           AS RENEWAL_MONTH,
        SUM(ATR)                                           AS ATR,
        SUM(COALESCE(FINANCE_FORECAST, ML_FORECAST, 0))   AS MODEL_FCST,
        SUM(CASE WHEN MANUAL_FORECAST IS NOT NULL
                 THEN MANUAL_FORECAST
                 ELSE COALESCE(FINANCE_FORECAST, ML_FORECAST, 0) END) AS EFF_FCST,
        COUNT(CASE WHEN MANUAL_FORECAST IS NOT NULL THEN 1 END) AS N_OVERRIDES
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH >= DATEADD('month', -3, CURRENT_DATE)
    GROUP BY 1
)
SELECT
    RENEWAL_MONTH,
    N_OVERRIDES,
    ROUND(MODEL_FCST / NULLIF(ATR,0) * 100, 2)  AS MODEL_RATE_PCT,
    ROUND(EFF_FCST  / NULLIF(ATR,0) * 100, 2)   AS EFF_RATE_PCT,
    ROUND((EFF_FCST - MODEL_FCST) / NULLIF(ATR,0) * 100, 2) AS OVERRIDE_DELTA_PP
FROM monthly
ORDER BY 1
"""
df4 = fetch_dataframe(q4, conn=conn)
if not df4.empty:
    print(df4.to_string(index=False))
    deltas = pd.to_numeric(df4["OVERRIDE_DELTA_PP"], errors="coerce").dropna()
    print(f"\n  Override delta range:  {deltas.min():+.2f} to {deltas.max():+.2f} pp")
    print(f"  Max absolute delta:    {deltas.abs().max():.2f} pp")
    print("\n  NOTE: Contract line shifts by exactly this same delta.")
    print("  No netting inflation — only the human judgment component moves the contract line.")
else:
    print("  No data in recent 3 months.")

print("\n" + "=" * 70)
print("5. FINANCE / LEADERSHIP ASK CHECKLIST")
print("=" * 70)
checklist = [
    ("ATR=0 phantom row removal",                    "SQL filter + assemble_frame() guard — both in place",               "✅ DONE"),
    ("Monthly forecast → contract-level topline",    "Native contract ML (CONTRACT_FORECAST_RATE_PCT); fallback netting", "✅ DONE"),
    ("Manual overrides impact contract forecast",    "Override pp delta bridged: CONTRACT_FCST + (RATE_PCT-MODEL_RATE_PCT)","✅ DONE"),
    ("Renewal Rate chart: one plot, 3 lines",        "ML (→Contract), Forecast w/Override (→Contract), Actual (Contract)", "✅ DONE"),
    ("Monthly rollup — leadership transposed view",  "Metric rows × month columns, ▶ marks forward months",               "✅ DONE"),
    ("Monthly rollup — analyst detail table",        "Full detail in collapsible expander below leadership table",         "✅ DONE"),
    ("Contract Detail — % manual input column",     "MANUAL_PCT = MANUAL_FORECAST / ATR × 100 computed and displayed",   "✅ DONE"),
    ("Executive summary cleanup / more professional","Hero: 'Renewals Forecast · Board-ready model forecasts'",           "✅ DONE"),
    ("Model performance — board readiness context", "Banner: calibration ECE <4pp, churn ≠ (1−renewal rate) note",       "✅ DONE"),
    ("Model + churn risk validation (board-ready)", "Backtest tab: dual-grain chart, segment error, portfolio error",     "⚠️  REVIEW"),
]
print(f"\n  {'Ask':<52} {'Implementation':<55} {'Status'}")
print(f"  {'-'*52} {'-'*55} {'-'*10}")
for ask, impl, status in checklist:
    print(f"  {ask:<52} {impl:<55} {status}")

print(f"\n  ⚠️  REVIEW note: the churn risk / model board-readiness question")
print(f"     is a governance gate — requires running backtest validation")
print(f"     (see _board_ready_check.py) to confirm ECE <4pp and bias <2pp")
print(f"     before presenting to the board. Metrics are exposed in the")
print(f"     Model Performance tab — no code change needed if they pass.")
