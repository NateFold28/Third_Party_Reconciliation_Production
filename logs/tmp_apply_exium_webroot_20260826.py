from pathlib import Path
from TEMPLATES.Python.connection import get_snowflake_connection

files = [
    Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/Exium_Reconciliation_Script_Prod.sql"),
    Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/Webroot_Reconciliation_Script_Prod.sql"),
]

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
try:
    for path in files:
        sql_text = path.read_text(encoding='utf-8')
        clean_lines = [ln for ln in sql_text.splitlines() if not ln.strip().startswith('--')]
        clean_sql = '\n'.join(clean_lines).strip()
        cursors = conn.execute_string(clean_sql)
        print(f"Executed {len(cursors)} statements from {path.name}")
finally:
    conn.close()
