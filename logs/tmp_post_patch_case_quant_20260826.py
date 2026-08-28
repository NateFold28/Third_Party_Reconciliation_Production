from TEMPLATES.Python.connection import fetch_dataframe

q1 = '''
SELECT VENDOR_PARTNER_NAME, SF_ID, VENDOR_PRODUCT, EXCEPTION_TYPE, OUTCOME_FLAG,
       VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
  AND VENDOR_PARTNER_NAME ILIKE ANY (
    '%Pure Technology /1%','%Keystone%','%Virtual Communication Specialists 1%','%In-Telecom%',
    '%The Learning Exchange%','%GSG Computers, Inc.%','%SHARKTOOTH NETWORKS INC%',
    '%Total Group International Ltd.%','%DigitalBrainz%','%Strong Conne%'
  )
ORDER BY VENDOR_PARTNER_NAME, SF_ID, VENDOR_PRODUCT
'''

q2 = '''
SELECT EXCEPTION_TYPE,
       COUNT(*) AS ROWS,
       ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA,0))),2) AS ABS_DELTA
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
  AND VENDOR_PARTNER_NAME ILIKE ANY (
    '%Pure Technology /1%','%Keystone%','%Virtual Communication Specialists 1%','%In-Telecom%',
    '%The Learning Exchange%','%GSG Computers, Inc.%','%SHARKTOOTH NETWORKS INC%',
    '%Total Group International Ltd.%','%DigitalBrainz%','%Strong Conne%'
  )
GROUP BY 1
ORDER BY ROWS DESC
'''

q3 = '''
SELECT EXCEPTION_TYPE, COUNT(*) AS ROWS, ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA,0))),2) AS ABS_DELTA
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
GROUP BY 1
ORDER BY ROWS DESC
'''

for name,q in [('case_rows',q1),('case_bucket_summary',q2),('acronis_june_bucket_summary',q3)]:
    print(f"\n=== {name} ===")
    df = fetch_dataframe(q)
    print(df.to_string(index=False, max_colwidth=120) if not df.empty else '(no rows)')
