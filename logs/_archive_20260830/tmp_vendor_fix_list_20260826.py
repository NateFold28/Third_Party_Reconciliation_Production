from TEMPLATES.Python.connection import fetch_dataframe
from pathlib import Path

sql = '''
WITH conflict_partner_keys AS (
  SELECT UPPER(TRIM(PARTNER_NAME)) AS partner_key
  FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
  WHERE PARTNER_NAME IS NOT NULL AND TRIM(PARTNER_NAME) <> ''
    AND SF_ID IS NOT NULL AND TRIM(SF_ID) <> ''
  GROUP BY 1
  HAVING COUNT(DISTINCT SF_ID) > 1
),
output_rows AS (
  SELECT
    VENDOR,
    EXCEPTION_TYPE,
    OUTCOME_FLAG,
    SF_ID,
    COALESCE(VENDOR_PARTNER_NAME, '') AS vendor_partner_name,
    UPPER(TRIM(COALESCE(VENDOR_PARTNER_NAME, ''))) AS partner_key,
    ABS(COALESCE(AMOUNT_DELTA,0)) AS abs_amt_delta,
    ABS(COALESCE(QTY_DELTA,0)) AS abs_qty_delta
  FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
  WHERE BILLING_MONTH >= '2026-01-01'
)
SELECT
  o.VENDOR,
  COUNT(*) AS impacted_rows,
  COUNT(DISTINCT o.partner_key) AS impacted_partner_keys,
  COUNT(DISTINCT o.SF_ID) AS impacted_sf_ids,
  SUM(o.abs_amt_delta) AS abs_amt_delta,
  SUM(o.abs_qty_delta) AS abs_qty_delta,
  COUNT_IF(o.EXCEPTION_TYPE = 'Unmapped Partner') AS unmapped_partner_rows,
  COUNT_IF(o.EXCEPTION_TYPE = 'Vendor Billing, No CW Billing') AS vendor_no_cw_rows,
  COUNT_IF(o.EXCEPTION_TYPE = 'Vendor Billing, Insufficient CW Billing') AS vendor_insuff_rows,
  COUNT_IF(o.EXCEPTION_TYPE = 'CW Billing, No Vendor Billing') AS cw_no_vendor_rows
FROM output_rows o
JOIN conflict_partner_keys c
  ON c.partner_key = o.partner_key
GROUP BY 1
ORDER BY impacted_rows DESC, abs_amt_delta DESC
'''

df = fetch_dataframe(sql)
out = Path(r'c:/Users/Nate.Fold/projects/logs/vendor_fix_priority_from_conflicts_20260826.csv')
df.to_csv(out, index=False)
print(df.to_string(index=False))
print(f"\nwritten={out}")
