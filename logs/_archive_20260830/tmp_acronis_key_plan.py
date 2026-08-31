import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects')
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH',
                                database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')

print('=== Distinct charge_sku suffix patterns (Acronis, last 6 months) ===')
q1 = """
SELECT REGEXP_SUBSTR(charge_sku, '-[0-9]+$') AS suffix, COUNT(*) AS n_rows
FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
WHERE product_description ILIKE '%acronis%'
  AND on_date >= '2026-01-01' AND on_date < '2026-08-01'
  AND charge_sku IS NOT NULL
GROUP BY 1
ORDER BY 2 DESC
"""
print(fetch_dataframe(q1, conn=conn).to_string(index=False))

print('\n=== SKU map: distinct CW_SKU values matching APP-BC-XXX or APP-SA-XXX pattern ===')
q2 = """
SELECT DISTINCT cw_sku, sku_match_key
FROM THIRD_PARTY_RECON_SKU_MAP_PROD
WHERE vendor='Acronis'
  AND cw_sku RLIKE '^APP-(BC|SA)-[A-Z0-9]{9}$'
ORDER BY cw_sku
"""
print(fetch_dataframe(q2, conn=conn).to_string(index=False))

print('\n=== SKU map: SKU_MATCH_KEYs having NO simple APP-BC / APP-SA CW_SKU row ===')
q3 = """
WITH have_app AS (
  SELECT DISTINCT sku_match_key
  FROM THIRD_PARTY_RECON_SKU_MAP_PROD
  WHERE vendor='Acronis' AND cw_sku RLIKE '^APP-(BC|SA)-[A-Z0-9]{9}$'
), all_smk AS (
  SELECT DISTINCT sku_match_key
  FROM THIRD_PARTY_RECON_SKU_MAP_PROD WHERE vendor='Acronis'
)
SELECT all_smk.sku_match_key FROM all_smk
LEFT JOIN have_app USING (sku_match_key)
WHERE have_app.sku_match_key IS NULL
ORDER BY 1
"""
print(fetch_dataframe(q3, conn=conn).to_string(index=False))

print('\n=== Raw TRT: distinct base sku present in charge_sku (last 6 mo, Acronis) ===')
q4 = """
SELECT DISTINCT REGEXP_SUBSTR(charge_sku, '^APP-(BC|SA)-([A-Z0-9]{9})-[0-9]+$', 1, 1, 'e', 2) AS base_sku,
       COUNT(DISTINCT charge_sku) AS n_charge_variants,
       SUM(agent_cnt) AS total_agent_cnt
FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
WHERE product_description ILIKE '%acronis%'
  AND on_date >= '2026-01-01' AND on_date < '2026-08-01'
  AND charge_sku IS NOT NULL
GROUP BY 1
ORDER BY 3 DESC NULLS LAST
"""
print(fetch_dataframe(q4, conn=conn).to_string(index=False))

conn.close()
