import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects')
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH',
                                database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()

print('=== BEFORE: bad Proofpoint rows to be deleted ===')
q_bad = """
SELECT vendor, vendor_product, vendor_sku, cw_sku, sku_match_key, mapping_notes
FROM THIRD_PARTY_RECON_SKU_MAP_PROD
WHERE vendor='Proofpoint' AND MAPPING_NOTES IS NOT NULL
ORDER BY vendor_product
"""
print(fetch_dataframe(q_bad, conn=conn).to_string(index=False))

n_before = fetch_dataframe(
    "SELECT COUNT(*) AS n FROM THIRD_PARTY_RECON_SKU_MAP_PROD WHERE vendor='Proofpoint'",
    conn=conn).iloc[0, 0]
print(f"\nTotal Proofpoint rows before: {n_before}")

cur.execute("DELETE FROM THIRD_PARTY_RECON_SKU_MAP_PROD WHERE vendor='Proofpoint' AND MAPPING_NOTES IS NOT NULL")
print(f"Deleted rows: {cur.rowcount}")

n_after = fetch_dataframe(
    "SELECT COUNT(*) AS n FROM THIRD_PARTY_RECON_SKU_MAP_PROD WHERE vendor='Proofpoint'",
    conn=conn).iloc[0, 0]
print(f"Proofpoint rows after delete: {n_after}")

try:
    cur.execute("ALTER TABLE THIRD_PARTY_RECON_SKU_MAP_PROD ADD COLUMN TRT_MATCH_KEY VARCHAR")
    print('Added TRT_MATCH_KEY column.')
except Exception as e:
    print(f'Column add note: {e}')

cur.execute("UPDATE THIRD_PARTY_RECON_SKU_MAP_PROD SET TRT_MATCH_KEY = CW_SKU WHERE vendor='Proofpoint'")
print(f"Populated TRT_MATCH_KEY (Proofpoint): {cur.rowcount}")

print('\n=== AFTER: sample Proofpoint rows with TRT_MATCH_KEY ===')
q_after = """
SELECT vendor, vendor_product, vendor_sku, cw_sku, trt_match_key, sku_match_key
FROM THIRD_PARTY_RECON_SKU_MAP_PROD
WHERE vendor='Proofpoint'
ORDER BY vendor_product, cw_sku
LIMIT 30
"""
print(fetch_dataframe(q_after, conn=conn).to_string(index=False))

print('\n=== column list ===')
q_cols = """
SELECT column_name FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
WHERE table_schema='DBT_NFOLD_TRANSFORMATION' AND table_name='THIRD_PARTY_RECON_SKU_MAP_PROD'
ORDER BY ordinal_position
"""
print(fetch_dataframe(q_cols, conn=conn).to_string(index=False))

conn.close()
