import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(
    role="DEVELOPER",
    warehouse="REPORTING_WH",
    database="ANALYTICS_DEV",
    schema="DBT_NFOLD_TRANSFORMATION",
)

print("=== Exium map rows currently in RECON_SKU_MAP ===")
q = """
SELECT vendor, vendor_product, vendor_sku, cw_sku, sku_match_key, trt_match_key
FROM RECON_SKU_MAP
WHERE vendor = 'Exium'
ORDER BY sku_match_key, cw_sku, vendor_sku
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Exium no-api sku groups with/without key (last 3 months) ===")
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
  COUNT_IF(d.api_quantity IS NOT NULL) AS api_rows,
  IFF(MAX(IFF(k.sku_match_group IS NOT NULL,1,0))=1, 'HAS_KEY', 'NO_KEY') AS key_status
FROM EXIUM_RECON_DETAIL d
LEFT JOIN key_groups k
  ON k.sku_match_group = d.sku_match_group
WHERE d.billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
  AND d.api_quantity IS NULL
GROUP BY 1
ORDER BY vendor_qty DESC NULLS LAST
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== SentinelOne: candidate TRT key lift for no-key groups (last 3 months) ===")
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
no_key_acct_month AS (
  SELECT DISTINCT
    d.sku_match_group,
    d.sf_id,
    d.billing_month,
    pm.cms_id,
    DATEADD('day', 20, d.billing_month)::DATE AS snapshot_date,
    DATEADD('day', 20, DATEADD('month', -1, d.billing_month))::DATE AS prev_snapshot_date
  FROM SENTINELONE_RECON_DETAIL d
  LEFT JOIN pm ON pm.sf_id = d.sf_id
  LEFT JOIN key_groups k ON k.sku_match_group = d.sku_match_group
  WHERE d.billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
    AND d.sf_id IS NOT NULL
    AND pm.cms_id IS NOT NULL
    AND k.sku_match_group IS NULL
),
all_acct_month AS (
  SELECT DISTINCT
    d.sf_id,
    d.billing_month,
    pm.cms_id,
    DATEADD('day', 20, d.billing_month)::DATE AS snapshot_date,
    DATEADD('day', 20, DATEADD('month', -1, d.billing_month))::DATE AS prev_snapshot_date
  FROM SENTINELONE_RECON_DETAIL d
  LEFT JOIN pm ON pm.sf_id = d.sf_id
  WHERE d.billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
    AND d.sf_id IS NOT NULL
    AND pm.cms_id IS NOT NULL
),
no_key_hits AS (
  SELECT DISTINCT
    n.sku_match_group,
    n.sf_id,
    n.billing_month,
    UPPER(TRIM(u.product_sku)) AS product_sku
  FROM no_key_acct_month n
  JOIN ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
    ON u.partner_id::VARCHAR = n.cms_id
   AND u.on_date::DATE > n.prev_snapshot_date
   AND u.on_date::DATE <= n.snapshot_date
  WHERE u.product_sku IS NOT NULL
),
all_hits AS (
  SELECT DISTINCT
    a.sf_id,
    a.billing_month,
    UPPER(TRIM(u.product_sku)) AS product_sku
  FROM all_acct_month a
  JOIN ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
    ON u.partner_id::VARCHAR = a.cms_id
   AND u.on_date::DATE > a.prev_snapshot_date
   AND u.on_date::DATE <= a.snapshot_date
  WHERE u.product_sku IS NOT NULL
),
group_denoms AS (
  SELECT sku_match_group, COUNT(*) AS group_acct_months
  FROM no_key_acct_month
  GROUP BY 1
),
all_denom AS (
  SELECT COUNT(*) AS all_acct_months FROM all_acct_month
),
cand AS (
  SELECT
    h.sku_match_group,
    h.product_sku,
    COUNT(*) AS group_hits,
    d.group_acct_months,
    ROUND(100.0 * COUNT(*) / NULLIF(d.group_acct_months,0), 1) AS group_hit_pct,
    a.sku_all_hits,
    ad.all_acct_months,
    ROUND(100.0 * a.sku_all_hits / NULLIF(ad.all_acct_months,0), 1) AS global_hit_pct,
    ROUND((COUNT(*) / NULLIF(d.group_acct_months,0)) / NULLIF(a.sku_all_hits / NULLIF(ad.all_acct_months,0),0), 2) AS lift
  FROM no_key_hits h
  JOIN group_denoms d
    ON d.sku_match_group = h.sku_match_group
  JOIN (
    SELECT product_sku, COUNT(*) AS sku_all_hits
    FROM all_hits
    GROUP BY 1
  ) a
    ON a.product_sku = h.product_sku
  CROSS JOIN all_denom ad
  GROUP BY 1,2,4,6,7
)
SELECT *
FROM cand
WHERE group_hits >= 5
  AND group_hit_pct >= 20
  AND lift >= 1.5
QUALIFY ROW_NUMBER() OVER (PARTITION BY sku_match_group ORDER BY lift DESC, group_hits DESC) <= 10
ORDER BY sku_match_group, lift DESC, group_hits DESC
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

conn.close()
