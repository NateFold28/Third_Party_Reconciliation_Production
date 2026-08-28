import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection
conn = get_snowflake_connection()
cur = conn.cursor()

# Find all stages in STREAMLIT_APPS.DBO
cur.execute("SHOW STAGES IN SCHEMA STREAMLIT_APPS.DBO")
cols = [d[0] for d in cur.description]
print("All stages in STREAMLIT_APPS.DBO:")
for row in cur.fetchall():
    d = dict(zip(cols, row))
    print(f"  name={d.get('name')} url={d.get('url','')}")

cur.close()
conn.close()
