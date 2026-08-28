"""
audit_v5_forward_forecast.py
============================
Diagnose the flat 69-70% forward forecast showing in the Dev App.

Expected (Iteration 9): Jun 74.5%, Jul 74.0%, Aug 72.1%, Sep 74.1%, Oct 76.4%, Nov 77.8%
Observed (app query):   Jun 69.7%, Jul 69.8%, Aug 69.7%, Sep 69.8%  <-- flat / wrong

This script answers:
  A. What run_id is live in ML_SANDBOX_V5_PREDICTIONS (latest by timestamp)?
  B. What run_id is baked into V5_SANDBOX_APP_CONTRACT_DETAIL?
     → If A != B: app table is STALE — fix = CALL SP_V5_SANDBOX_DAILY_REFRESH();
  C. What are forward rates directly from ML_SANDBOX_V5_PREDICTIONS (FINAL_DOLLARS)?
  D. What are forward rates from V5_SANDBOX_APP_CONTRACT_DETAIL (what app shows)?
  E. What is the segment anchor rate (24-month trailing matured)?
  F. What fraction of forward contracts are V5_ANCHOR_FALLBACK vs scored?
  G. ATR audit: sandbox forward ATR vs prod table ATR vs Finance portfolio direct query.
"""
import sys
sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")

from connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection()

SEP = "\n" + "=" * 70 + "\n"

# ─── A: Latest run in ML_SANDBOX_V5_PREDICTIONS ────────────────────────────
print(SEP + "A — Latest run in ML_SANDBOX_V5_PREDICTIONS")
q_a = """
WITH agg AS (
    SELECT
        RUN_ID,
        MAX(PREDICTION_TS)   AS PREDICTION_TS,
        COUNT(*)             AS N_ROWS,
        COUNT(DISTINCT DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE) AS N_MONTHS,
        MIN(DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE) AS FIRST_MONTH,
        MAX(DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE) AS LAST_MONTH
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
    GROUP BY RUN_ID
)
SELECT * FROM agg ORDER BY PREDICTION_TS DESC LIMIT 5
"""
print(fetch_dataframe(q_a, conn=conn).to_string(index=False))

# ─── B: Run_id baked into the app detail table ────────────────────────────
print(SEP + "B — Run_id baked into V5_SANDBOX_APP_CONTRACT_DETAIL")
q_b = """
SELECT
    RUN_ID,
    COUNT(*)             AS N_ROWS,
    COUNT(DISTINCT RENEWAL_MONTH) AS N_MONTHS,
    MIN(RENEWAL_MONTH)   AS FIRST_MONTH,
    MAX(RENEWAL_MONTH)   AS LAST_MONTH
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
GROUP BY RUN_ID
ORDER BY N_ROWS DESC
"""
print(fetch_dataframe(q_b, conn=conn).to_string(index=False))

# ─── C: Forward rates from ML_SANDBOX_V5_PREDICTIONS directly ─────────────
print(SEP + "C — Forward rates from ML_SANDBOX_V5_PREDICTIONS (latest run, SCORE split)")
q_c = """
WITH latest AS (
    SELECT RUN_ID
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
    GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
),
best_per_contract AS (
    SELECT
        DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
        p.SEGMENT,
        p.E_RENEWAL_RATE,
        p.FINAL_DOLLARS / NULLIF(p.ATR, 0)         AS FINAL_RATE,
        p.PRED_RENEW_RATE_FINAL,
        p.ATR,
        p.FINAL_DOLLARS,
        p.HORIZON,
        p.SPLIT,
        p.BASE_RATE,
        p.W_HORIZON
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS p
    JOIN latest lr ON lr.RUN_ID = p.RUN_ID
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY p.CONTRACT_ID_UFR, DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE
        ORDER BY IFF(p.SPLIT = 'SCORE', 0, 1), p.HORIZON ASC
    ) = 1
)
SELECT
    RENEWAL_MONTH,
    COUNT(*)                                            AS N_CONTRACTS,
    ROUND(SUM(ATR) / 1e6, 2)                           AS ATR_M,
    ROUND(SUM(FINAL_DOLLARS) / NULLIF(SUM(ATR), 0) * 100, 2) AS FINAL_RATE_PCT,
    ROUND(SUM(E_RENEWAL_RATE * ATR) / NULLIF(SUM(ATR), 0) * 100, 2) AS ML_RAW_RATE_PCT,
    ROUND(AVG(BASE_RATE) * 100, 2)                     AS AVG_BASE_RATE_PCT,
    ROUND(AVG(W_HORIZON), 3)                           AS AVG_W_HORIZON,
    ROUND(AVG(HORIZON), 1)                             AS AVG_HORIZON,
    COUNT(CASE WHEN SPLIT = 'SCORE' THEN 1 END)        AS N_SCORE,
    COUNT(CASE WHEN SPLIT != 'SCORE' THEN 1 END)       AS N_HISTORICAL
FROM best_per_contract
WHERE RENEWAL_MONTH BETWEEN '2026-06-01' AND '2026-11-01'
GROUP BY 1
ORDER BY 1
"""
print(fetch_dataframe(q_c, conn=conn).to_string(index=False))

