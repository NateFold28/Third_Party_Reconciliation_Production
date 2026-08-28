"""Check ML_MODEL_REGISTRY and ML_SANDBOX_V5_MODEL_RUNS schemas for Section 7 MERGE."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur  = conn.cursor()

for tbl in ("ML_MODEL_REGISTRY", "ML_SANDBOX_V5_MODEL_RUNS"):
    cur.execute(f"""
        SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
        FROM STREAMLIT_APPS.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'DBO' AND TABLE_NAME = '{tbl}'
        ORDER BY ORDINAL_POSITION
    """)
    rows = cur.fetchall()
    print(f"\n  {tbl}")
    print(f"  {'COLUMN':<40}  {'TYPE':<20}  NULLABLE")
    print("  " + "-"*70)
    for r in rows:
        print(f"  {r[0]:<40}  {r[1]:<20}  {r[2]}")

# Also show current registry contents
cur.execute("SELECT * FROM STREAMLIT_APPS.DBO.ML_MODEL_REGISTRY ORDER BY 1 DESC LIMIT 3")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
print(f"\n  ML_MODEL_REGISTRY current rows ({len(rows)}):")
print(f"  {' | '.join(cols)}")
for r in rows:
    print(f"  {' | '.join(str(v) for v in r)}")

conn.close()
