import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(
    role="DEVELOPER",
    warehouse="REPORTING_WH",
    database="ANALYTICS_DEV",
    schema="DBT_NFOLD_TRANSFORMATION",
)

vendors = ("Proofpoint", "Acronis", "SentinelOne", "Exium")

print("=== OUTPUT_PROD API population by vendor (last 3 months) ===")
q = """
SELECT
  vendor,
  COUNT(*) AS row_ct,
  COUNT(api_quantity) AS api_rows,
  COUNT(avg_api_quantity) AS avg_api_rows,
  ROUND(100.0 * COUNT(api_quantity) / NULLIF(COUNT(*),0), 1) AS api_row_pct,
  ROUND(SUM(api_quantity), 0) AS api_qty_sum,
  ROUND(SUM(avg_api_quantity), 0) AS avg_api_qty_sum,
  ROUND(SUM(vendor_quantity), 0) AS vendor_qty_sum,
  ROUND(SUM(total_billing_quantity), 0) AS billed_qty_sum
FROM THIRD_PARTY_RECON_OUTPUT_PROD
WHERE vendor IN ('Proofpoint', 'Acronis', 'SentinelOne', 'Exium')
  AND billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
GROUP BY 1
ORDER BY 1
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Detail table API population by vendor (last 3 months) ===")
q = """
WITH unioned AS (
  SELECT 'Proofpoint' AS vendor, billing_month, sf_id, sku_match_group, api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity FROM PROOFPOINT_RECON_DETAIL
  UNION ALL
  SELECT 'Acronis' AS vendor, billing_month, sf_id, sku_match_group, api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity FROM ACRONIS_RECON_DETAIL
  UNION ALL
  SELECT 'SentinelOne' AS vendor, billing_month, sf_id, sku_match_group, api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity FROM SENTINELONE_RECON_DETAIL
  UNION ALL
  SELECT 'Exium' AS vendor, billing_month, sf_id, sku_match_group, api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity FROM EXIUM_RECON_DETAIL
)
SELECT
  vendor,
  COUNT(*) AS row_ct,
  COUNT(api_quantity) AS api_rows,
  ROUND(100.0 * COUNT(api_quantity) / NULLIF(COUNT(*),0), 1) AS api_row_pct,
  ROUND(SUM(api_quantity), 0) AS api_qty_sum,
  ROUND(SUM(vendor_quantity), 0) AS vendor_qty_sum,
  ROUND(SUM(total_billing_quantity), 0) AS billed_qty_sum
FROM unioned
WHERE billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
GROUP BY 1
ORDER BY 1
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Vendor x partner x sku sanity (rows with all qty present, last 3 months) ===")
q = """
WITH unioned AS (
  SELECT 'Proofpoint' AS vendor, billing_month, sf_id, sku_match_group, api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity FROM PROOFPOINT_RECON_DETAIL
  UNION ALL
  SELECT 'Acronis' AS vendor, billing_month, sf_id, sku_match_group, api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity FROM ACRONIS_RECON_DETAIL
  UNION ALL
  SELECT 'SentinelOne' AS vendor, billing_month, sf_id, sku_match_group, api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity FROM SENTINELONE_RECON_DETAIL
  UNION ALL
  SELECT 'Exium' AS vendor, billing_month, sf_id, sku_match_group, api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity FROM EXIUM_RECON_DETAIL
),
base AS (
  SELECT *
  FROM unioned
  WHERE billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
    AND sf_id IS NOT NULL
    AND api_quantity IS NOT NULL
    AND vendor_quantity IS NOT NULL
    AND total_billing_quantity IS NOT NULL
)
SELECT
  vendor,
  COUNT(*) AS grain_rows,
  ROUND(AVG(ABS(total_billing_quantity - vendor_quantity)), 1) AS avg_abs_qty_delta,
  ROUND(MEDIAN(ABS(total_billing_quantity - vendor_quantity)), 1) AS p50_abs_qty_delta,
  ROUND(AVG(ABS(api_quantity - vendor_quantity)), 1) AS avg_abs_api_vendor_delta,
  ROUND(MEDIAN(ABS(api_quantity - vendor_quantity)), 1) AS p50_abs_api_vendor_delta,
  ROUND(AVG(IFF(vendor_quantity = 0, NULL, api_quantity / NULLIF(vendor_quantity,0))), 3) AS avg_api_to_vendor_ratio,
  ROUND(AVG(IFF(total_billing_quantity = 0, NULL, api_quantity / NULLIF(total_billing_quantity,0))), 3) AS avg_api_to_billed_ratio
FROM base
GROUP BY 1
ORDER BY 1
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Top mismatch slices (vendor x sf_id x sku_match_group, last 3 months) ===")
q = """
WITH unioned AS (
  SELECT 'Proofpoint' AS vendor, billing_month, sf_id, sku_match_group, api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity FROM PROOFPOINT_RECON_DETAIL
  UNION ALL
  SELECT 'Acronis' AS vendor, billing_month, sf_id, sku_match_group, api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity FROM ACRONIS_RECON_DETAIL
  UNION ALL
  SELECT 'SentinelOne' AS vendor, billing_month, sf_id, sku_match_group, api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity FROM SENTINELONE_RECON_DETAIL
  UNION ALL
  SELECT 'Exium' AS vendor, billing_month, sf_id, sku_match_group, api_quantity, avg_api_quantity, vendor_quantity, total_billing_quantity FROM EXIUM_RECON_DETAIL
)
SELECT
  vendor,
  sf_id,
  sku_match_group,
  COUNT(*) AS months,
  ROUND(SUM(vendor_quantity), 0) AS vendor_qty,
  ROUND(SUM(total_billing_quantity), 0) AS billed_qty,
  ROUND(SUM(api_quantity), 0) AS api_qty,
  ROUND(SUM(ABS(total_billing_quantity - vendor_quantity)), 0) AS abs_billed_vendor_delta,
  ROUND(SUM(ABS(api_quantity - vendor_quantity)), 0) AS abs_api_vendor_delta
FROM unioned
WHERE billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
  AND sf_id IS NOT NULL
  AND api_quantity IS NOT NULL
GROUP BY 1,2,3
QUALIFY ROW_NUMBER() OVER (PARTITION BY vendor ORDER BY abs_api_vendor_delta DESC NULLS LAST) <= 8
ORDER BY vendor, abs_api_vendor_delta DESC
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Stale TRT snapshot object touchpoints for these 4 vendors ===")
q = """
SELECT
  vendor,
  COUNT(*) AS rows_in_snapshot,
  COUNT(DISTINCT sf_id) AS sf_ids,
  MIN(billing_month) AS min_month,
  MAX(billing_month) AS max_month
FROM THIRD_PARTY_RECON_SOURCE_TRT_PROD
WHERE vendor IN ('Proofpoint','Acronis','SentinelOne','Exium')
GROUP BY 1
ORDER BY 1
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

conn.close()