# ─── D: Forward rates from V5_SANDBOX_APP_CONTRACT_DETAIL (app view) ──────
print(SEP + "D — Forward rates from V5_SANDBOX_APP_CONTRACT_DETAIL (what app shows)")
q_d = """
SELECT
    RENEWAL_MONTH,
    COUNT(*)                                            AS N_ROWS,
    COUNT(DISTINCT CONTRACT_ID)                        AS N_CONTRACTS,
    ROUND(SUM(ATR) / 1e6, 2)                           AS ATR_M,
    ROUND(SUM(RENEWAL_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 2)  AS RENEWAL_FORECAST_RATE_PCT,
    ROUND(SUM(ML_FORECAST)      / NULLIF(SUM(ATR), 0) * 100, 2)  AS ML_FORECAST_RATE_PCT,
    COUNT(CASE WHEN RUN_ID = 'V5_ANCHOR_FALLBACK' THEN 1 END)    AS N_FALLBACK,
    COUNT(CASE WHEN RUN_ID != 'V5_ANCHOR_FALLBACK' THEN 1 END)   AS N_SCORED,
    ROUND(
        COUNT(CASE WHEN RUN_ID = 'V5_ANCHOR_FALLBACK' THEN 1 END)
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                              AS FALLBACK_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH BETWEEN '2026-06-01' AND '2026-11-01'
GROUP BY 1
ORDER BY 1
"""
print(fetch_dataframe(q_d, conn=conn).to_string(index=False))

# ─── E: Segment anchor rate ────────────────────────────────────────────────
print(SEP + "E — Segment anchor rates (24-month trailing matured in V5_APP_CONTRACT_DETAIL)")
q_e = """
SELECT
    SEGMENT,
    COUNT(*)                                                                    AS N_ROWS,
    ROUND(SUM(ATR) / 1e6, 2)                                                   AS ATR_M,
    ROUND(SUM(LEAST(COALESCE(ACTUAL_RETAINED_ARR, 0), ATR)) / NULLIF(SUM(ATR), 0) * 100, 2) AS ANCHOR_RATE_PCT
FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
WHERE IS_MATURED_MONTH = TRUE
  AND RENEWAL_MONTH >= DATEADD('MONTH', -24, DATE_TRUNC('MONTH', CURRENT_DATE()))
  AND ATR > 0
GROUP BY SEGMENT
ORDER BY ANCHOR_RATE_PCT
"""
print(fetch_dataframe(q_e, conn=conn).to_string(index=False))

# ─── F: Fallback breakdown by segment for forward months ──────────────────
print(SEP + "F — Forward-month fallback rate by segment (V5_SANDBOX_APP_CONTRACT_DETAIL)")
q_f = """
SELECT
    SEGMENT,
    RENEWAL_MONTH,
    COUNT(*)                                                    AS N_TOTAL,
    COUNT(CASE WHEN RUN_ID = 'V5_ANCHOR_FALLBACK' THEN 1 END)  AS N_FALLBACK,
    ROUND(
        COUNT(CASE WHEN RUN_ID = 'V5_ANCHOR_FALLBACK' THEN 1 END)
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                           AS FALLBACK_PCT,
    ROUND(SUM(CASE WHEN RUN_ID = 'V5_ANCHOR_FALLBACK' THEN ATR ELSE 0 END) / 1e6, 2) AS FALLBACK_ATR_M,
    ROUND(SUM(ATR) / 1e6, 2)                                   AS TOTAL_ATR_M
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH BETWEEN '2026-06-01' AND '2026-11-01'
GROUP BY 1, 2
ORDER BY 1, 2
"""
print(fetch_dataframe(q_f, conn=conn).to_string(index=False))

