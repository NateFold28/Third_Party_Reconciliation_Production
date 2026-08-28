"""
Deep-dive checks:
1. April 2026 bias discrepancy (prod shows -3.86pp vs audit -1.3pp)
2. CHURN_PROBABILITY = 90%? Check actual column meaning
3. Contract count diff prod vs sandbox for forward months
"""
import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection()

print("=" * 60)
print("1. April bias — all matured rows vs IS_MATURED_MONTH=TRUE")
print("=" * 60)
df1 = fetch_dataframe("""
    SELECT
      'IS_MATURED_MONTH=TRUE'                                           AS filter,
      ROUND(SUM(FINANCE_FORECAST)      / NULLIF(SUM(ATR),0)*100, 2)   AS ff_pct,
      ROUND(SUM(FINANCE_RENEWED_GROSS) / NULLIF(SUM(ATR),0)*100, 2)   AS actual_pct,
      ROUND((SUM(FINANCE_FORECAST)-SUM(FINANCE_RENEWED_GROSS))
             / NULLIF(SUM(ATR),0)*100, 2)                              AS bias_pp,
      COUNT(*) AS n,
      ROUND(SUM(ATR)/1e6, 2)                                           AS atr_m
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-04-01'
      AND IS_MATURED_MONTH = TRUE
    UNION ALL
    SELECT
      'ALL rows for Apr',
      ROUND(SUM(FINANCE_FORECAST)      / NULLIF(SUM(ATR),0)*100, 2),
      ROUND(SUM(FINANCE_RENEWED_GROSS) / NULLIF(SUM(ATR),0)*100, 2),
      ROUND((SUM(FINANCE_FORECAST)-SUM(FINANCE_RENEWED_GROSS))
             / NULLIF(SUM(ATR),0)*100, 2),
      COUNT(*),
      ROUND(SUM(ATR)/1e6, 2)
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-04-01'
    UNION ALL
    SELECT
      'Jan-May avg',
      ROUND(SUM(FINANCE_FORECAST)      / NULLIF(SUM(ATR),0)*100, 2),
      ROUND(SUM(FINANCE_RENEWED_GROSS) / NULLIF(SUM(ATR),0)*100, 2),
      ROUND((SUM(FINANCE_FORECAST)-SUM(FINANCE_RENEWED_GROSS))
             / NULLIF(SUM(ATR),0)*100, 2),
      COUNT(*),
      ROUND(SUM(ATR)/1e6, 2)
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-01-01' AND '2026-05-01'
      AND IS_MATURED_MONTH = TRUE
""", conn=conn)
print(df1.to_string(index=False))
print()

print("=" * 60)
print("2. CHURN_PROBABILITY column — sample values + stats")
print("=" * 60)
df2 = fetch_dataframe("""
    SELECT
      ROUND(MIN(CHURN_PROBABILITY),   4) AS churn_min,
      ROUND(MAX(CHURN_PROBABILITY),   4) AS churn_max,
      ROUND(AVG(CHURN_PROBABILITY),   4) AS churn_avg,
      ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY CHURN_PROBABILITY), 4) AS churn_p50,
      ROUND(MIN(CHURN_PCT),           2) AS churn_pct_min,
      ROUND(MAX(CHURN_PCT),           2) AS churn_pct_max,
      ROUND(AVG(CHURN_PCT),           2) AS churn_pct_avg
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-12-01'
""", conn=conn)
print(df2.to_string(index=False))
print()

print("=" * 60)
print("3. Forward contract count discrepancy: prod vs sandbox")
print("=" * 60)
df3 = fetch_dataframe("""
    SELECT mo, prod_n, sandbox_n, prod_atr_m, sandbox_atr_m FROM (
      SELECT
        DATE_TRUNC('month', RENEWAL_MONTH)::DATE AS mo,
        COUNT(*) AS prod_n,
        ROUND(SUM(ATR)/1e6, 2) AS prod_atr_m
      FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
      WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-12-01'
      GROUP BY 1
    ) p
    JOIN (
      SELECT
        DATE_TRUNC('month', RENEWAL_MONTH)::DATE AS mo,
        COUNT(*) AS sandbox_n,
        ROUND(SUM(ATR)/1e6, 2) AS sandbox_atr_m
      FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
      WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-12-01'
      GROUP BY 1
    ) s USING (mo)
    ORDER BY mo
""", conn=conn)
print(df3.to_string(index=False))
print()

print("=" * 60)
print("4. FINANCE_FORECAST = EFFECTIVE_FORECAST_FINANCE for forward months?")
print("   (confirms app display col is correct)")
print("=" * 60)
df4 = fetch_dataframe("""
    SELECT
      SUM(CASE WHEN ABS(FINANCE_FORECAST - EFFECTIVE_FORECAST_FINANCE) < 0.01
               THEN 1 ELSE 0 END)              AS matching_rows,
      SUM(CASE WHEN ABS(FINANCE_FORECAST - EFFECTIVE_FORECAST_FINANCE) >= 0.01
               THEN 1 ELSE 0 END)              AS diverging_rows,
      COUNT(*)                                 AS total_rows,
      ROUND(AVG(EFFECTIVE_FORECAST_FINANCE / NULLIF(ATR,0)*100), 2) AS avg_eff_finance_pct,
      ROUND(AVG(FINANCE_FORECAST           / NULLIF(ATR,0)*100), 2) AS avg_ff_pct
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-12-01'
      AND ATR > 0
""", conn=conn)
print(df4.to_string(index=False))
