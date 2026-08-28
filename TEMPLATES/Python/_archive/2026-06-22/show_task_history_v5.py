import sys
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()
cur.execute("SHOW TASK HISTORY LIKE 'V5_SANDBOX_MONTHLY_MODEL_TASK' IN SCHEMA STREAMLIT_APPS.DBO")
cols = [d[0] for d in cur.description]
rows = cur.fetchall()
print(cols)
for r in rows[:10]:
    rec = dict(zip(cols, r))
    print(rec)
