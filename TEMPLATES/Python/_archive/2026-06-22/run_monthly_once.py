import sys
from datetime import datetime, timezone
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()
print('start_utc', datetime.now(timezone.utc))
cur.execute("CALL STREAMLIT_APPS.DBO.SP_V5_SANDBOX_RUN_PIPELINE()")
row = cur.fetchone()
print('result', row[0] if row else None)
print('end_utc', datetime.now(timezone.utc))
