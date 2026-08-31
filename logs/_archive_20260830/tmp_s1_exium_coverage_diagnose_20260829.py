import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(
    role="DEVELOPER",
    warehouse="REPORTING_WH",
    database="ANALYTICS_DEV",
    schema="DBT_NFOLD_TRANSFORMATION",
)

print("=== SentinelOne map footprint (source of truth) ===")
q = """
SELECT
  COUNT(*) AS map_rows,
  COUNT(DISTINCT cw_sku) AS cw_sku_distinct,
  COUNT_IF(cw_sku = 'UNMAPPED') AS unmapped_rows,
  COUNT_IF(trt_match_key IS NOT NULL AND TRIM(trt_match_key) <> '') AS trt_key_rows,
  COUNT(DISTINCT IFF(trt_match_key IS NOT NULL AND TRIM(trt_match_key) <> '', trt_match_key, NULL)) AS trt_key_distinct
FROM THIRD_PARTY_RECON_SKU_MAP_PROD
WHERE vendor = 'SentinelOne'
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== SentinelOne map footprint (derived RECON_SKU_MAP actually used by recon) ===")
q = """
SELECT
  COUNT(*) AS recon_rows,
  COUNT(DISTINCT cw_sku) AS cw_sku_distinct,
  COUNT_IF(cw_sku = 'UNMAPPED') AS unmapped_rows,
  COUNT_IF(trt_match_key IS NOT NULL AND TRIM(trt_match_key) <> '') AS trt_key_rows,
  COUNT(DISTINCT IFF(trt_match_key IS NOT NULL AND TRIM(trt_match_key) <> '', trt_match_key, NULL)) AS trt_key_distinct,
  COUNT(DISTINCT sku_match_key) AS sku_groups
FROM RECON_SKU_MAP
WHERE vendor = 'SentinelOne'
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== SentinelOne recon row-level API coverage (all time + last 3 months) ===")
q = """
WITH base AS (
  SELECT *,
         IFF(api_quantity IS NOT NULL, 1, 0) AS has_api,
         IFF(sf_id IS NOT NULL, 1, 0) AS has_sf
  FROM SENTINELONE_RECON_DETAIL
)
SELECT
  'all_time' AS scope,
  COUNT(*) AS row_ct,
  COUNT_IF(has_api = 1) AS api_rows,
  ROUND(100.0 * COUNT_IF(has_api = 1) / NULLIF(COUNT(*),0), 1) AS api_row_pct,
  COUNT_IF(has_sf = 0) AS no_sf_rows
FROM base
UNION ALL
SELECT
  'last_3_months' AS scope,
  COUNT(*) AS row_ct,
  COUNT_IF(has_api = 1) AS api_rows,
  ROUND(100.0 * COUNT_IF(has_api = 1) / NULLIF(COUNT(*),0), 1) AS api_row_pct,
  COUNT_IF(has_sf = 0) AS no_sf_rows
FROM base
WHERE billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== SentinelOne coverage decomposition: why API is null? (last 3 months) ===")
q = """
WITH trt_groups AS (
  SELECT DISTINCT sku_match_key AS sku_match_group
  FROM RECON_SKU_MAP
  WHERE vendor = 'SentinelOne'
    AND trt_match_key IS NOT NULL
    AND TRIM(trt_match_key) <> ''
),
rows3 AS (
  SELECT
    d.sf_id,
    d.billing_month,
    d.sku_match_group,
    d.vendor_quantity,
    d.api_quantity,
    pm.cms_id,
    IFF(g.sku_match_group IS NOT NULL, 1, 0) AS group_has_trt_key
  FROM SENTINELONE_RECON_DETAIL d
  LEFT JOIN RECON_PARTNER_MAP pm
    ON pm.sf_id = d.sf_id
  LEFT JOIN trt_groups g
    ON g.sku_match_group = d.sku_match_group
  WHERE d.billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
)
SELECT
  CASE
    WHEN api_quantity IS NOT NULL THEN 'HAS_API'
    WHEN sf_id IS NULL THEN 'NO_SF_ID'
    WHEN cms_id IS NULL OR TRIM(cms_id) = '' THEN 'NO_CMS_MAP'
    WHEN sku_match_group IS NULL THEN 'NO_SKU_GROUP'
    WHEN group_has_trt_key = 0 THEN 'GROUP_WITHOUT_TRT_KEY'
    ELSE 'HAS_KEY_BUT_NO_RAW_USAGE_IN_WINDOW'
  END AS reason,
  COUNT(*) AS row_ct,
  ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 1) AS pct
