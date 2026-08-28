"""
Full pipeline target + ATR definition audit.
Checks every column that feeds the board-facing metrics end-to-end.
"""
import sys, json
sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection()
SEP = "\n" + "=" * 70 + "\n"

# ── 1: Latest model run notes (halflife selected per segment) ──────────────
print(SEP + "1 — Latest run halflife & calibration notes")
q1 = """
WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY RUN_ID ORDER BY 1) AS rn
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_MODEL_RUNS
)
SELECT RUN_ID, FORECAST_RATE_PCT, NOTES
FROM ranked WHERE rn = 1
ORDER BY 1 DESC LIMIT 3
"""
for _, row in fetch_dataframe(q1, conn=conn).iterrows():
    print(f"\n  RUN_ID={row['RUN_ID']}  forecast={row['FORECAST_RATE_PCT']:.2f}%")
    notes = str(row['NOTES'] or '')
    print(f"  NOTES: {notes[:300]}")

# ── 2: ATR column comparison — find Finance-blessed value ─────────────────
print(SEP + "2 — ATR column audit: identify which column = 52,677,115 for Jan 2026")
q2 = """
SELECT
    DATE_TRUNC('MONTH', MASTER_DATE)::DATE                           AS RENEWAL_MONTH,
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE), 0)                             AS ADJ_ATR_C_BUDGET_RATE_SUM,
    ROUND(SUM(COALESCE(ADJ_ATR_C, 0)), 0)                            AS ADJ_ATR_C_SUM,
    ROUND(SUM(INITIAL_ATR_C), 0)                                     AS INITIAL_ATR_C_SUM,
    COUNT(*)                                                          AS N_ROWS,
    COUNT(CASE WHEN INCLUDE_FLAG_C = 1 THEN 1 END)                   AS N_INCLUDE_1,
    COUNT(CASE WHEN INCLUDE_FLAG_C IS NULL THEN 1 END)               AS N_INCLUDE_NULL
FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
WHERE DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-01-01'
GROUP BY 1
"""
print(fetch_dataframe(q2, conn=conn).to_string(index=False))

# ── 2b: Same for CONTRACT_LVL ──────────────────────────────────────────────
print(SEP + "2b — Contract level ATR columns for Jan 2026")
q2b = """
SELECT
    DATE_TRUNC('MONTH', MASTER_DATE)::DATE                           AS RENEWAL_MONTH,
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE), 0)                             AS ADJ_ATR_C_BUDGET_RATE_SUM,
    ROUND(SUM(COALESCE(ADJ_ATR_C, 0)), 0)                            AS ADJ_ATR_C_SUM,
    ROUND(SUM(INITIAL_ATR_C), 0)                                     AS INITIAL_ATR_C_SUM,
    COUNT(*)                                                          AS N_ROWS,
    COUNT(CASE WHEN INCLUDE_FLAG_C = 1 THEN 1 END)                   AS N_INCLUDE_1
FROM ANALYTICS.DBO.CARR__RENEWALS_CONTRACT_LVL
WHERE DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-01-01'
GROUP BY 1
"""
print(fetch_dataframe(q2b, conn=conn).to_string(index=False))

# ── 2c: No INCLUDE_FLAG filter vs flag=1 ──────────────────────────────────
print(SEP + "2c — Portfolio ATR with and without INCLUDE_FLAG_C filter (Jan 2026)")
q2c = """
SELECT
    CASE WHEN INCLUDE_FLAG_C = 1 THEN 'include=1' ELSE 'include<>1 or null' END AS filter,
    COUNT(*)                                AS N,
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE), 0)   AS SUM_ADJ_ATR_BUDGET,
    ROUND(SUM(COALESCE(ADJ_ATR_C, 0)), 0)  AS SUM_ADJ_ATR_C
FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
WHERE DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-01-01'
GROUP BY 1
ORDER BY 1
"""
print(fetch_dataframe(q2c, conn=conn).to_string(index=False))

# ── 3: Finance actuals column audit ───────────────────────────────────────
print(SEP + "3 — Finance actuals: which column = gross_realized_renewal_portfolio")
q3 = """
SELECT
    DATE_TRUNC('MONTH', MASTER_DATE)::DATE                           AS RENEWAL_MONTH,
    ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE, 0)), 0) AS ALLOCATED_CARR_RENEW_GROSS_BUDGET,
    ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_C, 0)), 0)               AS ALLOCATED_CARR_RENEW_C_SUM,
    ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C, 0)), 0)         AS ALLOCATED_CARR_RENEW_GROSS_C_SUM,
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE), 0)                             AS ATR_BUDGET,
    ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE, 0))
          / NULLIF(SUM(ADJ_ATR_C_BUDGET_RATE), 0) * 100, 3)          AS RATE_BUDGET_PCT
FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
WHERE INCLUDE_FLAG_C = 1
  AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE BETWEEN '2025-06-01' AND '2026-05-01'
GROUP BY 1 ORDER BY 1
"""
print(fetch_dataframe(q3, conn=conn).to_string(index=False))

