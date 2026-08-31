import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(role="DEVELOPER", warehouse="REPORTING_WH", database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION")

print("=== Acronis API point-in-time vs avg coverage by month ===")
q = """
SELECT
  billing_month,
  COUNT(*) AS row_ct,
  COUNT(api_quantity) AS api_rows,
  COUNT(avg_api_quantity) AS avg_rows,
  ROUND(100.0 * COUNT(api_quantity) / NULLIF(COUNT(*),0), 1) AS api_pct,
  ROUND(100.0 * COUNT(avg_api_quantity) / NULLIF(COUNT(*),0), 1) AS avg_pct
FROM ACRONIS_RECON_DETAIL
WHERE billing_month >= DATEADD(month,-6,DATE_TRUNC('month',CURRENT_DATE))
GROUP BY 1
ORDER BY 1 DESC
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Acronis: rows with avg present but api null (last 3 months) ===")
q = """
SELECT
  COUNT(*) AS avg_present_api_null_rows,
  ROUND(SUM(avg_api_quantity),0) AS avg_api_qty_sum
FROM ACRONIS_RECON_DETAIL
WHERE billing_month >= DATEADD(month,-3,DATE_TRUNC('month',CURRENT_DATE))
  AND avg_api_quantity IS NOT NULL
  AND api_quantity IS NULL
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Acronis raw TRT snapshot-day availability test (day 22 cycle) ===")
q = """
WITH pm AS (
  SELECT sf_id, MAX(cms_id) AS cms_id
  FROM RECON_PARTNER_MAP
  GROUP BY 1
),
keys AS (
  SELECT DISTINCT UPPER(TRIM(trt_match_key)) AS charge_sku_key,
         UPPER(TRIM(sku_match_key)) AS sku_match_group_key
  FROM RECON_SKU_MAP
  WHERE vendor='Acronis'
    AND trt_match_key IS NOT NULL
    AND TRIM(trt_match_key) <> ''
    AND sku_match_key IS NOT NULL
),
base AS (
  SELECT DISTINCT
    d.sf_id,
    d.billing_month,
    UPPER(TRIM(d.vendor_product_group)) AS sku_match_group_key,
    pm.cms_id,
    DATEADD('day', 21, d.billing_month)::DATE AS snapshot_date,
    DATEADD('day', 21, DATEADD('month', -1, d.billing_month))::DATE AS prev_snapshot_date
  FROM ACRONIS_RECON_DETAIL d
  JOIN pm ON pm.sf_id = d.sf_id
  WHERE d.billing_month >= DATEADD(month,-3,DATE_TRUNC('month',CURRENT_DATE))
),
match_days AS (
  SELECT
    b.sf_id,
    b.billing_month,
    b.sku_match_group_key,
    COUNT(DISTINCT u.on_date::DATE) AS active_days,
    MAX(IFF(u.on_date::DATE = b.snapshot_date, 1, 0)) AS has_snapshot_day
  FROM base b
  JOIN keys k
    ON k.sku_match_group_key = b.sku_match_group_key
  JOIN ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
    ON u.partner_id::VARCHAR = b.cms_id
   AND UPPER(TRIM(u.product_sku)) = k.charge_sku_key
   AND u.on_date::DATE > b.prev_snapshot_date
   AND u.on_date::DATE <= b.snapshot_date
  GROUP BY 1,2,3
)
SELECT
  COUNT(*) AS acct_month_groups_with_usage,
  COUNT_IF(has_snapshot_day=1) AS groups_with_snapshot_day,
  ROUND(100.0*COUNT_IF(has_snapshot_day=1)/NULLIF(COUNT(*),0),1) AS snapshot_day_pct,
  ROUND(AVG(active_days),1) AS avg_active_days
FROM match_days
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Vendor x partner x sku monthly quantity sanity (last 3 months) ===")
q = """
WITH u AS (
  SELECT 'Proofpoint' AS vendor, billing_month, sf_id, vendor_product, vendor_quantity, total_billing_quantity, api_quantity, avg_api_quantity
  FROM PROOFPOINT_RECON_DETAIL
  UNION ALL
  SELECT 'Acronis', billing_month, sf_id, vendor_product, vendor_quantity, total_billing_quantity, api_quantity, avg_api_quantity
  FROM ACRONIS_RECON_DETAIL
  UNION ALL
  SELECT 'SentinelOne', billing_month, sf_id, sku_match_group AS vendor_product, vendor_quantity, total_billing_quantity, api_quantity, avg_api_quantity
  FROM SENTINELONE_RECON_DETAIL
  UNION ALL
  SELECT 'Exium', billing_month, sf_id, sku_match_group AS vendor_product, vendor_quantity, total_billing_quantity, api_quantity, avg_api_quantity
  FROM EXIUM_RECON_DETAIL
),
base AS (
  SELECT *
  FROM u
  WHERE billing_month >= DATEADD(month,-3,DATE_TRUNC('month',CURRENT_DATE))
    AND sf_id IS NOT NULL
    AND vendor_quantity IS NOT NULL
    AND total_billing_quantity IS NOT NULL
)
SELECT
  vendor,
  COUNT(*) AS grain_rows,
  COUNT_IF(api_quantity IS NOT NULL) AS api_rows,
  ROUND(AVG(ABS(total_billing_quantity - vendor_quantity)),1) AS avg_abs_billed_vendor_delta,
  ROUND(MEDIAN(ABS(total_billing_quantity - vendor_quantity)),1) AS p50_abs_billed_vendor_delta,
  ROUND(AVG(IFF(api_quantity IS NULL, NULL, ABS(api_quantity - vendor_quantity))),1) AS avg_abs_api_vendor_delta,
  ROUND(MEDIAN(IFF(api_quantity IS NULL, NULL, ABS(api_quantity - vendor_quantity))),1) AS p50_abs_api_vendor_delta,
  ROUND(AVG(IFF(api_quantity IS NULL OR vendor_quantity=0, NULL, api_quantity/NULLIF(vendor_quantity,0))),3) AS avg_api_vendor_ratio,
  ROUND(AVG(IFF(avg_api_quantity IS NULL OR vendor_quantity=0, NULL, avg_api_quantity/NULLIF(vendor_quantity,0))),3) AS avg_avgapi_vendor_ratio
FROM base
GROUP BY 1
ORDER BY 1
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n=== Candidate stale TRT objects in DBT_NFOLD_TRANSFORMATION (metadata only) ===")
q = """
SELECT table_name, table_type, created, last_altered
FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
WHERE table_schema='DBT_NFOLD_TRANSFORMATION'
  AND (
    table_name ILIKE '%TRT%'
    OR table_name ILIKE '%API%BACKFILL%'
  )
ORDER BY table_name
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

conn.close()
