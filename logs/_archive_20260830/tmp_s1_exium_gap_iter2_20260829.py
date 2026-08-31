import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(
    role="DEVELOPER",
    warehouse="REPORTING_WH",
    database="ANALYTICS_DEV",
    schema="DBT_NFOLD_TRANSFORMATION",
)

print("=== Partner map multiplicity check (can cause decomposition fanout) ===")
q = """
SELECT
  COUNT(*) AS sf_rows,
  COUNT(DISTINCT sf_id) AS sf_distinct,
  SUM(empty_cms) AS empty_cms,
  MAX(cms_per_sf) AS max_cms_per_sf,
  AVG(cms_per_sf) AS avg_cms_per_sf
FROM (
  SELECT sf_id,
         COUNT(*) AS cms_per_sf,
         COUNT_IF(cms_id IS NULL OR TRIM(cms_id) = '') AS empty_cms
  FROM RECON_PARTNER_MAP
  GROUP BY 1
)
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== SentinelOne decomposition (last 3 months, deduped sf->cms) ===")
q = """
WITH pm AS (
  SELECT sf_id, MAX(cms_id) AS cms_id
  FROM RECON_PARTNER_MAP
  GROUP BY 1
),
trt_groups AS (
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
  LEFT JOIN pm
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

print("\n=== SentinelOne groups without TRT key by volume (last 3 months) ===")
q = """
WITH key_groups AS (
  SELECT DISTINCT sku_match_key AS sku_match_group
  FROM RECON_SKU_MAP
  WHERE vendor='SentinelOne'
    AND trt_match_key IS NOT NULL
    AND TRIM(trt_match_key) <> ''
)
SELECT
  d.sku_match_group,
  COUNT(*) AS row_ct,
  ROUND(SUM(d.vendor_quantity),0) AS vendor_qty,
  COUNT_IF(d.api_quantity IS NOT NULL) AS api_rows
FROM SENTINELONE_RECON_DETAIL d
LEFT JOIN key_groups k
  ON k.sku_match_group = d.sku_match_group
WHERE d.billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
  AND (k.sku_match_group IS NULL)
GROUP BY 1
ORDER BY vendor_qty DESC NULLS LAST
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== No-key SentinelOne SKU map rows ===")
q = """
SELECT DISTINCT
  sku_match_key AS sku_match_group,
  cw_sku,
  vendor_sku,
  vendor_product,
  trt_match_key
FROM RECON_SKU_MAP
WHERE vendor='SentinelOne'
  AND (trt_match_key IS NULL OR TRIM(trt_match_key) = '')
ORDER BY 1,2
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Candidate TRT product_sku by partner overlap for no-key groups (last 3 months) ===")
q = """
WITH pm AS (
  SELECT sf_id, MAX(cms_id) AS cms_id
  FROM RECON_PARTNER_MAP
  GROUP BY 1
),
key_groups AS (
  SELECT DISTINCT sku_match_key AS sku_match_group
  FROM RECON_SKU_MAP
  WHERE vendor='SentinelOne'
    AND trt_match_key IS NOT NULL
    AND TRIM(trt_match_key) <> ''
),
no_key_rows AS (
  SELECT d.sf_id, d.billing_month, d.sku_match_group, pm.cms_id,
         DATEADD('day', 20, d.billing_month)::DATE AS snapshot_date,
         DATEADD('day', 20, DATEADD('month', -1, d.billing_month))::DATE AS prev_snapshot_date,
         d.vendor_quantity
  FROM SENTINELONE_RECON_DETAIL d
  LEFT JOIN pm ON pm.sf_id = d.sf_id
  LEFT JOIN key_groups k ON k.sku_match_group = d.sku_match_group
  WHERE d.billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
    AND d.sf_id IS NOT NULL
    AND pm.cms_id IS NOT NULL
    AND (k.sku_match_group IS NULL)
),
cand AS (
  SELECT
    n.sku_match_group,
    UPPER(TRIM(u.product_sku)) AS product_sku,
    COUNT(*) AS usage_rows,
    COUNT(DISTINCT n.sf_id || '|' || TO_VARCHAR(n.billing_month)) AS acct_months,
    SUM(COALESCE(u.agent_cnt,0)) AS qty
  FROM no_key_rows n
  JOIN ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
    ON u.partner_id::VARCHAR = n.cms_id
   AND u.on_date::DATE > n.prev_snapshot_date
   AND u.on_date::DATE <= n.snapshot_date
  WHERE u.product_sku IS NOT NULL
  GROUP BY 1,2
)
SELECT *
FROM cand
QUALIFY ROW_NUMBER() OVER (PARTITION BY sku_match_group ORDER BY acct_months DESC, qty DESC) <= 15
ORDER BY sku_match_group, acct_months DESC, qty DESC
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Exium decomposition (last 3 months, deduped sf->cms) ===")
q = """
WITH pm AS (
  SELECT sf_id, MAX(cms_id) AS cms_id
  FROM RECON_PARTNER_MAP
  GROUP BY 1
),
trt_groups AS (
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
  LEFT JOIN pm
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
