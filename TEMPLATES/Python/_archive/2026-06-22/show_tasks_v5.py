import sys
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()
cur.execute("SHOW TASKS IN SCHEMA STREAMLIT_APPS.DBO")
cols = [d[0] for d in cur.description]
rows = cur.fetchall()
print("columns:")
print(cols)
print("\nrows:")
for r in rows:
    rec = dict(zip(cols, r))
    print(f"{rec.get('name')} | state={rec.get('state')} | schedule={rec.get('schedule')} | owner={rec.get('owner')} | comment={rec.get('comment')}")
