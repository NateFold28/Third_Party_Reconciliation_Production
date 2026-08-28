from pathlib import Path
from TEMPLATES.Python.connection import get_snowflake_connection
repo=Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline")
for rel in [r"Maps/sql/00b_backfill_invoice_prices.sql", r"Reconciliation/10_vendor_invoice_usage_intra_prod.sql"]:
    sql=(repo/rel).read_text(encoding='utf-8')
    conn=get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
    try:
        n=0
        for c in conn.execute_string(sql, return_cursors=True):
            n+=1
            try: c.fetchall()
            except Exception: pass
        conn.commit()
        print(f"{rel}: executed {n} statements")
    finally:
        conn.close()
