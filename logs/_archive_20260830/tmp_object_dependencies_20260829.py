import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

sql = """
SELECT referencing_database,
       referencing_schema,
       referencing_object_name,
       referencing_object_domain,
       referenced_database,
       referenced_schema,
       referenced_object_name,
       referenced_object_domain
FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES
WHERE referenced_database = 'ANALYTICS_DEV'
  AND referenced_schema = 'DBT_NFOLD_TRANSFORMATION'
  AND referenced_object_name = 'THIRD_PARTY_RECON_SOURCE_TRT_PROD'
ORDER BY 1,2,3;
"""
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
try:
    df = fetch_dataframe(sql, conn=conn)
    if df.empty:
        print('(no dependencies returned)')
    else:
        print(df.to_string(index=False))
finally:
    conn.close()
