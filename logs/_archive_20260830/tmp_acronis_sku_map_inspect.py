import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects')
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH',
                                database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')

print('=== Acronis rows in SKU map (current TRT_MATCH_KEY state) ===')
q1 = """
SELECT vendor, vendor_product, vendor_sku, cw_sku, sku_match_key, trt_match_key, contract_cost_rate
FROM THIRD_PARTY_RECON_SKU_MAP_PROD
WHERE vendor='Acronis'
ORDER BY vendor_product, vendor_sku
"""
df = fetch_dataframe(q1, conn=conn)
print(df.to_string(index=False))

print(f'\ntotal rows: {len(df)}')
print(f'rows with TRT_MATCH_KEY populated: {df["TRT_MATCH_KEY"].notna().sum() if "TRT_MATCH_KEY" in df.columns else "?"}')

print('\n=== Distinct CHARGE_SKU values in raw TRT (May 2026) — Acronis usage rows ===')
q2 = """
SELECT DISTINCT charge_sku, product_sku, product_description, COUNT(*) AS n_rows
FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
WHERE product_description ILIKE '%acronis%'
  AND on_date >= '2026-05-01' AND on_date < '2026-06-01'
GROUP BY 1,2,3
ORDER BY n_rows DESC
"""
print(fetch_dataframe(q2, conn=conn).to_string(index=False))

print('\n=== Distinct CHARGE_SKU where product_sku starts with BB-ACRONIS (May 2026) ===')
q3 = """
SELECT DISTINCT charge_sku, product_sku, COUNT(*) AS n_rows, SUM(agent_cnt) AS total_seats
FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
WHERE product_sku ILIKE 'BB-ACRONIS%'
  AND on_date >= '2026-05-01' AND on_date < '2026-06-01'
GROUP BY 1,2
ORDER BY total_seats DESC NULLS LAST
"""
print(fetch_dataframe(q3, conn=conn).to_string(index=False))

conn.close()
