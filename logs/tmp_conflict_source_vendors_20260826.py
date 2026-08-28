from TEMPLATES.Python.connection import fetch_dataframe
from pathlib import Path

sql_vendor = '''
WITH conflict_keys AS (
  SELECT UPPER(TRIM(PARTNER_NAME)) AS partner_key
  FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
  WHERE PARTNER_NAME IS NOT NULL AND TRIM(PARTNER_NAME) <> ''
    AND SF_ID IS NOT NULL AND TRIM(SF_ID) <> ''
  GROUP BY 1
  HAVING COUNT(DISTINCT SF_ID) > 1
)
SELECT
  p.VENDOR,
  COUNT(*) AS map_rows_on_conflict_keys,
  COUNT(DISTINCT UPPER(TRIM(p.PARTNER_NAME))) AS conflict_partner_keys,
  COUNT(DISTINCT p.SF_ID) AS conflict_sf_ids
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_PARTNER_MAP_PROD p
JOIN conflict_keys c
  ON c.partner_key = UPPER(TRIM(p.PARTNER_NAME))
GROUP BY 1
ORDER BY conflict_partner_keys DESC, map_rows_on_conflict_keys DESC
'''

sql_keys = '''
WITH conflict AS (
  SELECT UPPER(TRIM(PARTNER_NAME)) AS partner_key,
         COUNT(DISTINCT SF_ID) AS sf_id_count,
         LISTAGG(DISTINCT SF_ID, ' | ') WITHIN GROUP (ORDER BY SF_ID) AS sf_ids
  FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
  WHERE PARTNER_NAME IS NOT NULL AND TRIM(PARTNER_NAME) <> ''
    AND SF_ID IS NOT NULL AND TRIM(SF_ID) <> ''
  GROUP BY 1
  HAVING COUNT(DISTINCT SF_ID) > 1
),
key_vendors AS (
  SELECT
    c.partner_key,
    c.sf_id_count,
    c.sf_ids,
    LISTAGG(DISTINCT p.VENDOR, ' | ') WITHIN GROUP (ORDER BY p.VENDOR) AS vendors,
    COUNT(DISTINCT p.VENDOR) AS vendor_count
  FROM conflict c
  JOIN ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_PARTNER_MAP_PROD p
    ON UPPER(TRIM(p.PARTNER_NAME)) = c.partner_key
  GROUP BY 1,2,3
)
SELECT *
FROM key_vendors
ORDER BY sf_id_count DESC, vendor_count DESC, partner_key
'''

vdf = fetch_dataframe(sql_vendor)
kdf = fetch_dataframe(sql_keys)

out1 = Path(r'c:/Users/Nate.Fold/projects/logs/conflict_source_vendor_counts_20260826.csv')
out2 = Path(r'c:/Users/Nate.Fold/projects/logs/conflict_partner_keys_with_vendors_20260826.csv')
vdf.to_csv(out1, index=False)
kdf.to_csv(out2, index=False)

print('=== conflict_source_vendor_counts ===')
print(vdf.to_string(index=False))
print(f'\nwritten={out1}')
print(f'written={out2}')
