"""
Diagnose June 2026 forecast discrepancy:
  "contract level forecast is smaller than portfolio forecast"

Checks:
  1. Monthly rollup: ML_FORECAST vs FINANCE_FORECAST for June 2026
     — ML should always >= Finance by design (contract rate > portfolio rate)
  2. Contract detail: per-row ML_FORECAST vs RENEWAL_FORECAST for June
     — any rows where RENEWAL_FORECAST > ML_FORECAST?
  3. Contract-level monthly table: CONTRACT_FORECAST_RATE_PCT vs portfolio
  4. Compares Open Renewals (FORWARD_OPEN) population vs All-June population
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import os
os.environ["PYTHONIOENCODING"] = "utf-8"

from connection import get_snowflake_connection
import pandas as pd

conn = get_snowflake_connection()

print("=" * 70)
print("JUNE 2026 FORECAST DIAGNOSTIC")
print("=" * 70)

# ── 1. Monthly rollup for June ─────────────────────────────────────────────
print("\n--- 1. MONTHLY ROLLUP (V5_SANDBOX_APP_MONTHLY_ROLLUP) June 2026 ---")
q = """
SELECT
    MONTH,
    CONTRACTS,
    ATR,
    ML_FORECAST,
    FINANCE_FORECAST,
    EFFECTIVE_FORECAST_ML_ONLY,
    EFFECTIVE_FORECAST_FINANCE,
    ACTUAL,
    OPEN_OPP,
    IS_FULLY_MATURED,
    ROUND(ML_FORECAST / NULLIF(ATR,0) * 100, 2)              AS ML_RATE_PCT,
    ROUND(FINANCE_FORECAST / NULLIF(ATR,0) * 100, 2)         AS FINANCE_RATE_PCT,
    ROUND(ML_FORECAST - FINANCE_FORECAST, 0)                 AS ML_MINUS_FINANCE,
    ROUND(ACTUAL / NULLIF(ATR,0) * 100, 2)                   AS ACTUAL_RATE_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_MONTHLY_ROLLUP
WHERE MONTH >= '2026-05-01' AND MONTH <= '2026-07-01'
ORDER BY MONTH
"""
df = pd.read_sql(q, conn)
print(df.to_string(index=False))

# ── 2. Contract detail — any rows where RENEWAL_FORECAST > ML_FORECAST for June? ──
print("\n--- 2. CONTRACT DETAIL ANOMALY CHECK: RENEWAL_FORECAST > ML_FORECAST ---")
q2 = """
SELECT
    COUNT(*) AS TOTAL_JUNE_ROWS,
    SUM(CASE WHEN RENEWAL_FORECAST > ML_FORECAST + 0.01 THEN 1 ELSE 0 END) AS ROWS_WHERE_RENEWAL_GT_ML,
    ROUND(SUM(ML_FORECAST), 0)                 AS SUM_ML_FORECAST,
    ROUND(SUM(FINANCE_FORECAST), 0)            AS SUM_FINANCE_FORECAST,
    ROUND(SUM(RENEWAL_FORECAST), 0)            AS SUM_RENEWAL_FORECAST,
    ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ATR),0) * 100, 2)   AS ML_RATE_CONTRACT,
    ROUND(SUM(FINANCE_FORECAST) / NULLIF(SUM(ATR),0) * 100, 2) AS FINANCE_RATE_PORTFOLIO,
    ROUND(SUM(ACTUAL_RETAINED_ARR), 0)         AS SUM_ACTUAL,
    ROUND(SUM(OPEN_OPP), 0)                    AS SUM_OPEN_OPP,
    SUM(CASE WHEN IS_MATURE THEN 1 ELSE 0 END) AS MATURE_CONTRACTS,
    SUM(CASE WHEN NOT IS_MATURE THEN 1 ELSE 0 END) AS OPEN_CONTRACTS
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH = '2026-06-01'
  AND COALESCE(ATR, 0) > 0
"""
df2 = pd.read_sql(q2, conn)
print(df2.to_string(index=False))

# ── 3. Open Renewals (FORWARD_OPEN) vs All June contracts ─────────────────
print("\n--- 3. FORWARD_OPEN vs HISTORICAL_MATURED for June 2026 ---")
q3 = """
SELECT
    COHORT,
    COUNT(*) AS ROWS,
    ROUND(SUM(ATR), 0) AS ATR,
    ROUND(SUM(ML_FORECAST), 0) AS ML_FORECAST,
    ROUND(SUM(FINANCE_FORECAST), 0) AS FINANCE_FORECAST,
    ROUND(SUM(ACTUAL_RETAINED_ARR), 0) AS ACTUAL,
    ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ATR),0) * 100, 2) AS ML_RATE
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH = '2026-06-01'
  AND COALESCE(ATR, 0) > 0
