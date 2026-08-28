from pathlib import Path
from TEMPLATES.Python.connection import get_snowflake_connection
p=Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Maps/sql/03_compat_dead_object_views.sql")
sql=p.read_text(encoding='utf-8')
conn=get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
try:
    for c in conn.execute_string(sql, return_cursors=True):
        try:
            c.fetchall()
        except Exception:
            pass
    conn.commit()
    print('03 executed successfully')
finally:
    conn.close()
