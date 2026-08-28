"""
_check_snapshot_health.py
Verify snapshot table health, accuracy chart data, and EOM open renewals.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection()

print("=== V5_APP_FORECAST_SNAPSHOTS — matured months (ACTUAL_PCT populated) ===")
df = fetch_dataframe("""
    SELECT RENEWAL_MONTH, SNAPSHOT_DATE, ACTUAL_PCT, MODEL_RATE_PCT,
           MANUAL_ADJUSTED_PCT, CONTRACT_ACTUAL_PCT, NETTING_PP
    FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS
    WHERE ACTUAL_PCT IS NOT NULL
      AND SNAPSHOT_DATE = CURRENT_DATE()
    ORDER BY RENEWAL_MONTH
""", conn=conn)
print(f"  {len(df)} matured months in today's snapshot")
print(df.to_string(index=False))

print()
print("=== V5_APP_CONTRACT_SNAPSHOTS — growing daily snapshot (unique dates) ===")
df2 = fetch_dataframe("""
    SELECT SNAPSHOT_DATE,
           COUNT(DISTINCT CONTRACT_ID) AS CONTRACTS,
           COUNT(DISTINCT RENEWAL_MONTH) AS MONTHS
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_SNAPSHOTS
    GROUP BY SNAPSHOT_DATE
    ORDER BY SNAPSHOT_DATE DESC LIMIT 10
""", conn=conn)
print(df2.to_string(index=False))

print()
print("=== V5_APP_OPEN_RENEWALS_SNAPSHOTS — EOM contract-level captures ===")
df3 = fetch_dataframe("""
    SELECT SNAPSHOT_MONTH, CAPTURED_ON,
           COUNT(*) AS CONTRACTS,
           ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR, 0)) / 1e6, 2) AS ACTUAL_M
    FROM STREAMLIT_APPS.DBO.V5_APP_OPEN_RENEWALS_SNAPSHOTS
    GROUP BY SNAPSHOT_MONTH, CAPTURED_ON
    ORDER BY SNAPSHOT_MONTH DESC LIMIT 15
""", conn=conn)
print(df3.to_string(index=False))

print()
print("=== Columns in V5_APP_FORECAST_SNAPSHOT_LATEST view ===")
df4 = fetch_dataframe("""
    SELECT * FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOT_LATEST LIMIT 0
""", conn=conn)
print(df4.columns.tolist())
