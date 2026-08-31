import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(
    role="DEVELOPER",
    warehouse="REPORTING_WH",
    database="ANALYTICS_DEV",
    schema="DBT_NFOLD_TRANSFORMATION",
)

print("=== SentinelOne: no-TRT-key groups with map fields and raw TRT exact hit checks ===")
q = """
WITH no_key AS (
  SELECT DISTINCT
    sku_match_key AS sku_match_group,
    cw_sku,
    vendor_sku,
    vendor_product,
    UPPER(TRIM(cw_sku)) AS cw_sku_u,
    UPPER(TRIM(vendor_sku)) AS vendor_sku_u,
    UPPER(TRIM(vendor_product)) AS vendor_product_u
  FROM RECON_SKU_MAP
  WHERE vendor='SentinelOne'
    AND (trt_match_key IS NULL OR TRIM(trt_match_key) = '')
),
trt AS (
  SELECT DISTINCT UPPER(TRIM(product_sku)) AS product_sku_u
  FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
  WHERE on_date >= DATEADD(day, -365, CURRENT_TIMESTAMP())
    AND product_sku IS NOT NULL
)
SELECT
  n.sku_match_group,
  n.cw_sku,
  n.vendor_sku,
  n.vendor_product,
  IFF(t1.product_sku_u IS NOT NULL, 1, 0) AS cw_exact_in_trt,
  IFF(t2.product_sku_u IS NOT NULL, 1, 0) AS vendor_sku_exact_in_trt,
  IFF(t3.product_sku_u IS NOT NULL, 1, 0) AS vendor_product_exact_in_trt
FROM no_key n
LEFT JOIN trt t1 ON t1.product_sku_u = n.cw_sku_u
LEFT JOIN trt t2 ON t2.product_sku_u = n.vendor_sku_u
LEFT JOIN trt t3 ON t3.product_sku_u = n.vendor_product_u
ORDER BY 1,2,3
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== SentinelOne: high-confidence hardcode candidates (exact TRT hits) ===")
q = """
WITH no_key AS (
  SELECT DISTINCT
    sku_match_key AS sku_match_group,
    cw_sku,
    vendor_sku,
    vendor_product,
    UPPER(TRIM(cw_sku)) AS cw_sku_u,
    UPPER(TRIM(vendor_sku)) AS vendor_sku_u,
    UPPER(TRIM(vendor_product)) AS vendor_product_u
  FROM RECON_SKU_MAP
  WHERE vendor='SentinelOne'
    AND (trt_match_key IS NULL OR TRIM(trt_match_key) = '')
),
trt AS (
  SELECT DISTINCT UPPER(TRIM(product_sku)) AS product_sku_u
  FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
  WHERE on_date >= DATEADD(day, -365, CURRENT_TIMESTAMP())
    AND product_sku IS NOT NULL
),
cand AS (
  SELECT
    n.sku_match_group,
    n.cw_sku,
    n.vendor_sku,
    n.vendor_product,
    CASE
      WHEN t1.product_sku_u IS NOT NULL THEN n.cw_sku_u
      WHEN t2.product_sku_u IS NOT NULL THEN n.vendor_sku_u
      WHEN t3.product_sku_u IS NOT NULL THEN n.vendor_product_u
      ELSE NULL
    END AS candidate_trt_key
  FROM no_key n
  LEFT JOIN trt t1 ON t1.product_sku_u = n.cw_sku_u
  LEFT JOIN trt t2 ON t2.product_sku_u = n.vendor_sku_u
  LEFT JOIN trt t3 ON t3.product_sku_u = n.vendor_product_u
)
SELECT *
FROM cand
WHERE candidate_trt_key IS NOT NULL
ORDER BY sku_match_group, cw_sku, vendor_sku
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Exium groups without TRT key by volume (last 3 months) ===")
q = """
WITH key_groups AS (
  SELECT DISTINCT COALESCE(sku_match_key, vendor_sku) AS sku_match_group
  FROM RECON_SKU_MAP
  WHERE vendor='Exium'
    AND trt_match_key IS NOT NULL
    AND TRIM(trt_match_key) <> ''
)
SELECT
  d.sku_match_group,
  COUNT(*) AS row_ct,
  ROUND(SUM(d.vendor_quantity),0) AS vendor_qty,
  COUNT_IF(d.api_quantity IS NOT NULL) AS api_rows
FROM EXIUM_RECON_DETAIL d
LEFT JOIN key_groups k
  ON k.sku_match_group = d.sku_match_group
WHERE d.billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
  AND (k.sku_match_group IS NULL)
GROUP BY 1
ORDER BY vendor_qty DESC NULLS LAST
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Exium no-key map rows and exact TRT hits ===")
q = """
WITH no_key AS (
  SELECT DISTINCT
    COALESCE(sku_match_key, vendor_sku) AS sku_match_group,
    cw_sku,
    vendor_sku,
    vendor_product,
    UPPER(TRIM(cw_sku)) AS cw_sku_u,
    UPPER(TRIM(vendor_sku)) AS vendor_sku_u,
    UPPER(TRIM(vendor_product)) AS vendor_product_u
  FROM RECON_SKU_MAP
  WHERE vendor='Exium'
    AND (trt_match_key IS NULL OR TRIM(trt_match_key) = '')
),
trt AS (
  SELECT DISTINCT UPPER(TRIM(product_sku)) AS product_sku_u
  FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
  WHERE on_date >= DATEADD(day, -365, CURRENT_TIMESTAMP())
    AND product_sku IS NOT NULL
)
SELECT
  n.sku_match_group,
  n.cw_sku,
  n.vendor_sku,
  n.vendor_product,
  IFF(t1.product_sku_u IS NOT NULL, 1, 0) AS cw_exact_in_trt,
  IFF(t2.product_sku_u IS NOT NULL, 1, 0) AS vendor_sku_exact_in_trt,
  IFF(t3.product_sku_u IS NOT NULL, 1, 0) AS vendor_product_exact_in_trt
FROM no_key n
LEFT JOIN trt t1 ON t1.product_sku_u = n.cw_sku_u
LEFT JOIN trt t2 ON t2.product_sku_u = n.vendor_sku_u
LEFT JOIN trt t3 ON t3.product_sku_u = n.vendor_product_u
ORDER BY 1,2,3
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

conn.close()
