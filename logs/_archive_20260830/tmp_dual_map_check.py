import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection(role='DEVELOPER',warehouse='REPORTING_WH',database='ANALYTICS_DEV',schema='DBT_NFOLD_TRANSFORMATION')

cur = conn.cursor()
# Are both tables real?
for name in ['RECON_SKU_MAP', 'THIRD_PARTY_RECON_SKU_MAP_PROD']:
    cur.execute(f"SHOW OBJECTS LIKE '{name}' IN SCHEMA ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION")
    print([r[:5] for r in cur.fetchall()])

# Compare row counts by vendor
q = """
SELECT
  'RECON_SKU_MAP' AS src,
  vendor,
  COUNT(*) AS row_ct,
  COUNT_IF(trt_match_key IS NOT NULL AND TRIM(trt_match_key) <> '') AS trt_keys
FROM RECON_SKU_MAP
WHERE vendor IN ('SentinelOne','Exium')
GROUP BY 1,2
UNION ALL
SELECT
  'THIRD_PARTY_RECON_SKU_MAP_PROD',
  vendor,
  COUNT(*),
  COUNT_IF(trt_match_key IS NOT NULL AND TRIM(trt_match_key) <> '')
FROM THIRD_PARTY_RECON_SKU_MAP_PROD
WHERE vendor IN ('SentinelOne','Exium')
GROUP BY 1,2
ORDER BY 2,1
"""
print()
print(fetch_dataframe(q, conn=conn).to_string(index=False))