FROM rows3
GROUP BY 1
ORDER BY row_ct DESC
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== SentinelOne biggest no-API sku groups by vendor qty (last 3 months) ===")
q = """
SELECT
  sku_match_group,
  COUNT(*) AS row_ct,
  ROUND(SUM(vendor_quantity),0) AS vendor_qty,
  COUNT_IF(api_quantity IS NOT NULL) AS api_rows
FROM SENTINELONE_RECON_DETAIL
WHERE billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
GROUP BY 1
HAVING COUNT_IF(api_quantity IS NULL) > 0
ORDER BY vendor_qty DESC NULLS LAST
LIMIT 20
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== S1 map rows without TRT key but still active vendor qty in last 3 months ===")
q = """
WITH map_no_key AS (
  SELECT DISTINCT sku_match_key AS sku_match_group, cw_sku
  FROM RECON_SKU_MAP
  WHERE vendor = 'SentinelOne'
    AND (trt_match_key IS NULL OR TRIM(trt_match_key) = '')
    AND cw_sku IS NOT NULL
    AND TRIM(cw_sku) <> ''
    AND cw_sku <> 'UNMAPPED'
),
qty AS (
  SELECT sku_match_group, SUM(vendor_quantity) AS vendor_qty, COUNT(*) AS rows
  FROM SENTINELONE_RECON_DETAIL
  WHERE billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
  GROUP BY 1
)
SELECT m.sku_match_group, m.cw_sku, q.vendor_qty, q.rows
FROM map_no_key m
LEFT JOIN qty q
  ON q.sku_match_group = m.sku_match_group
ORDER BY q.vendor_qty DESC NULLS LAST, m.sku_match_group, m.cw_sku
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Candidate fuzzy normalization matches for S1 no-key map rows against raw TRT product_sku ===")
q = """
WITH map_no_key AS (
  SELECT DISTINCT
    cw_sku,
    UPPER(REGEXP_REPLACE(TRIM(cw_sku), '[^A-Z0-9]', '')) AS norm
  FROM RECON_SKU_MAP
  WHERE vendor = 'SentinelOne'
    AND (trt_match_key IS NULL OR TRIM(trt_match_key) = '')
    AND cw_sku IS NOT NULL
    AND TRIM(cw_sku) <> ''
    AND cw_sku <> 'UNMAPPED'
),
trt_recent AS (
  SELECT DISTINCT
    product_sku,
    UPPER(REGEXP_REPLACE(TRIM(product_sku), '[^A-Z0-9]', '')) AS norm
  FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
  WHERE on_date >= DATEADD(day, -180, CURRENT_TIMESTAMP())
    AND product_sku IS NOT NULL
)
SELECT
  m.cw_sku,
  t.product_sku AS candidate_trt_sku,
  m.norm
FROM map_no_key m
JOIN trt_recent t
  ON t.norm = m.norm
ORDER BY 1,2
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Exium recon row-level API coverage + decomposition (last 3 months) ===")
q = """
WITH trt_groups AS (
  SELECT DISTINCT COALESCE(sku_match_key, vendor_sku) AS sku_match_group
  FROM RECON_SKU_MAP
  WHERE vendor = 'Exium'
    AND trt_match_key IS NOT NULL
    AND TRIM(trt_match_key) <> ''
),
rows3 AS (
  SELECT
    d.sf_id,
    d.billing_month,
    d.sku_match_group,
    d.vendor_quantity,
    d.api_quantity,
    pm.cms_id,
    IFF(g.sku_match_group IS NOT NULL, 1, 0) AS group_has_trt_key
  FROM EXIUM_RECON_DETAIL d
  LEFT JOIN RECON_PARTNER_MAP pm
    ON pm.sf_id = d.sf_id
  LEFT JOIN trt_groups g
    ON g.sku_match_group = d.sku_match_group
  WHERE d.billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
),
classed AS (
  SELECT
    CASE
      WHEN api_quantity IS NOT NULL THEN 'HAS_API'
      WHEN sf_id IS NULL THEN 'NO_SF_ID'
      WHEN cms_id IS NULL OR TRIM(cms_id) = '' THEN 'NO_CMS_MAP'
      WHEN sku_match_group IS NULL THEN 'NO_SKU_GROUP'
      WHEN group_has_trt_key = 0 THEN 'GROUP_WITHOUT_TRT_KEY'
      ELSE 'HAS_KEY_BUT_NO_RAW_USAGE_IN_WINDOW'
    END AS reason
  FROM rows3
)
SELECT reason, COUNT(*) AS row_ct,
       ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 1) AS pct
FROM classed
GROUP BY 1
ORDER BY row_ct DESC
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

conn.close()
