import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection(role='DEVELOPER',warehouse='REPORTING_WH',database='ANALYTICS_DEV',schema='DBT_NFOLD_TRANSFORMATION')
q = """
SELECT vendor,
       COUNT(*) AS row_ct,
       COUNT_IF(trt_match_key IS NOT NULL AND TRIM(trt_match_key) <> '') AS has_trt_key,
       COUNT_IF(cw_sku = 'UNMAPPED') AS unmapped_rows
FROM RECON_SKU_MAP
WHERE vendor IN ('SentinelOne','Exium')
GROUP BY 1
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

q2 = "SELECT COUNT(*) AS row_ct FROM THIRD_PARTY_RECON_SKU_MAP_PROD WHERE vendor='SentinelOne'"
print(fetch_dataframe(q2, conn=conn).to_string(index=False))
