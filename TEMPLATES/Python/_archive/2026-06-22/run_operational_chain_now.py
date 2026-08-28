import sys
from datetime import datetime, timezone
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()

print('start_utc', datetime.now(timezone.utc))

calls = [
    ("daily_refresh", "CALL STREAMLIT_APPS.DBO.SP_V5_SANDBOX_DAILY_REFRESH()"),
    ("snapshot_monthly", "CALL STREAMLIT_APPS.DBO.SP_V5_SNAPSHOT_MONTHLY_FORECAST()"),
    ("snapshot_eom", "CALL STREAMLIT_APPS.DBO.SP_V5_SNAPSHOT_OPEN_RENEWALS()"),
    ("registry", "CALL STREAMLIT_APPS.DBO.SP_REGISTER_MONTHLY_MODEL()"),
]
for name, sql in calls:
    cur.execute(sql)
    row = cur.fetchone()
    print(name, row[0] if row else None)

cur.execute("""
SELECT TRIGGERED_AT, SOURCE, STATUS, MESSAGE
FROM STREAMLIT_APPS.DBO.V5_PIPELINE_RUN_LOG
ORDER BY TRIGGERED_AT DESC
LIMIT 12
""")
print('\nlatest_log_rows:')
for r in cur.fetchall():
    print(r)

cur.execute("SELECT COUNT(*) FROM STREAMLIT_APPS.DBO.RENEWAL_FORECAST_V5_USER_INPUTS")
print('\nmanual_input_rows', cur.fetchone()[0])

print('end_utc', datetime.now(timezone.utc))
