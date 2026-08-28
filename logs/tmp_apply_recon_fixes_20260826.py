from pathlib import Path
from TEMPLATES.Python.connection import get_snowflake_connection

files = [
    Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Maps/sql/02_unified_reference_maps.sql"),
    Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/Auvik_Reconciliation_Script_Prod.sql"),
    Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/Exium_Reconciliation_Script_Prod.sql"),
    Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/Webroot_Reconciliation_Script_Prod.sql"),
]

def split_statements_naive(sql_text: str):
    lines = []
    for ln in sql_text.splitlines():
        if ln.strip().startswith('--'):
            continue
        lines.append(ln)
    cleaned = '\n'.join(lines)
    for part in cleaned.split(';'):
        stmt = part.strip()
        if stmt:
            yield stmt

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
try:
    with conn.cursor() as cur:
        for path in files:
            sql_text = path.read_text(encoding='utf-8')
            print(f"\\n=== Applying {path.name} ===")
            count = 0
            for stmt in split_statements_naive(sql_text):
                cur.execute(stmt)
                count += 1
            print(f"Executed {count} statements from {path.name}")
finally:
    conn.close()
