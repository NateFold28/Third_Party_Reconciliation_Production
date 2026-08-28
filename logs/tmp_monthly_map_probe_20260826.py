from TEMPLATES.Python.connection import fetch_dataframe

q = '''
SELECT BILLING_MONTH::DATE AS BILLING_MONTH, PARTNER_NAME, SF_ID, RAW_SF_ID, SF_ID_SOURCE, CMS_ID
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP_MONTHLY
WHERE PARTNER_NAME ILIKE ANY (
  '%system go%','%securetech%','%bulletproof infotech%','%bulletproof systems%','%rx technology%','%serit ostereng it%','%origami technology group%'
)
  AND BILLING_MONTH BETWEEN '2026-01-01'::DATE AND '2026-08-01'::DATE
ORDER BY PARTNER_NAME, BILLING_MONTH, SF_ID
'''

df = fetch_dataframe(q)
print(df.to_string(index=False, max_colwidth=120) if not df.empty else '(no rows)')
