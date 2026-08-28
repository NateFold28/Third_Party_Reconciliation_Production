from TEMPLATES.Python.connection import fetch_dataframe

queries = {
    'seed_columns': '''
      SELECT COLUMN_NAME
      FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
      WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION' AND TABLE_NAME='ACRONIS_COMBINED_MAPPING_SEED'
      ORDER BY ORDINAL_POSITION
    ''',
    'seed_june_focus': '''
      SELECT BILLING_MONTH::DATE AS BILLING_MONTH, TENANT_NAME, SF_ID, CMS_ID, BILLING_TYPE,
             VENDOR_SKU, QUANTITY AS VENDOR_QTY, AMOUNT AS VENDOR_AMT,
             CW_SKU, CW_QUANTITY, CW_EXTENDED_PRICE,
             STATUS, MODIFIER
      FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_COMBINED_MAPPING_SEED
      WHERE BILLING_MONTH='2026-06-01'::DATE
        AND TENANT_NAME ILIKE ANY ('%Pure Technology /1%','%Virtual Communication Specialists 1%','%The Learning Exchange%','%GSG Computers, Inc.%','%SHARKTOOTH NETWORKS INC%','%DigitalBrainz%','%Total Group International Ltd.%')
      ORDER BY TENANT_NAME, SF_ID, VENDOR_SKU, CW_SKU
    '''
}
for n,q in queries.items():
  print(f"\n=== {n} ===")
  df = fetch_dataframe(q)
  print(df.to_string(index=False, max_colwidth=120) if not df.empty else '(no rows)')
