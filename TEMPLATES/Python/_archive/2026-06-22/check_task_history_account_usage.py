import sys
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()
cur.execute("""
SELECT SCHEDULED_TIME, QUERY_START_TIME, COMPLETED_TIME, STATE, QUERY_ID, ERROR_CODE, ERROR_MESSAGE, NAME
FROM SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY
WHERE DATABASE_NAME = 'STREAMLIT_APPS'
  AND SCHEMA_NAME = 'DBO'
  AND NAME = 'V5_SANDBOX_MONTHLY_MODEL_TASK'
  AND SCHEDULED_TIME >= DATEADD('day', -2, CURRENT_TIMESTAMP())
ORDER BY SCHEDULED_TIME DESC
LIMIT 20
""")
for r in cur.fetchall():
    print(r)
