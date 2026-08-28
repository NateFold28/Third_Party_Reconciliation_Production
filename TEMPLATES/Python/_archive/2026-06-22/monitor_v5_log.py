import sys
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()
cur.execute("""
SELECT TRIGGERED_AT, SOURCE, STATUS, MESSAGE
FROM STREAMLIT_APPS.DBO.V5_PIPELINE_RUN_LOG
WHERE TRIGGERED_AT >= DATEADD(hour, -6, CURRENT_TIMESTAMP())
ORDER BY TRIGGERED_AT DESC
LIMIT 20
""")
for r in cur.fetchall():
    print(f"{r[0]} | {r[1]} | {r[2]} | {r[3]}")
