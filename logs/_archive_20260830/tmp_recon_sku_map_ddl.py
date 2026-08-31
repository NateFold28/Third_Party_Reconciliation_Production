import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection(role='DEVELOPER',warehouse='REPORTING_WH',database='ANALYTICS_DEV',schema='DBT_NFOLD_TRANSFORMATION')

cur = conn.cursor()
cur.execute("SHOW OBJECTS LIKE 'RECON_SKU_MAP' IN SCHEMA ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION")
for row in cur.fetchall():
    print(row[:5])
print()
print(fetch_dataframe("SELECT GET_DDL('VIEW', 'ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_SKU_MAP') AS ddl", conn=conn).iloc[0]['DDL'])
