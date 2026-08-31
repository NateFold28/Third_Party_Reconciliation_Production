"""Delete 13 orphan Acronis rows from THIRD_PARTY_RECON_SKU_MAP_PROD.
These rows have NULL CW_SKU and are unused by any recon join. They're
documentation-only vendor SKUs that appear in ACRONIS_COMBINED_MAPPING_SEED
already, so the SKU_MAP entries are redundant."""
import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects')
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH',
                                database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()

VENDOR_SKUS = ['SBDAMSENS','SBDBMSENS','SBDCMSENS','SBDDMSENS','SESDMSENS',
               'SH1AMSENS','SHAAMSENS','SHJAMSENS','SPSAMSENS',
               'SRDUMSENS','SREUMSENS','SRFUMSENS','SUPDMSENS']
QUOTED = ','.join(repr(k) for k in VENDOR_SKUS)

print('== Before ==')
r = cur.execute(f"""
SELECT COUNT(*) AS matched_rows,
       COUNT(cw_sku) AS with_cw_sku
FROM THIRD_PARTY_RECON_SKU_MAP_PROD
WHERE vendor='Acronis' AND UPPER(TRIM(vendor_sku)) IN ({QUOTED})
""").fetchone()
print(f'  matched={r[0]}, with_cw_sku={r[1]}  (should be 13 matched, 0 with_cw_sku)')

# Only delete rows that are truly orphaned — belt-and-suspenders WHERE clause.
cur.execute(f"""
DELETE FROM THIRD_PARTY_RECON_SKU_MAP_PROD
WHERE vendor='Acronis'
  AND UPPER(TRIM(vendor_sku)) IN ({QUOTED})
  AND (cw_sku IS NULL OR TRIM(cw_sku) = '')
""")
print(f'  deleted rows: {cur.rowcount}')

print('\n== After ==')
r = cur.execute("""
SELECT COUNT(*) AS acronis_rows,
       COUNT(cw_sku) AS with_cw_sku,
       COUNT(DISTINCT sku_match_key) AS distinct_smk
FROM THIRD_PARTY_RECON_SKU_MAP_PROD WHERE vendor='Acronis'
""").fetchone()
print(f'  acronis_rows={r[0]}, with_cw_sku={r[1]}, distinct SKU_MATCH_KEYs={r[2]}')

conn.commit()
conn.close()