# ─── G: ATR comparison: sandbox vs prod vs Finance direct ─────────────────
print(SEP + "G — ATR audit: sandbox vs prod vs Finance CARR__RENEWALS_PORTFOLIO_LVL")
q_g = """
WITH sandbox AS (
    SELECT
        RENEWAL_MONTH,
        ROUND(SUM(ATR) / 1e6, 2) AS SANDBOX_ATR_M
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2025-01-01' AND '2026-11-01'
    GROUP BY 1
),
prod_app AS (
    SELECT
        DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
        ROUND(SUM(ATR) / 1e6, 2) AS PROD_ATR_M
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2025-01-01' AND '2026-11-01'
    GROUP BY 1
),
finance_direct AS (
    SELECT
        DATE_TRUNC('MONTH', MASTER_DATE)::DATE AS RENEWAL_MONTH,
        ROUND(SUM(ADJ_ATR_C_BUDGET_RATE) / 1e6, 2) AS FINANCE_ATR_M
    FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
    WHERE INCLUDE_FLAG_C = 1
      AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE BETWEEN '2025-01-01' AND '2026-11-01'
    GROUP BY 1
)
SELECT
    s.RENEWAL_MONTH,
    s.SANDBOX_ATR_M,
    p.PROD_ATR_M,
    f.FINANCE_ATR_M,
    ROUND(s.SANDBOX_ATR_M - f.FINANCE_ATR_M, 2) AS SANDBOX_VS_FINANCE_DIFF_M,
    ROUND(p.PROD_ATR_M    - f.FINANCE_ATR_M, 2) AS PROD_VS_FINANCE_DIFF_M
FROM sandbox s
LEFT JOIN prod_app p ON p.RENEWAL_MONTH = s.RENEWAL_MONTH
LEFT JOIN finance_direct f ON f.RENEWAL_MONTH = s.RENEWAL_MONTH
ORDER BY 1
"""
print(fetch_dataframe(q_g, conn=conn).to_string(index=False))

# ─── H: Contract vs Portfolio gap (validate Finance netting doc) ───────────
print(SEP + "H — Contract vs Portfolio actual rates (2025-01-01 to 2026-05-01)")
q_h = """
WITH contract AS (
    SELECT
        DATE_TRUNC('MONTH', MASTER_DATE)::DATE AS RENEWAL_MONTH,
        ROUND(SUM(ADJ_ATR_C_BUDGET_RATE) / 1e6, 2)                       AS CONTRACT_ATR_M,
        ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE, 0)) / 1e6, 2) AS CONTRACT_RENEWED_M
    FROM ANALYTICS.DBO.CARR__RENEWALS_CONTRACT_LVL
    WHERE INCLUDE_FLAG_C = 1
      AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE BETWEEN '2025-01-01' AND '2026-05-01'
    GROUP BY 1
),
portfolio AS (
    SELECT
        DATE_TRUNC('MONTH', MASTER_DATE)::DATE AS RENEWAL_MONTH,
        ROUND(SUM(ADJ_ATR_C_BUDGET_RATE) / 1e6, 2)                       AS PORT_ATR_M,
        ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE, 0)) / 1e6, 2) AS PORT_RENEWED_M
    FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
    WHERE INCLUDE_FLAG_C = 1
      AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE BETWEEN '2025-01-01' AND '2026-05-01'
    GROUP BY 1
)
SELECT
    c.RENEWAL_MONTH,
    c.CONTRACT_ATR_M,
    p.PORT_ATR_M,
    ROUND(c.CONTRACT_RENEWED_M / NULLIF(c.CONTRACT_ATR_M, 0) * 100, 2)  AS CONTRACT_RATE_PCT,
    ROUND(p.PORT_RENEWED_M     / NULLIF(p.PORT_ATR_M,     0) * 100, 2)  AS PORTFOLIO_RATE_PCT,
    ROUND(
        c.CONTRACT_RENEWED_M / NULLIF(c.CONTRACT_ATR_M, 0) * 100
      - p.PORT_RENEWED_M     / NULLIF(p.PORT_ATR_M,     0) * 100,
        2
    )                                                                     AS GAP_PP
FROM contract c
JOIN portfolio p ON p.RENEWAL_MONTH = c.RENEWAL_MONTH
ORDER BY 1
"""
print(fetch_dataframe(q_h, conn=conn).to_string(index=False))

conn.close()
print(SEP + "DONE — review sections A/B first to confirm stale-table vs model issue")
