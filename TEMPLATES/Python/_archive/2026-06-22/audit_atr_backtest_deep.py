"""Deep ATR + backtest investigation."""
import sys
sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection()
SEP = "\n" + "=" * 70 + "\n"

# ── A: List ALL columns in PORTFOLIO_LVL to find Finance's adj_atr_portfolio ─
print(SEP + "A — All ATR-related columns in CARR__RENEWALS_PORTFOLIO_LVL")
qA = """
SELECT COLUMN_NAME, DATA_TYPE
FROM ANALYTICS.INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'DBO'
  AND TABLE_NAME = 'CARR__RENEWALS_PORTFOLIO_LVL'
  AND (LOWER(COLUMN_NAME) LIKE '%atr%' OR LOWER(COLUMN_NAME) LIKE '%adj%'
       OR LOWER(COLUMN_NAME) LIKE '%initial%' OR LOWER(COLUMN_NAME) LIKE '%arr%')
ORDER BY ORDINAL_POSITION
"""
print(fetch_dataframe(qA, conn=conn).to_string(index=False))

# ── B: Sum EVERY ATR-like column for Jan 2026 to find 52,677,115 ───────────
print(SEP + "B — All ATR column sums for Jan 2026 (INCLUDE_FLAG_C=1)")
qB = """
SELECT
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE), 0)          AS ADJ_ATR_C_BUDGET_RATE,
    ROUND(SUM(COALESCE(ADJ_ATR_C, 0)), 0)         AS ADJ_ATR_C,
    ROUND(SUM(INITIAL_ATR_C), 0)                  AS INITIAL_ATR_C,
    ROUND(SUM(COALESCE(ADJ_ATR_PORTFOLIO, 0)), 0) AS ADJ_ATR_PORTFOLIO_IF_EXISTS
FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
WHERE INCLUDE_FLAG_C = 1
  AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-01-01'
"""
try:
    print(fetch_dataframe(qB, conn=conn).to_string(index=False))
except Exception as e:
    print(f"  Column not found: {e}")

# ── C: What does the CARR_RENEWALS_PORTFOLIO_LVL dbt source look like? ──────
print(SEP + "C — Check if there is a view/dbt version with adj_atr_portfolio alias")
qC = """
SELECT COLUMN_NAME, TABLE_NAME
FROM ANALYTICS.INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'DBO'
  AND LOWER(COLUMN_NAME) LIKE '%adj_atr_portfolio%'
ORDER BY TABLE_NAME, ORDINAL_POSITION
"""
print(fetch_dataframe(qC, conn=conn).to_string(index=False))

# ── D: Backtest table — check what ACTUAL values look like ────────────────
print(SEP + "D — V5_SANDBOX_APP_BACKTEST raw sample")
qD = """
SELECT *
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_BACKTEST
WHERE RENEWAL_MONTH BETWEEN '2026-01-01' AND '2026-05-01'
ORDER BY RENEWAL_MONTH, SEGMENT
LIMIT 30
"""
print(fetch_dataframe(qD, conn=conn).to_string(index=False))

# ── E: Backtest SQL — check if ACTUAL_RETAINED is being populated ──────────
print(SEP + "E — Backtest table column list")
qE = """
SELECT COLUMN_NAME, DATA_TYPE
FROM STREAMLIT_APPS.INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'DBO'
  AND TABLE_NAME = 'V5_SANDBOX_APP_BACKTEST'
ORDER BY ORDINAL_POSITION
"""
print(fetch_dataframe(qE, conn=conn).to_string(index=False))

# ── F: Check V5_APP_BACKTEST (production) for comparison ──────────────────
print(SEP + "F — Production V5_APP_BACKTEST (Jan-May 2026) for comparison")
qF = """
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
    SEGMENT,
    ATR,
    ROUND(PREDICTED_RATE_PCT, 2)             AS PRED_RATE_PCT,
    ROUND(ACTUAL_RATE_PCT, 2)                AS ACTUAL_RATE_PCT,
    ROUND(PREDICTED_RATE_PCT - ACTUAL_RATE_PCT, 2) AS ERROR_PP
FROM STREAMLIT_APPS.DBO.V5_APP_BACKTEST
WHERE RENEWAL_MONTH BETWEEN '2026-01-01' AND '2026-05-01'
ORDER BY RENEWAL_MONTH, SEGMENT
"""
print(fetch_dataframe(qF, conn=conn).to_string(index=False))

# ── G: Check feature store label vs CARR portfolio actuals ──────────────────
print(SEP + "G — Feature store TARGET__RENEWED_AMOUNT vs CARR PORTFOLIO actuals by segment (Jan-May 2026)")
qG = """
WITH fs AS (
    SELECT
        DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
        F_SEGMENT                                AS SEGMENT,
        SUM(ATR)                                 AS FS_ATR,
        SUM(TARGET__RENEWED_AMOUNT)              AS FS_RENEWED,
        COUNT(*)                                 AS N_ROWS
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE
    WHERE HORIZON = 0
      AND DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE BETWEEN '2026-01-01' AND '2026-05-01'
    GROUP BY 1, 2
)
SELECT
    RENEWAL_MONTH,
    SEGMENT,
    ROUND(FS_ATR/1e6, 2)         AS FS_ATR_M,
    ROUND(FS_RENEWED / NULLIF(FS_ATR, 0) * 100, 2) AS FS_RATE_PCT,
    N_ROWS
FROM fs
ORDER BY 1, 2
"""
print(fetch_dataframe(qG, conn=conn).to_string(index=False))

# ── H: Check the dbt model columns for CARR tables ───────────────────────
print(SEP + "H — CARR dbt model column search for adj_atr variants")
qH = """
SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
FROM ANALYTICS.INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'DBO'
  AND TABLE_NAME IN ('CARR__RENEWALS_PORTFOLIO_LVL', 'CARR__RENEWALS_CONTRACT_LVL')
  AND (LOWER(COLUMN_NAME) LIKE '%atr%')
ORDER BY TABLE_NAME, ORDINAL_POSITION
"""
print(fetch_dataframe(qH, conn=conn).to_string(index=False))

conn.close()
print(SEP + "DONE")