# ── 4: Same for contract level ────────────────────────────────────────────
print(SEP + "4 — Contract actuals columns Jun 2025 - May 2026")
q4 = """
SELECT
    DATE_TRUNC('MONTH', MASTER_DATE)::DATE                           AS RENEWAL_MONTH,
    ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE, 0)), 0) AS GROSS_RENEWAL_BUDGET,
    ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_C, 0)), 0)               AS GROSS_RENEWAL_C,
    ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C, 0)), 0)         AS GROSS_RENEWAL_GROSS_C,
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE), 0)                             AS ATR_BUDGET,
    ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE, 0))
          / NULLIF(SUM(ADJ_ATR_C_BUDGET_RATE), 0) * 100, 3)          AS CONTRACT_RATE_PCT
FROM ANALYTICS.DBO.CARR__RENEWALS_CONTRACT_LVL
WHERE INCLUDE_FLAG_C = 1
  AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE BETWEEN '2025-06-01' AND '2026-05-01'
GROUP BY 1 ORDER BY 1
"""
print(fetch_dataframe(q4, conn=conn).to_string(index=False))

# ── 5: Training target audit in feature store ──────────────────────────────
print(SEP + "5 — Training target columns in ML_SANDBOX_V5_FEATURE_STORE (2026 val months)")
q5 = """
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE  AS RENEWAL_MONTH,
    SPLIT,
    COUNT(*)                                  AS N_ROWS,
    ROUND(AVG(ATR), 0)                        AS AVG_ATR,
    ROUND(SUM(TARGET__RENEWED_AMOUNT) / NULLIF(SUM(ATR), 0) * 100, 2) AS TARGET_RENEWAL_RATE_PCT,
    ROUND(AVG(TARGET__IS_CHURN), 4)           AS AVG_IS_CHURN,
    SUM(CASE WHEN TARGET__IS_CHURN IS NULL THEN 1 ELSE 0 END) AS N_NULL_CHURN
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE
WHERE DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE BETWEEN '2026-01-01' AND '2026-05-01'
  AND HORIZON = 0
GROUP BY 1, 2
ORDER BY 1, 2
"""
print(fetch_dataframe(q5, conn=conn).to_string(index=False))

# ── 6: Training target vs CARR actuals cross-check ─────────────────────────
print(SEP + "6 — FS training targets vs CARR actuals (Jan-May 2026 settled months)")
q6 = """
WITH fs AS (
    SELECT
        DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
        SUM(ATR)                                 AS FS_ATR,
        SUM(TARGET__RENEWED_AMOUNT)              AS FS_RENEWED
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE
    WHERE HORIZON = 0
      AND DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE BETWEEN '2026-01-01' AND '2026-05-01'
    GROUP BY 1
),
carr AS (
    SELECT
        DATE_TRUNC('MONTH', MASTER_DATE)::DATE AS RENEWAL_MONTH,
        SUM(ADJ_ATR_C_BUDGET_RATE)             AS CARR_ATR,
        SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE, 0)) AS CARR_RENEWED
    FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
    WHERE INCLUDE_FLAG_C = 1
      AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE BETWEEN '2026-01-01' AND '2026-05-01'
    GROUP BY 1
)
SELECT
    fs.RENEWAL_MONTH,
    ROUND(fs.FS_ATR/1e6, 2)                         AS FS_ATR_M,
    ROUND(carr.CARR_ATR/1e6, 2)                      AS CARR_ATR_M,
    ROUND(fs.FS_RENEWED / NULLIF(fs.FS_ATR, 0) * 100, 2) AS FS_RENEWAL_RATE_PCT,
    ROUND(carr.CARR_RENEWED / NULLIF(carr.CARR_ATR, 0) * 100, 2) AS CARR_RENEWAL_RATE_PCT,
    ROUND((fs.FS_RENEWED / NULLIF(fs.FS_ATR, 0) - carr.CARR_RENEWED / NULLIF(carr.CARR_ATR, 0)) * 100, 2) AS DIFF_PP
FROM fs
LEFT JOIN carr ON carr.RENEWAL_MONTH = fs.RENEWAL_MONTH
ORDER BY 1
"""
print(fetch_dataframe(q6, conn=conn).to_string(index=False))

