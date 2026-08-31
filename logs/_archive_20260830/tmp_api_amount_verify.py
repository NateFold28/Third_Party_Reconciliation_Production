import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects')
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH',
                                database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')

print('=== new columns present ===')
q = """
SELECT column_name FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
WHERE table_schema='DBT_NFOLD_TRANSFORMATION' AND table_name='THIRD_PARTY_RECON_OUTPUT_PROD'
  AND column_name IN ('API_QUANTITY','AVG_API_QUANTITY','API_AMOUNT','AVG_API_AMOUNT',
                      'API_AVG_MINUS_POINT_AMOUNT','VENDOR_UNIT_PRICE','VENDOR_AMOUNT','ZUORA_AMOUNT')
ORDER BY column_name
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print('\n=== Proofpoint: monthly amount comparison ===')
q2 = """
SELECT billing_month,
       ROUND(SUM(vendor_amount), 0)    AS vendor_amount_actual,
       ROUND(SUM(api_amount), 0)       AS api_amount_pointintime,
       ROUND(SUM(avg_api_amount), 0)   AS api_amount_avg,
       ROUND(SUM(zuora_amount), 0)     AS zuora_amount,
       ROUND(SUM(avg_api_amount) - SUM(api_amount), 0) AS delta_avg_minus_point
FROM THIRD_PARTY_RECON_OUTPUT_PROD
WHERE vendor='Proofpoint'
GROUP BY billing_month ORDER BY billing_month
"""
print(fetch_dataframe(q2, conn=conn).to_string(index=False))

print('\n=== Proofpoint: per-product avg-vs-point delta (May 2026) ===')
q3 = """
SELECT vendor_product,
       COUNT(*) AS rows_n,
       ROUND(SUM(api_amount), 0)        AS amt_point,
       ROUND(SUM(avg_api_amount), 0)    AS amt_avg,
       ROUND(SUM(avg_api_amount) - SUM(api_amount), 0) AS delta,
       ROUND(SUM(vendor_amount), 0)     AS vendor_amt_actual,
       ROUND(SUM(zuora_amount), 0)      AS zuora_amt
FROM THIRD_PARTY_RECON_OUTPUT_PROD
WHERE vendor='Proofpoint' AND billing_month='2026-05-01'
GROUP BY vendor_product ORDER BY amt_point DESC
"""
print(fetch_dataframe(q3, conn=conn).to_string(index=False))

print('\n=== sample rows: sf_id level ===')
q4 = """
SELECT sf_id, vendor_product,
       api_quantity, avg_api_quantity, vendor_unit_price,
       api_amount, avg_api_amount, vendor_amount, zuora_amount
FROM THIRD_PARTY_RECON_OUTPUT_PROD
WHERE vendor='Proofpoint' AND billing_month='2026-05-01' AND api_quantity IS NOT NULL
ORDER BY api_amount DESC LIMIT 10
"""
print(fetch_dataframe(q4, conn=conn).to_string(index=False))

conn.close()