GROUP BY COHORT
ORDER BY COHORT
"""
df3 = pd.read_sql(q3, conn)
print(df3.to_string(index=False))

# ── 4. Contract-level monthly table for June ───────────────────────────────
print("\n--- 4. CONTRACT_LVL_MONTHLY: contract vs portfolio rates for May/Jun ---")
q4 = """
SELECT
    RENEWAL_MONTH,
    N_CONTRACTS,
    ROUND(CONTRACT_ATR, 0) AS CONTRACT_ATR,
    ROUND(CONTRACT_RENEWED, 0) AS CONTRACT_RENEWED,
    CONTRACT_RATE_PCT,
    CONTRACT_FORECAST_RATE_PCT,
    CONTRACT_ML_RAW_RATE_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
WHERE RENEWAL_MONTH >= '2026-05-01' AND RENEWAL_MONTH <= '2026-07-01'
ORDER BY RENEWAL_MONTH
"""
df4 = pd.read_sql(q4, conn)
print(df4.to_string(index=False))

# ── 5. Compare contract vs portfolio grain for June by segment ────────────
print("\n--- 5. JUNE 2026 BY SEGMENT: ML_FORECAST vs FINANCE_FORECAST ---")
q5 = """
SELECT
    SEGMENT,
    COUNT(*) AS ROWS,
    ROUND(SUM(ATR), 0) AS ATR,
    ROUND(SUM(ML_FORECAST), 0) AS ML_FORECAST,
    ROUND(SUM(FINANCE_FORECAST), 0) AS FINANCE_FORECAST,
    ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ATR),0) * 100, 2) AS ML_RATE,
    ROUND(SUM(FINANCE_FORECAST) / NULLIF(SUM(ATR),0) * 100, 2) AS FINANCE_RATE,
    ROUND((SUM(ML_FORECAST) - SUM(FINANCE_FORECAST)) / NULLIF(SUM(ATR),0) * 100, 2) AS ML_MINUS_FINANCE_PP,
    ROUND(SUM(ACTUAL_RETAINED_ARR), 0) AS ACTUAL,
    SUM(CASE WHEN IS_MATURE THEN 1 ELSE 0 END) AS MATURE
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH = '2026-06-01'
  AND COALESCE(ATR, 0) > 0
GROUP BY SEGMENT
ORDER BY SEGMENT
"""
df5 = pd.read_sql(q5, conn)
print(df5.to_string(index=False))

# ── 6. Reconciliation table for June — direct ML vs Finance comparison ─────
print("\n--- 6. RECONCILIATION TABLE: ML vs Finance rates for June 2026 ---")
q6 = """
SELECT
    COUNT(*) AS ROWS,
    ROUND(SUM(PORTFOLIO_ATR), 0) AS PORTFOLIO_ATR,
    ROUND(SUM(CONTRACT_ATR), 0) AS CONTRACT_ATR,
    ROUND(SUM(ML_FORECAST_DOLLARS), 0) AS ML_FORECAST_DOLLARS,
    ROUND(SUM(FINANCE_FORECAST_DOLLARS), 0) AS FINANCE_FORECAST_DOLLARS,
    ROUND(SUM(ML_FORECAST_DOLLARS) / NULLIF(SUM(PORTFOLIO_ATR),0) * 100, 2) AS ML_RATE_PCT,
    ROUND(SUM(FINANCE_FORECAST_DOLLARS) / NULLIF(SUM(PORTFOLIO_ATR),0) * 100, 2) AS FINANCE_RATE_PCT,
    ROUND(SUM(ML_FORECAST_DOLLARS - FINANCE_FORECAST_DOLLARS), 0) AS ML_MINUS_FINANCE_DOLLARS
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_RECONCILIATION
WHERE RENEWAL_MONTH = '2026-06-01'
"""
df6 = pd.read_sql(q6, conn)
print(df6.to_string(index=False))

print("\n" + "=" * 70)
print("DIAGNOSIS COMPLETE")
print("=" * 70)
conn.close()
