"""Check live netting values and snapshot NETTING_PP per month."""
import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection()

print("=" * 60)
print("1. Snapshot NETTING_PP per month (is it dynamic or static?)")
print("=" * 60)
df1 = fetch_dataframe("""
    SELECT
      RENEWAL_MONTH::DATE AS mo,
      ROUND(NETTING_PP, 3)           AS netting_pp,
      ROUND(ACTUAL_PCT, 2)           AS portfolio_actual_pct,
      ROUND(CONTRACT_ACTUAL_PCT, 2)  AS contract_actual_pct,
      ROUND(CONTRACT_ACTUAL_PCT - ACTUAL_PCT, 3) AS computed_gap
    FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOT_LATEST
    WHERE NETTING_PP IS NOT NULL
    ORDER BY RENEWAL_MONTH
""", conn=conn)
print(df1.to_string(index=False))
print()

print("=" * 60)
print("2. Live blended netting (trimmed mean of >=4 matured months)")
print("   (This is what the app shows as +X.Xpp Netting Uplift)")
print("=" * 60)
df2 = fetch_dataframe("""
    WITH gaps AS (
      SELECT
        RENEWAL_MONTH,
        CONTRACT_ACTUAL_PCT - ACTUAL_PCT AS gap,
        ROW_NUMBER() OVER (ORDER BY gap) AS rn,
        COUNT(*) OVER () AS total
      FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOT_LATEST
      WHERE NETTING_PP IS NOT NULL
        AND CONTRACT_ACTUAL_PCT IS NOT NULL
        AND ACTUAL_PCT IS NOT NULL
    )
    SELECT
      COUNT(*) AS n_months,
      ROUND(MIN(gap), 3)    AS min_gap,
      ROUND(MAX(gap), 3)    AS max_gap,
      ROUND(AVG(gap), 3)    AS raw_mean,
      ROUND(AVG(CASE WHEN rn > 2 AND rn <= total - 2 THEN gap END), 3) AS trimmed_mean
    FROM gaps
""", conn=conn)
print(df2.to_string(index=False))
print()

print("=" * 60)
print("3. Columns available in V5_APP_FORECAST_SNAPSHOT_LATEST")
print("=" * 60)
df3 = fetch_dataframe("SELECT * FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOT_LATEST LIMIT 1", conn=conn)
print("Columns:", sorted(df3.columns.tolist()))
print()
print(df3.to_string(index=False))
