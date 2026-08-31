from TEMPLATES.Python.connection import fetch_dataframe

queries = {
    'marketplace_columns': '''
      SELECT COLUMN_NAME
      FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
      WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION' AND TABLE_NAME='ACRONIS_MARKETPLACE_BILLING_MATCHED'
      ORDER BY ORDINAL_POSITION
    ''',
    'marketplace_june_focus': '''
      SELECT *
      FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_MARKETPLACE_BILLING_MATCHED
      WHERE BILLING_MONTH='2026-06-01'::DATE
        AND (
          SF_ID IN ('ACT-00151751','ACT-00246010','ACT-00239688','ACT-00240157','ACT-00153605')
          OR CW_ACCOUNT_NAME ILIKE ANY ('%Pure Technology /1%','%Virtual Communication Specialists 1%','%The Learning Exchange%','%GSG Computers, Inc.%','%SHARKTOOTH NETWORKS INC%')
        )
      ORDER BY SF_ID
    '''
}

for n, q in queries.items():
  print(f"\n=== {n} ===")
  df = fetch_dataframe(q)
  print(df.to_string(index=False, max_colwidth=120) if not df.empty else '(no rows)')
