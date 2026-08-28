from TEMPLATES.Python.connection import fetch_dataframe

q2 = '''
SELECT EXCEPTION_TYPE,
       COUNT(*) AS ROW_CNT,
       ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA,0))),2) AS ABS_DELTA
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
  AND VENDOR_PARTNER_NAME ILIKE ANY (
    '%Pure Technology /1%','%Keystone%','%Virtual Communication Specialists 1%','%In-Telecom%',
    '%The Learning Exchange%','%GSG Computers, Inc.%','%SHARKTOOTH NETWORKS INC%',
    '%Total Group International Ltd.%','%DigitalBrainz%','%Strong Conne%'
  )
GROUP BY 1
ORDER BY ROW_CNT DESC
'''

q3 = '''
SELECT EXCEPTION_TYPE, COUNT(*) AS ROW_CNT, ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA,0))),2) AS ABS_DELTA
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
GROUP BY 1
ORDER BY ROW_CNT DESC
'''

print('=== case_bucket_summary ===')
print(fetch_dataframe(q2).to_string(index=False, max_colwidth=120))
print('\n=== acronis_june_bucket_summary ===')
print(fetch_dataframe(q3).to_string(index=False, max_colwidth=120))
