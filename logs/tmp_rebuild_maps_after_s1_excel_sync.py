from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd()))
from TEMPLATES.Python.connection import get_snowflake_connection
sql_path = Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Maps/sql/02_unified_reference_maps.sql")
sql_text = sql_path.read_text(encoding="utf-8")
filtered = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
conn = get_snowflake_connection(role="DEVELOPER", warehouse="REPORTING_WH", database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION")
try:
    for cur in conn.execute_string(filtered, return_cursors=True):
        try:
            cur.fetchall()
        except Exception:
            pass
    conn.commit()
    print("Rebuilt unified reference maps")
finally:
    conn.close()
