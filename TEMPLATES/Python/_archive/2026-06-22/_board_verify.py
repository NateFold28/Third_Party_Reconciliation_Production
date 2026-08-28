"""Board readiness verification — runs against Snowflake production tables."""
import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection()

# A. Column semantics — prod vs sandbox forward months
print("=" * 60)
print("A. PROD ML_FORECAST vs FINANCE_FORECAST vs RETENTION_PCT")
print("   (Jul–Dec 2026 forward months)")
print("=" * 60)
df_a = fetch_dataframe("""
    SELECT
      ROUND(SUM(ML_FORECAST)      / NULLIF(SUM(ATR),0)*100, 2) AS ml_atr_pct,
      ROUND(SUM(FINANCE_FORECAST) / NULLIF(SUM(ATR),0)*100, 2) AS ff_atr_pct,
      ROUND(AVG(RETENTION_PCT), 2)                              AS avg_retention_pct,
      ROUND(AVG(CHURN_PROBABILITY*100), 2)                      AS avg_churn_prob_pct,
      COUNT(*) AS n_contracts
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-12-01'
      AND ATR > 0
""", conn=conn)
print(df_a.to_string(index=False))
print()

print("=" * 60)
print("B. PROD per-month: ML_FORECAST/ATR vs FINANCE_FORECAST/ATR")
print("=" * 60)
df_b = fetch_dataframe("""
    SELECT
      DATE_TRUNC('month', RENEWAL_MONTH)::DATE                  AS mo,
      ROUND(SUM(ML_FORECAST)      / NULLIF(SUM(ATR),0)*100, 1)  AS ml_atr_pct,
      ROUND(SUM(FINANCE_FORECAST) / NULLIF(SUM(ATR),0)*100, 1)  AS ff_atr_pct,
      ROUND(AVG(RETENTION_PCT), 1)                               AS avg_ret_pct,
      COUNT(*) AS n
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-12-01'
      AND ATR > 0
    GROUP BY 1 ORDER BY 1
""", conn=conn)
print(df_b.to_string(index=False))
print()

print("=" * 60)
print("C. SANDBOX same months (comparison)")
print("=" * 60)
df_c = fetch_dataframe("""
    SELECT
      DATE_TRUNC('month', RENEWAL_MONTH)::DATE                  AS mo,
      ROUND(SUM(ML_FORECAST)      / NULLIF(SUM(ATR),0)*100, 1)  AS ml_atr_pct,
      ROUND(SUM(FINANCE_FORECAST) / NULLIF(SUM(ATR),0)*100, 1)  AS ff_atr_pct,
      COUNT(*) AS n
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-12-01'
      AND ATR > 0
    GROUP BY 1 ORDER BY 1
""", conn=conn)
print(df_c.to_string(index=False))
print()

print("=" * 60)
print("D. What does the PROD app actually use? Check EFFECTIVE_FORECAST cols")
print("   (forward months, ATR-weighted)")
print("=" * 60)
df_d = fetch_dataframe("""
    SELECT
      DATE_TRUNC('month', RENEWAL_MONTH)::DATE                              AS mo,
      ROUND(SUM(EFFECTIVE_FORECAST_FINANCE)  / NULLIF(SUM(ATR),0)*100, 1)  AS eff_finance_pct,
      ROUND(SUM(EFFECTIVE_FORECAST_ML_ONLY)  / NULLIF(SUM(ATR),0)*100, 1)  AS eff_ml_pct,
      ROUND(SUM(FINANCE_FORECAST)            / NULLIF(SUM(ATR),0)*100, 1)  AS ff_pct,
      COUNT(*) AS n
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-12-01'
      AND ATR > 0
    GROUP BY 1 ORDER BY 1
""", conn=conn)
print(df_d.to_string(index=False))
print()

print("=" * 60)
print("E. Historical accuracy check — matured months Jan–May 2026")
print("   FINANCE_FORECAST vs ACTUAL (FINANCE_RENEWED_GROSS)")
print("=" * 60)
df_e = fetch_dataframe("""
    SELECT
      DATE_TRUNC('month', RENEWAL_MONTH)::DATE                                     AS mo,
      ROUND(SUM(FINANCE_FORECAST)     / NULLIF(SUM(ATR),0)*100, 2)                AS ff_pct,
      ROUND(SUM(FINANCE_RENEWED_GROSS)/ NULLIF(SUM(ATR),0)*100, 2)                AS actual_pct,
      ROUND((SUM(FINANCE_FORECAST) - SUM(FINANCE_RENEWED_GROSS))
             / NULLIF(SUM(ATR),0)*100, 2)                                          AS bias_pp
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-01-01' AND '2026-05-01'
      AND IS_MATURED_MONTH = TRUE
    GROUP BY 1 ORDER BY 1
""", conn=conn)
print(df_e.to_string(index=False))
print()

print("=" * 60)
print("F. Snapshot table coverage (board historical data)")
print("=" * 60)
df_f = fetch_dataframe("""
    SELECT
      COUNT(*) AS snapshot_rows,
      COUNT(DISTINCT SNAPSHOT_DATE) AS snapshot_dates,
      MIN(SNAPSHOT_DATE) AS earliest,
      MAX(SNAPSHOT_DATE) AS latest
    FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS
""", conn=conn)
print(df_f.to_string(index=False))
print()

print("=" * 60)
print("G. User overrides — count + last updated")
print("=" * 60)
df_g = fetch_dataframe("""
    SELECT
      COUNT(*)                    AS total_rows,
      COUNT(DISTINCT CONTRACT_ID) AS distinct_contracts,
      MAX(UPDATED_AT)             AS last_updated
    FROM STREAMLIT_APPS.DBO.RENEWAL_FORECAST_V5_USER_INPUTS
""", conn=conn)
print(df_g.to_string(index=False))
