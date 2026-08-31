import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

queries = {
  "views_ref_t_rt_prod": """
SELECT table_catalog, table_schema, table_name
FROM ANALYTICS_DEV.INFORMATION_SCHEMA.VIEWS
WHERE UPPER(view_definition) LIKE '%THIRD_PARTY_RECON_SOURCE_TRT_PROD%'
ORDER BY 1,2,3;
""",
  "procedures_ref_t_rt_prod": """
SELECT procedure_catalog, procedure_schema, procedure_name
FROM ANALYTICS_DEV.INFORMATION_SCHEMA.PROCEDURES
WHERE UPPER(procedure_definition) LIKE '%THIRD_PARTY_RECON_SOURCE_TRT_PROD%'
ORDER BY 1,2,3;
""",
  "table_exists": """
SELECT table_catalog, table_schema, table_name, row_count
FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
WHERE table_schema='DBT_NFOLD_TRANSFORMATION'
  AND table_name='THIRD_PARTY_RECON_SOURCE_TRT_PROD';
"""
}

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
try:
  for name, sql in queries.items():
    print(f"\n=== {name} ===")
    df = fetch_dataframe(sql, conn=conn)
    if df.empty:
      print('(no rows)')
    else:
      print(df.to_string(index=False))
finally:
  conn.close()
