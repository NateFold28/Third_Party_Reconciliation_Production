import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

sql = """
SELECT table_name, table_type
FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
WHERE table_schema='DBT_NFOLD_TRANSFORMATION'
  AND table_name IN ('THIRD_PARTY_RECON_SOURCE_TRT_PROD','THIRD_PARTY_RECON_TRT_BILLING_PROD','WEBROOT_TRT_USAGE_MONTHLY')
ORDER BY 1;
"""
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
try:
    df = fetch_dataframe(sql, conn=conn)
    print(df.to_string(index=False))
finally:
    conn.close()
