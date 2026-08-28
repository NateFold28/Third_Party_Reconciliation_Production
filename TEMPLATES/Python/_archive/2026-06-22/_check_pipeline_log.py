"""
_check_pipeline_log.py
Check today's pipeline log entries and diagnose stale data timestamp.
Also run sandbox daily refresh if needed.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection()

print("=== Recent pipeline log (last 30 entries) ===")
df = fetch_dataframe("""
    SELECT SOURCE, STATUS, MESSAGE,
           CONVERT_TIMEZONE('America/New_York', EVENT_TIMESTAMP) AS TS_ET
    FROM STREAMLIT_APPS.DBO.V5_PIPELINE_RUN_LOG
    ORDER BY EVENT_TIMESTAMP DESC
    LIMIT 30
""", conn=conn)
print(df.to_string(index=False))

print()
print("=== What 'Data last refreshed' shows (load_last_data_refresh_ts query) ===")
df2 = fetch_dataframe("""
    SELECT
        CONVERT_TIMEZONE('America/New_York', MAX(EVENT_TIMESTAMP)) AS LAST_REFRESH_ET,
        MAX(MESSAGE) AS LAST_MSG
    FROM STREAMLIT_APPS.DBO.V5_PIPELINE_RUN_LOG
    WHERE SOURCE IN ('v5-sandbox-daily', 'v5-sandbox-daily-blend',
                     'v5-daily-base-refresh', 'v5-sandbox-monthly')
      AND STATUS = 'OK'
""", conn=conn)
print(df2.to_string(index=False))

print()
print("=== V5_APP_FORECAST_SNAPSHOTS — today's snapshot ===")
df3 = fetch_dataframe("""
    SELECT SNAPSHOT_DATE, COUNT(*) AS MONTHS,
           SUM(CASE WHEN CONTRACT_ACTUAL_PCT IS NOT NULL THEN 1 ELSE 0 END) AS WITH_CONTRACT_PCT
    FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS
    WHERE SNAPSHOT_DATE >= CURRENT_DATE() - 2
    GROUP BY SNAPSHOT_DATE ORDER BY SNAPSHOT_DATE
""", conn=conn)
print(df3.to_string(index=False))

print()
print("=== V5_APP_CONTRACT_SNAPSHOTS — today's snapshot ===")
df4 = fetch_dataframe("""
    SELECT SNAPSHOT_DATE, COUNT(*) AS CONTRACTS
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_SNAPSHOTS
    WHERE SNAPSHOT_DATE >= CURRENT_DATE() - 2
    GROUP BY SNAPSHOT_DATE ORDER BY SNAPSHOT_DATE
""", conn=conn)
print(df4.to_string(index=False))
