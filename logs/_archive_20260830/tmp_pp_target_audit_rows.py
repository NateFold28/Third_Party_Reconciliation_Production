from pathlib import Path
import sys
from textwrap import dedent
sys.path.insert(0, str(Path.cwd()))
from TEMPLATES.Python.connection import fetch_dataframe
partners = [
    'Applied Network Solutions','BluWave','Baleehoo Media Inc','MHIT Automatisering',
    'Summit Digital Networks','TRITECH CORPORATION AMERICA','Jeff Computers',
    'Engineered Medical Systems, Inc','Windsor Telecom','Alt Gr SA'
]
q = dedent(f"""
SELECT BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, VENDOR_PRODUCT,
       CW_SKUS, ZUORA_SKUS, MARKETPLACE_SKUS, BILLING_SOURCE_MIX,
       VENDOR_QUANTITY, API_QUANTITY, TOTAL_BILLING_QUANTITY,
       VENDOR_AMOUNT, TOTAL_BILLING_AMOUNT,
       OUTCOME_FLAG, EXCEPTION_TYPE, INVESTIGATION_REASON
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
WHERE VENDOR='Proofpoint'
  AND BILLING_MONTH IN ('2026-05-01','2026-06-01')
  AND (
        UPPER(COALESCE(VENDOR_PARTNER_NAME,'')) IN ({','.join("'"+p.upper().replace("'","''")+"'" for p in partners)})
        OR SF_ID IN ('ACT-00189434','ACT-00119428','ACT-00298323','ACT-00232862','ACT-00212035','ACT-00039364','ACT-00004890','ACT-00144238','ACT-00107433','ACT-00136789')
      )
ORDER BY BILLING_MONTH, SF_ID, VENDOR_PRODUCT
""")
df = fetch_dataframe(q)
print(df.to_string(index=False))
print('\nROW_COUNT', len(df))
