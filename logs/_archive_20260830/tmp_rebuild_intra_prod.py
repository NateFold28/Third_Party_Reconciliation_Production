from pathlib import Path
from TEMPLATES.Python.connection import get_snowflake_connection

sql_path = Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/10_vendor_invoice_usage_intra_prod.sql")
sql_text = sql_path.read_text(encoding='utf-8')
parts = [p.strip() for p in sql_text.split(';') if p.strip()]
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()
try:
    for i, stmt in enumerate(parts, 1):
        cur.execute(stmt)
        print(f"Executed statement {i}/{len(parts)}")
finally:
    cur.close()
    conn.close()
print('Intra rebuild complete.')
