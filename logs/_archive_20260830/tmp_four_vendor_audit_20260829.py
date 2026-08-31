import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

queries = {
    "detail_api": """
SELECT vendor,
       COUNT(*) AS row_count,
       COUNT_IF(api_quantity IS NOT NULL) AS api_row_count,
       COUNT_IF(avg_api_quantity IS NOT NULL) AS avg_api_row_count,
       ROUND(COUNT_IF(api_quantity IS NOT NULL) / NULLIF(COUNT(*),0), 4) AS api_ratio,
       ROUND(COUNT_IF(avg_api_quantity IS NOT NULL) / NULLIF(COUNT(*),0), 4) AS avg_api_ratio,
       SUM(COALESCE(api_quantity,0)) AS api_sum,
       SUM(COALESCE(avg_api_quantity,0)) AS avg_api_sum
FROM (
    SELECT 'Proofpoint' AS vendor, api_quantity, avg_api_quantity FROM PROOFPOINT_RECON_DETAIL
    UNION ALL
    SELECT 'Acronis' AS vendor, api_quantity, avg_api_quantity FROM ACRONIS_RECON_DETAIL
    UNION ALL
    SELECT 'SentinelOne' AS vendor, api_quantity, avg_api_quantity FROM SENTINELONE_RECON_DETAIL
    UNION ALL
    SELECT 'Exium' AS vendor, api_quantity, avg_api_quantity FROM EXIUM_RECON_DETAIL
)
GROUP BY 1
ORDER BY 1;
""",
    "output_api": """
SELECT vendor,
       COUNT(*) AS row_count,
       COUNT_IF(api_quantity IS NOT NULL) AS api_row_count,
       COUNT_IF(avg_api_quantity IS NOT NULL) AS avg_api_row_count,
       ROUND(COUNT_IF(api_quantity IS NOT NULL) / NULLIF(COUNT(*),0), 4) AS api_ratio,
       ROUND(COUNT_IF(avg_api_quantity IS NOT NULL) / NULLIF(COUNT(*),0), 4) AS avg_api_ratio,
       SUM(COALESCE(api_quantity,0)) AS api_sum,
       SUM(COALESCE(avg_api_quantity,0)) AS avg_api_sum
FROM THIRD_PARTY_RECON_OUTPUT_PROD
WHERE vendor IN ('Proofpoint','Acronis','SentinelOne','Exium')
GROUP BY 1
ORDER BY 1;
""",
    "recent_partner_sku": """
WITH base AS (
    SELECT vendor,
           sf_id,
           billing_month,
           UPPER(TRIM(COALESCE(vendor_product,''))) AS vendor_product,
           SUM(COALESCE(vendor_quantity,0)) AS vendor_qty,
           SUM(COALESCE(total_billing_quantity,0)) AS billing_qty,
           SUM(COALESCE(api_quantity,0)) AS api_qty,
           SUM(COALESCE(avg_api_quantity,0)) AS avg_api_qty,
           SUM(COALESCE(vendor_amount,0)) AS vendor_amt,
           SUM(COALESCE(total_billing_amount,0)) AS billing_amt
    FROM THIRD_PARTY_RECON_OUTPUT_PROD
    WHERE vendor IN ('Proofpoint','Acronis','SentinelOne','Exium')
      AND billing_month >= DATEADD(month,-3,DATE_TRUNC(month,CURRENT_DATE()))
    GROUP BY 1,2,3,4
)
SELECT vendor,
       COUNT(*) AS group_count_3mo,
       COUNT_IF(api_qty > 0) AS groups_with_api,
       ROUND(COUNT_IF(api_qty > 0)/NULLIF(COUNT(*),0),4) AS api_group_ratio,
       ROUND(AVG(ABS(vendor_qty - billing_qty)),3) AS avg_abs_qty_gap,
       ROUND(SUM(vendor_amt - billing_amt),2) AS net_amt_delta,
       ROUND(AVG(ABS(vendor_amt - billing_amt)),2) AS avg_abs_amt_gap
FROM base
GROUP BY 1
ORDER BY 1;
"""
}

conn = get_snowflake_connection()
try:
    with conn.cursor() as cur:
        cur.execute("USE ROLE DEVELOPER")
        cur.execute("USE WAREHOUSE REPORTING_WH")
        cur.execute("USE DATABASE ANALYTICS_DEV")
        cur.execute("USE SCHEMA DBT_NFOLD_TRANSFORMATION")
    for name, sql in queries.items():
        print(f"\n=== {name} ===")
        df = fetch_dataframe(sql, conn=conn)
        print(df.to_string(index=False))
finally:
    conn.close()
