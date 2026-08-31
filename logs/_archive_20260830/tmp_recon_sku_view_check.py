import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects')
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH',
                                database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
q = """
SELECT COUNT(*) AS total, COUNT(trt_match_key) AS with_trt_key
FROM RECON_SKU_MAP
WHERE vendor = 'Acronis'
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))
conn.close()
