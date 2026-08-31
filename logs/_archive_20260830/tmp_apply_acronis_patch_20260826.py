from pathlib import Path
from TEMPLATES.Python.connection import get_snowflake_connection

sql_path = Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/Acronis_Reconciliation_Script_Prod.sql")
sql = sql_path.read_text(encoding='utf-8')

conn = get_snowflake_connection()
cur = conn.cursor()
try:
    cur.execute("USE ROLE DEVELOPER")
    cur.execute("USE WAREHOUSE REPORTING_WH")
    cur.execute("USE DATABASE ANALYTICS_DEV")
    cur.execute("USE SCHEMA DBT_NFOLD_TRANSFORMATION")
    for _ in conn.execute_string(sql, remove_comments=True):
        pass
    print("Acronis script executed successfully.")
finally:
    cur.close()
    conn.close()
