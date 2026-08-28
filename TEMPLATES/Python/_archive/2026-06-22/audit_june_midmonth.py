"""
audit_june_midmonth.py  –  confirm mid-month blending discrepancy for June
"""
import sys
sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection()

# ── Per-status breakdown ─────────────────────────────────────────────────────
df = fetch_dataframe("""
SELECT
    CASE
        WHEN COALESCE(OPEN_OPP, 0) = 0
             AND COALESCE(ACTUAL_RETAINED_ARR, 0) > 0  THEN 'CLOSED_WITH_ACTUAL'
        WHEN COALESCE(OPEN_OPP, 0) = 0
             AND COALESCE(ACTUAL_RETAINED_ARR, 0) = 0  THEN 'CLOSED_CHURNED'
        ELSE                                                  'STILL_OPEN'
    END AS STATUS,
    COUNT(*)                                                AS N,
    ROUND(SUM(ATR)/1e6, 3)                                  AS ATR_M,
    ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR,0))/1e6, 3)      AS ACTUAL_M,
    ROUND(SUM(RENEWAL_FORECAST)/1e6, 3)                     AS ML_FORECAST_M,
    ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(ATR),0)*100, 2) AS ACTUAL_RATE_PCT,
    ROUND(SUM(RENEWAL_FORECAST)/NULLIF(SUM(ATR),0)*100, 2)  AS ML_RATE_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH = '2026-06-01'
  AND RUN_ID != 'V5_ANCHOR_FALLBACK'
GROUP BY 1
ORDER BY 1
""", conn=conn)
print("── June contract status breakdown ──────────────────────────────────────")
print(df.to_string(index=False))

# ── Blended mid-month vs model-only ─────────────────────────────────────────
df2 = fetch_dataframe("""
SELECT
    ROUND(SUM(ATR)/1e6, 3)                                         AS TOTAL_ATR_M,
    ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR,0))/1e6, 3)             AS TOTAL_ACTUAL_CLOSED_M,
    ROUND(SUM(RENEWAL_FORECAST)/1e6, 3)                            AS MODEL_FULL_MONTH_M,
    ROUND(SUM(
        CASE WHEN COALESCE(OPEN_OPP,0) = 0
             THEN COALESCE(ACTUAL_RETAINED_ARR, 0)
             ELSE RENEWAL_FORECAST
        END
    )/1e6, 3)                                                      AS BLENDED_FORECAST_M,
    ROUND(SUM(RENEWAL_FORECAST)/NULLIF(SUM(ATR),0)*100, 2)         AS MODEL_RATE_PCT,
    ROUND(SUM(
        CASE WHEN COALESCE(OPEN_OPP,0) = 0
             THEN COALESCE(ACTUAL_RETAINED_ARR, 0)
             ELSE RENEWAL_FORECAST
        END
    )/NULLIF(SUM(ATR),0)*100, 2)                                   AS BLENDED_RATE_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH = '2026-06-01'
  AND RUN_ID != 'V5_ANCHOR_FALLBACK'
""", conn=conn)
print("\n── BLENDED mid-month (actual-on-closed + ML-on-open) ───────────────────")
print(df2.to_string(index=False))

# ── Also check: does V5_SANDBOX_APP_CONTRACT_DETAIL already have ACTUAL set
#    for closed contracts? (i.e. is the data there but app ignores it?) ───────
df3 = fetch_dataframe("""
SELECT
    COUNT_IF(ACTUAL_RETAINED_ARR IS NOT NULL AND ACTUAL_RETAINED_ARR > 0) AS N_HAS_ACTUAL,
    COUNT_IF(ACTUAL_RETAINED_ARR IS NULL OR ACTUAL_RETAINED_ARR = 0)      AS N_NO_ACTUAL,
    ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR,0))/1e6, 3)                    AS SUM_ACTUAL_M,
    ROUND(SUM(COALESCE(OPEN_OPP,0))/1e6, 3)                               AS SUM_OPEN_M,
    COUNT(*)                                                               AS TOTAL_ROWS
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH = '2026-06-01'
  AND RUN_ID != 'V5_ANCHOR_FALLBACK'
""", conn=conn)
print("\n── Data availability check ─────────────────────────────────────────────")
print(df3.to_string(index=False))

conn.close()
