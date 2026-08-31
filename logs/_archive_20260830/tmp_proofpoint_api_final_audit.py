import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects')
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH',
                                database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')

print('=== Proofpoint API coverage in DETAIL_PROD (post-emit) ===')
q1 = """
SELECT billing_month,
       COUNT(*) AS rows_n,
       COUNT(api_quantity) AS api_nn,
       SUM(IFF(api_quantity > 0, 1, 0)) AS api_gt_zero,
       ROUND(AVG(api_quantity), 2) AS avg_api,
       ROUND(AVG(vendor_quantity), 2) AS avg_vendor,
       ROUND(AVG(api_quantity) / NULLIF(AVG(vendor_quantity), 0), 3) AS avg_ratio
FROM THIRD_PARTY_RECON_DETAIL_PROD
WHERE vendor='Proofpoint'
GROUP BY billing_month ORDER BY billing_month
"""
print(fetch_dataframe(q1, conn=conn).to_string(index=False))

print('\n=== Proofpoint API coverage in OUTPUT_PROD (final app-facing table) ===')
q2 = """
SELECT billing_month,
       COUNT(*) AS rows_n,
       COUNT(api_quantity) AS api_nn,
       SUM(IFF(api_quantity > 0, 1, 0)) AS api_gt_zero,
       ROUND(AVG(api_quantity), 2) AS avg_api,
       ROUND(AVG(vendor_quantity), 2) AS avg_vendor,
       ROUND(AVG(api_quantity) / NULLIF(AVG(vendor_quantity), 0), 3) AS avg_ratio
FROM THIRD_PARTY_RECON_OUTPUT_PROD
WHERE vendor='Proofpoint'
GROUP BY billing_month ORDER BY billing_month
"""
print(fetch_dataframe(q2, conn=conn).to_string(index=False))

print('\n=== sample Proofpoint OUTPUT_PROD rows with API columns ===')
q3 = """
SELECT sf_id, billing_month, vendor_product,
       api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity,
       outcome_flag
FROM THIRD_PARTY_RECON_OUTPUT_PROD
WHERE vendor='Proofpoint' AND billing_month='2026-05-01' AND api_quantity IS NOT NULL
ORDER BY api_quantity DESC LIMIT 10
"""
print(fetch_dataframe(q3, conn=conn).to_string(index=False))

print('\n=== Sanity: are API_QUANTITY / AVG_API_QUANTITY populated in OUTPUT_PROD across all vendors? ===')
q4 = """
SELECT vendor,
       COUNT(*) rows_n,
       COUNT(api_quantity) api_nn,
       COUNT(avg_api_quantity) avg_api_nn,
       ROUND(AVG(NULLIF(api_quantity,0)), 2) mean_api
FROM THIRD_PARTY_RECON_OUTPUT_PROD
GROUP BY vendor ORDER BY vendor
"""
print(fetch_dataframe(q4, conn=conn).to_string(index=False))

conn.close()
