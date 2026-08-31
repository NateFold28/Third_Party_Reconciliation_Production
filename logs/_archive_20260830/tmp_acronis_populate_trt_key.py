import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects')
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH',
                                database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()

print('== Before ==')
r = cur.execute("""
SELECT COUNT(*) AS acronis_rows,
       COUNT(trt_match_key) AS acronis_with_trt_key
FROM THIRD_PARTY_RECON_SKU_MAP_PROD WHERE vendor='Acronis'
""").fetchone()
print(f'  acronis_rows={r[0]}, populated={r[1]}')

# Populate TRT_MATCH_KEY = CW_SKU || '-001' where CW_SKU matches APP-(BC|SA)-<9char> pattern.
cur.execute("""
UPDATE THIRD_PARTY_RECON_SKU_MAP_PROD
SET trt_match_key = cw_sku || '-001'
WHERE vendor='Acronis'
  AND cw_sku RLIKE '^APP-(BC|SA)-[A-Z0-9]{9}$'
""")
print(f'  update rows: {cur.rowcount}')

print('\n== After ==')
r = cur.execute("""
SELECT COUNT(*) AS acronis_rows,
       COUNT(trt_match_key) AS acronis_with_trt_key,
       COUNT(DISTINCT sku_match_key) AS distinct_smk,
       COUNT(DISTINCT CASE WHEN trt_match_key IS NOT NULL THEN sku_match_key END) AS distinct_smk_with_trt
FROM THIRD_PARTY_RECON_SKU_MAP_PROD WHERE vendor='Acronis'
""").fetchone()
print(f'  acronis_rows={r[0]}, populated={r[1]}, distinct SKU_MATCH_KEYs={r[2]}, distinct SMKs with TRT_MATCH_KEY={r[3]}')

print('\n== SKU_MATCH_KEYs still without a TRT_MATCH_KEY (informational) ==')
df = fetch_dataframe("""
SELECT DISTINCT sku_match_key
FROM THIRD_PARTY_RECON_SKU_MAP_PROD
WHERE vendor='Acronis' AND sku_match_key NOT IN (
    SELECT DISTINCT sku_match_key
    FROM THIRD_PARTY_RECON_SKU_MAP_PROD
    WHERE vendor='Acronis' AND trt_match_key IS NOT NULL
)
ORDER BY 1
""", conn=conn)
print(df.to_string(index=False))

print('\n== Sample populated rows ==')
df = fetch_dataframe("""
SELECT sku_match_key, cw_sku, trt_match_key
FROM THIRD_PARTY_RECON_SKU_MAP_PROD
WHERE vendor='Acronis' AND trt_match_key IS NOT NULL
ORDER BY sku_match_key, cw_sku LIMIT 20
""", conn=conn)
print(df.to_string(index=False))

conn.commit()
conn.close()
