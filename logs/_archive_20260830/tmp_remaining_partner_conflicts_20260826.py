from TEMPLATES.Python.connection import fetch_dataframe
from pathlib import Path

sql = '''
WITH base AS (
  SELECT
    UPPER(TRIM(m.PARTNER_NAME)) AS partner_key,
    m.SF_ID AS sf_id,
    COALESCE(r.canonical_sf_id, m.SF_ID) AS canonical_sf_id
  FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP m
  LEFT JOIN ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_ACCOUNT_MERGE_RESOLVER r
    ON r.old_sf_id = m.SF_ID
  WHERE m.PARTNER_NAME IS NOT NULL AND TRIM(m.PARTNER_NAME) <> ''
    AND m.SF_ID IS NOT NULL AND TRIM(m.SF_ID) <> ''
),
conflicts AS (
  SELECT
    partner_key,
    COUNT(DISTINCT sf_id) AS sf_id_count,
    COUNT(DISTINCT canonical_sf_id) AS canonical_sf_id_count,
    LISTAGG(DISTINCT sf_id, ' | ') WITHIN GROUP (ORDER BY sf_id) AS sf_ids,
    LISTAGG(DISTINCT canonical_sf_id, ' | ') WITHIN GROUP (ORDER BY canonical_sf_id) AS canonical_sf_ids
  FROM base
  GROUP BY 1
  HAVING COUNT(DISTINCT sf_id) > 1
)
SELECT
  partner_key,
  sf_id_count,
  canonical_sf_id_count,
  IFF(canonical_sf_id_count = 1, 'AUTO_RESOLVABLE_BY_MERGE', 'MANUAL_REVIEW_REQUIRED') AS resolution_class,
  sf_ids,
  canonical_sf_ids
FROM conflicts
ORDER BY sf_id_count DESC, partner_key
'''

df = fetch_dataframe(sql)
out = Path(r'c:/Users/Nate.Fold/projects/logs/remaining_partner_multi_sf_after_fix_20260826.csv')
df.to_csv(out, index=False)
print(f"rows={len(df)} written={out}")
print(df.groupby('RESOLUTION_CLASS').size().to_string())
