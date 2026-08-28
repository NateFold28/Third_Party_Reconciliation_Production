import sys
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()

# Tables
cur.execute("""
    SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, BYTES/1024/1024 AS MB
    FROM STREAMLIT_APPS.INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = 'DBO'
      AND TABLE_TYPE = 'BASE TABLE'
    ORDER BY TABLE_NAME
""")
rows = cur.fetchall()
print(f'=== TABLES ({len(rows)}) ===')
for r in rows:
    mb = r[3] if r[3] else 0
    rc = r[2] if r[2] else 0
    print(f'  {r[0]:<65} rows={rc:>8,}  {mb:>6.1f}MB')

# Views
cur.execute("""
    SELECT TABLE_NAME FROM STREAMLIT_APPS.INFORMATION_SCHEMA.VIEWS
    WHERE TABLE_SCHEMA = 'DBO'
    ORDER BY TABLE_NAME
""")
views = cur.fetchall()
print(f'\n=== VIEWS ({len(views)}) ===')
for r in views:
    print(f'  {r[0]}')

# Procs
cur.execute('SHOW PROCEDURES IN SCHEMA STREAMLIT_APPS.DBO')
procs = cur.fetchall()
print(f'\n=== PROCEDURES ({len(procs)}) ===')
for r in procs:
    print(f'  {r[1]}')

# Tasks
cur.execute('SHOW TASKS IN SCHEMA STREAMLIT_APPS.DBO')
tasks = cur.fetchall()
print(f'\n=== TASKS ({len(tasks)}) ===')
for r in tasks:
    print(f'  {r[1]:<60} state={r[9]:<12}  sched={r[4]}')
