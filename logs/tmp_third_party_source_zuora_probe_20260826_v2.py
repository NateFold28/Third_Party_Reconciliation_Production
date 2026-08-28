from TEMPLATES.Python.connection import fetch_dataframe

queries = {
  'zuora_source_june_names': '''
    SELECT BILLING_MONTH::DATE AS BILLING_MONTH, VENDOR, SF_ID, ZUORA_ACCOUNT_NAME, PRODUCT_SKU, QTY, CHARGE_AMOUNT_USD, INVOICE_NUMBER
    FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
    WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
      AND (
        ZUORA_ACCOUNT_NAME ILIKE ANY ('%Pure Technology /1%','%Virtual Communication Specialists 1%','%The Learning Exchange%','%GSG Computers, Inc.%','%SHARKTOOTH NETWORKS INC%','%In-Telecom%','%Keystone%')
        OR SF_ID IN ('ACT-00151751','ACT-00246010','ACT-00239688','ACT-00240157','ACT-00153605','ACT-00246692','ACT-00048610','ACT-00103890')
      )
    ORDER BY ZUORA_ACCOUNT_NAME, SF_ID, PRODUCT_SKU
  ''',
  'marketplace_source_june_names': '''
    SELECT BILLING_MONTH::DATE AS BILLING_MONTH, VENDOR, SF_ID, MARKETPLACE_PARTNER_NAME, PRODUCT_SKU, QUANTITY, EXTENDED_PRICE_USD
    FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
    WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
      AND (
        MARKETPLACE_PARTNER_NAME ILIKE ANY ('%Pure Technology /1%','%Virtual Communication Specialists 1%','%The Learning Exchange%','%GSG Computers, Inc.%','%SHARKTOOTH NETWORKS INC%','%In-Telecom%','%Keystone%')
        OR SF_ID IN ('ACT-00151751','ACT-00246010','ACT-00239688','ACT-00240157','ACT-00153605','ACT-00246692','ACT-00048610','ACT-00103890')
      )
    ORDER BY MARKETPLACE_PARTNER_NAME, SF_ID, PRODUCT_SKU
  ''',
  'marketplace_source_cols': '''
    SELECT COLUMN_NAME
    FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION' AND TABLE_NAME='THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD'
    ORDER BY ORDINAL_POSITION
  '''
}
for n,q in queries.items():
  print(f"\n=== {n} ===")
  try:
    df = fetch_dataframe(q)
    print(df.to_string(index=False, max_colwidth=120) if not df.empty else '(no rows)')
  except Exception as e:
    print('ERROR:', e)
