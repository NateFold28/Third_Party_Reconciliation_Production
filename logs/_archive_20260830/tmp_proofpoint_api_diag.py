import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects')
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH',
                                database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')

print('=== 1) distinct sku_match_group values in PROOFPOINT_RECON_DETAIL ===')
q = """
SELECT vendor_product, COUNT(*) n, COUNT(api_quantity) api_nn
FROM PROOFPOINT_RECON_DETAIL
WHERE billing_month = '2026-05-01'
GROUP BY 1 ORDER BY 2 DESC
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print('\n=== 2) RECON_SKU_MAP Proofpoint sku_match_key distinct values ===')
q2 = """
SELECT sku_match_key, COUNT(*) n
FROM RECON_SKU_MAP WHERE vendor='Proofpoint'
GROUP BY 1 ORDER BY 1
"""
print(fetch_dataframe(q2, conn=conn).to_string(index=False))

print('\n=== 3) how many recon rows have a matching cms_id via RECON_PARTNER_MAP? ===')
q3 = """
SELECT COUNT(*) recon_rows,
       COUNT(DISTINCT d.sf_id) recon_sf_ids,
       COUNT(DISTINCT pm.cms_id) matched_cms_ids
FROM PROOFPOINT_RECON_DETAIL d
LEFT JOIN RECON_PARTNER_MAP pm ON pm.sf_id = d.sf_id
WHERE d.billing_month = '2026-05-01'
"""
print(fetch_dataframe(q3, conn=conn).to_string(index=False))

print('\n=== 4) direct check: raw TRT hits joining by cms_id + product_sku for May 2026 ===')
q4 = """
WITH keys AS (
    SELECT DISTINCT UPPER(TRIM(trt_match_key)) AS product_sku_key,
                    UPPER(TRIM(sku_match_key)) AS sku_match_group
    FROM RECON_SKU_MAP WHERE vendor='Proofpoint' AND trt_match_key IS NOT NULL
),
partners AS (
    SELECT DISTINCT d.sf_id, d.billing_month, d.vendor_product AS sku_match_group_raw,
                    pm.cms_id
    FROM PROOFPOINT_RECON_DETAIL d
    JOIN RECON_PARTNER_MAP pm ON pm.sf_id = d.sf_id
    WHERE d.billing_month='2026-05-01' AND pm.cms_id IS NOT NULL
)
SELECT
    COUNT(DISTINCT (p.sf_id || '|' || p.sku_match_group_raw))
        AS candidate_partner_product,
    COUNT(DISTINCT (p.sf_id || '|' || p.sku_match_group_raw))
        FILTER (WHERE u.partner_id IS NOT NULL) AS partner_product_with_trt_hit
FROM partners p
LEFT JOIN keys k
   ON k.sku_match_group = UPPER(TRIM(p.sku_match_group_raw))
LEFT JOIN ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
   ON u.partner_id::VARCHAR = p.cms_id
  AND UPPER(TRIM(u.product_sku)) = k.product_sku_key
  AND u.on_date::DATE >  DATEADD('day', 20, DATEADD('month', -1, p.billing_month))::DATE
  AND u.on_date::DATE <= DATEADD('day', 20, p.billing_month)::DATE
"""
try:
    print(fetch_dataframe(q4, conn=conn).to_string(index=False))
except Exception as e:
    print(f'ERR: {e}')

print('\n=== 5) simpler check: how many Proofpoint recon sf_ids have ANY raw TRT hit in May cycle? ===')
q5 = """
WITH keys AS (
    SELECT DISTINCT UPPER(TRIM(trt_match_key)) AS product_sku_key
    FROM RECON_SKU_MAP WHERE vendor='Proofpoint' AND trt_match_key IS NOT NULL
)
SELECT COUNT(DISTINCT d.sf_id) recon_sf_ids_may,
       COUNT(DISTINCT pm.cms_id) mapped_cms,
       COUNT(DISTINCT u.partner_id) trt_hit_partners
FROM PROOFPOINT_RECON_DETAIL d
LEFT JOIN RECON_PARTNER_MAP pm ON pm.sf_id = d.sf_id
LEFT JOIN ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
       ON u.partner_id::VARCHAR = pm.cms_id
      AND UPPER(TRIM(u.product_sku)) IN (SELECT product_sku_key FROM keys)
      AND u.on_date::DATE > '2026-04-20' AND u.on_date::DATE <= '2026-05-20'
WHERE d.billing_month='2026-05-01'
"""
print(fetch_dataframe(q5, conn=conn).to_string(index=False))

print('\n=== 6) proofpoint_int-style: does vendor_product in recon match sku_match_key? ===')
q6 = """
SELECT d.vendor_product,
       COUNT(*) n_recon,
       COUNT(DISTINCT m.cw_sku) n_map_matches
FROM PROOFPOINT_RECON_DETAIL d
LEFT JOIN RECON_SKU_MAP m
       ON m.vendor='Proofpoint'
      AND UPPER(TRIM(m.sku_match_key)) = UPPER(TRIM(d.vendor_product))
WHERE d.billing_month='2026-05-01'
GROUP BY 1 ORDER BY n_recon DESC LIMIT 25
"""
print(fetch_dataframe(q6, conn=conn).to_string(index=False))

conn.close()
