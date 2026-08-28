"""Debug backtest zero actuals + ATR Finance source."""
import sys
sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection()
SEP = "\n" + "=" * 70 + "\n"

# ── A: Are there ANY IS_MATURED_MONTH=TRUE rows in sandbox detail? ─────────
print(SEP + "A — IS_MATURED_MONTH=TRUE count by month in V5_SANDBOX_APP_CONTRACT_DETAIL")
qA = """
SELECT
    RENEWAL_MONTH,
    COUNT(*)                                                AS N_TOTAL,
    COUNT(CASE WHEN IS_MATURED_MONTH = TRUE THEN 1 END)    AS N_MATURED,
    ROUND(SUM(CASE WHEN IS_MATURED_MONTH = TRUE THEN ATR ELSE 0 END)/1e6,2) AS MATURED_ATR_M,
    ROUND(SUM(CASE WHEN IS_MATURED_MONTH = TRUE THEN ACTUAL_RETAINED_ARR ELSE 0 END)/1e6,2) AS MATURED_ACTUAL_M
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH BETWEEN '2026-01-01' AND '2026-06-01'
GROUP BY 1 ORDER BY 1
"""
print(fetch_dataframe(qA, conn=conn).to_string(index=False))

# ── B: Do the CAL/VALIDATION RENEWAL_MONTHs in predictions match detail? ──
print(SEP + "B — RENEWAL_MONTH format in ML_SANDBOX_V5_PREDICTIONS (CAL/VAL H=0)")
qB = """
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE  AS MONTH_TRUNC,
    RENEWAL_MONTH                              AS RAW_RENEWAL_MONTH,
    SPLIT,
    COUNT(*)                                   AS N_ROWS
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
WHERE SPLIT IN ('CAL', 'VALIDATION') AND HORIZON = 0
GROUP BY 1, 2, 3
ORDER BY 2 DESC LIMIT 15
"""
print(fetch_dataframe(qB, conn=conn).to_string(index=False))

# ── C: Direct join test — does any bt_pred row match full_actuals? ────────
print(SEP + "C — Direct join test (sample 5 contracts from Jan 2026 VAL)")
qC = """
WITH sample AS (
    SELECT CONTRACT_ID_UFR, DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RENEWAL_MONTH, SEGMENT
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
    WHERE SPLIT = 'VALIDATION' AND HORIZON = 0
      AND DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE = '2026-01-01'
    LIMIT 10
)
SELECT s.CONTRACT_ID_UFR, s.RENEWAL_MONTH, s.SEGMENT,
       d.CONTRACT_ID AS DETAIL_ID, d.RENEWAL_MONTH AS DETAIL_MONTH,
       d.SEGMENT AS DETAIL_SEG, d.IS_MATURED_MONTH, d.ACTUAL_RETAINED_ARR
FROM sample s
LEFT JOIN STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
  ON d.CONTRACT_ID = s.CONTRACT_ID_UFR
 AND d.RENEWAL_MONTH = s.RENEWAL_MONTH
 AND d.SEGMENT = s.SEGMENT
"""
print(fetch_dataframe(qC, conn=conn).to_string(index=False))

# ── D: Check if the JOIN works without the SEGMENT condition ─────────────
print(SEP + "D — Join test WITHOUT segment condition (Jan 2026, 5 contracts)")
qD = """
WITH sample AS (
    SELECT CONTRACT_ID_UFR, DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RENEWAL_MONTH, SEGMENT
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
    WHERE SPLIT = 'VALIDATION' AND HORIZON = 0
      AND DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE = '2026-01-01'
    LIMIT 10
)
SELECT s.CONTRACT_ID_UFR, s.SEGMENT AS PRED_SEG,
       d.SEGMENT AS DETAIL_SEG, d.IS_MATURED_MONTH, d.ACTUAL_RETAINED_ARR,
       IFF(s.SEGMENT = d.SEGMENT, 'MATCH', 'MISMATCH') AS SEG_MATCH
FROM sample s
LEFT JOIN STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
  ON d.CONTRACT_ID = s.CONTRACT_ID_UFR
 AND d.RENEWAL_MONTH = s.RENEWAL_MONTH
"""
print(fetch_dataframe(qD, conn=conn).to_string(index=False))

# ── E: ATR - check if Finance is using ADJ_ATR_C (not budget rate) ────────
print(SEP + "E — Finance ATR question: what gives 52,677,115 for Jan 2026?")
qE = """
SELECT
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE), 0)                    AS ADJ_ATR_C_BUDGET_RATE,
    ROUND(SUM(ADJ_ATR_C), 0)                                 AS ADJ_ATR_C,
    ROUND(SUM(INITIAL_ATR_C), 0)                             AS INITIAL_ATR_C,
    -- combinations
    ROUND((SUM(ADJ_ATR_C) + SUM(ADJ_ATR_C_BUDGET_RATE)) / 2, 0) AS AVG_OF_BOTH,
    COUNT(*) AS N_ROWS
FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
WHERE DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-01-01'
  AND INCLUDE_FLAG_C = 1
"""
print(fetch_dataframe(qE, conn=conn).to_string(index=False))

# ── F: Check CARR SNAPSHOTS table for Jan 2026 ────────────────────────────
print(SEP + "F — SNAPSHOTS.CARR__RENEWALS_PORTFOLIO_LVL_SNAP ATR for Jan 2026")
qF = """
SELECT
    SNAPSHOT_DATE,
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE), 0)   AS ADJ_ATR_C_BUDGET_RATE,
    COUNT(*) AS N_ROWS
FROM ANALYTICS.SNAPSHOTS.CARR__RENEWALS_PORTFOLIO_LVL_SNAP
WHERE DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-01-01'
  AND INCLUDE_FLAG_C = 1
GROUP BY SNAPSHOT_DATE
ORDER BY SNAPSHOT_DATE DESC LIMIT 5
"""
try:
    print(fetch_dataframe(qF, conn=conn).to_string(index=False))
except Exception as ex:
    print(f"  No snap table or error: {ex}")

# ── G: Recent portfolio actuals to calibrate "reasonable" forecast ─────────
print(SEP + "G — Recent CARR portfolio rates (last 12 months) — what is 'normal'?")
qG = """
SELECT
    DATE_TRUNC('MONTH', MASTER_DATE)::DATE AS RENEWAL_MONTH,
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE)/1e6, 2) AS ATR_M,
    ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE,0))/1e6, 2) AS RENEWED_M,
    ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE,0))
          / NULLIF(SUM(ADJ_ATR_C_BUDGET_RATE),0) * 100, 2) AS PORTFOLIO_RATE_PCT
FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
WHERE INCLUDE_FLAG_C = 1
  AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE BETWEEN '2025-06-01' AND '2026-05-01'
GROUP BY 1 ORDER BY 1
"""
print(fetch_dataframe(qG, conn=conn).to_string(index=False))

conn.close()
print(SEP + "DONE")