# ── 7: Walk-forward accuracy by horizon (board readiness) ─────────────────
print(SEP + "7 — Walk-forward accuracy by horizon (from V5_SANDBOX_APP_BACKTEST)")
q7 = """
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
    SEGMENT,
    ATR,
    ROUND(PREDICTED_RATE_PCT, 2)             AS PRED_RATE_PCT,
    ROUND(ACTUAL_RATE_PCT, 2)                AS ACTUAL_RATE_PCT,
    ROUND(PREDICTED_RATE_PCT - ACTUAL_RATE_PCT, 2) AS ERROR_PP
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_BACKTEST
WHERE RENEWAL_MONTH BETWEEN '2025-01-01' AND '2026-05-01'
ORDER BY RENEWAL_MONTH, SEGMENT
"""
df7 = fetch_dataframe(q7, conn=conn)
if not df7.empty:
    # Monthly aggregate
    import pandas as pd
    df7['RENEWAL_MONTH'] = pd.to_datetime(df7['RENEWAL_MONTH'])
    monthly = df7.groupby('RENEWAL_MONTH').apply(
        lambda g: pd.Series({
            'ATR_M': g['ATR'].sum()/1e6,
            'PRED_RATE': (g['PRED_RATE_PCT'] * g['ATR']).sum() / g['ATR'].sum(),
            'ACTUAL_RATE': (g['ACTUAL_RATE_PCT'] * g['ATR']).sum() / g['ATR'].sum(),
        })
    ).reset_index()
    monthly['ERROR_PP'] = monthly['PRED_RATE'] - monthly['ACTUAL_RATE']
    print(monthly.round(2).to_string(index=False))

# ── 8: Segment-level forward forecast ─────────────────────────────────────
print(SEP + "8 — Segment-level forward forecast (V5_SANDBOX_APP_CONTRACT_DETAIL, Jun-Nov 2026)")
q8 = """
SELECT
    RENEWAL_MONTH,
    SEGMENT,
    COUNT(DISTINCT CONTRACT_ID)                                      AS N_CONTRACTS,
    ROUND(SUM(ATR)/1e6, 2)                                           AS ATR_M,
    ROUND(SUM(RENEWAL_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 2)     AS FORECAST_RATE_PCT,
    ROUND(SUM(ML_FORECAST)      / NULLIF(SUM(ATR), 0) * 100, 2)     AS ML_RATE_PCT,
    COUNT(CASE WHEN RUN_ID = 'V5_ANCHOR_FALLBACK' THEN 1 END)        AS N_FALLBACK
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH BETWEEN '2026-06-01' AND '2026-11-01'
GROUP BY 1, 2
ORDER BY 1, 2
"""
print(fetch_dataframe(q8, conn=conn).to_string(index=False))

# ── 9: Portfolio-level forward forecast ───────────────────────────────────
print(SEP + "9 — Product portfolio-level forward forecast (Jun-Nov 2026)")
q9 = """
SELECT
    RENEWAL_MONTH,
    PRODUCT_PORTFOLIO,
    ROUND(SUM(ATR)/1e6, 2)                                           AS ATR_M,
    ROUND(SUM(RENEWAL_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 2)     AS FORECAST_RATE_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH BETWEEN '2026-06-01' AND '2026-08-01'
GROUP BY 1, 2
ORDER BY 1, 2
"""
print(fetch_dataframe(q9, conn=conn).to_string(index=False))

# ── 10: Contract vs portfolio forecast rate comparison ────────────────────
print(SEP + "10 — Contract grain vs Portfolio grain forecast (should contract > portfolio)")
q10 = """
SELECT
    c.RENEWAL_MONTH,
    ROUND(c.CONTRACT_ATR/1e6, 2)                                     AS CONTRACT_ATR_M,
    ROUND(c.CONTRACT_RATE_PCT, 2)                                     AS CONTRACT_ACTUAL_PCT,
    ROUND(c.CONTRACT_FORECAST_RATE_PCT, 2)                           AS CONTRACT_FORECAST_PCT,
    ROUND(c.CONTRACT_ML_RAW_RATE_PCT, 2)                             AS CONTRACT_ML_RAW_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY c
WHERE c.RENEWAL_MONTH BETWEEN '2025-06-01' AND '2026-11-01'
ORDER BY 1
"""
print(fetch_dataframe(q10, conn=conn).to_string(index=False))

conn.close()
print(SEP + "DONE")
