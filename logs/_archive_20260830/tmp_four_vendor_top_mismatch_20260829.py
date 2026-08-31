import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

sql = """
WITH base AS (
    SELECT vendor,
           sf_id,
           billing_month,
           UPPER(TRIM(COALESCE(vendor_product,''))) AS vendor_product,
           SUM(COALESCE(vendor_quantity,0)) AS vendor_qty,
           SUM(COALESCE(total_billing_quantity,0)) AS billing_qty,
           SUM(COALESCE(api_quantity,0)) AS api_qty,
           SUM(COALESCE(vendor_amount,0)) AS vendor_amt,
           SUM(COALESCE(total_billing_amount,0)) AS billing_amt,
           SUM(COALESCE(vendor_amount,0) - COALESCE(total_billing_amount,0)) AS amt_delta
    FROM THIRD_PARTY_RECON_OUTPUT_PROD
    WHERE vendor IN ('Proofpoint','Acronis','SentinelOne','Exium')
      AND billing_month >= DATEADD(month,-3,DATE_TRUNC(month,CURRENT_DATE()))
    GROUP BY 1,2,3,4
), ranked AS (
    SELECT *,
           ABS(amt_delta) AS abs_amt_delta,
           ROW_NUMBER() OVER (PARTITION BY vendor ORDER BY ABS(amt_delta) DESC) AS rn
    FROM base
)
SELECT vendor, sf_id, billing_month, vendor_product,
       vendor_qty, billing_qty, api_qty,
       vendor_amt, billing_amt, amt_delta, abs_amt_delta
FROM ranked
WHERE rn <= 5
ORDER BY vendor, abs_amt_delta DESC;
"""

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
try:
    df = fetch_dataframe(sql, conn=conn)
    print(df.to_string(index=False))
finally:
    conn.close()
