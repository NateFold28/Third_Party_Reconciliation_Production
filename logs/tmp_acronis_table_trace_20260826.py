from TEMPLATES.Python.connection import fetch_dataframe

queries = {
    'tables_like_acronis': '''
        SELECT TABLE_NAME
        FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION' AND TABLE_NAME ILIKE 'ACRONIS%'
        ORDER BY TABLE_NAME
    ''',
    'billing_matched_columns': '''
        SELECT COLUMN_NAME
        FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION' AND TABLE_NAME='ACRONIS_BILLING_MATCHED'
        ORDER BY ORDINAL_POSITION
    ''',
    'billing_matched_june_focus': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, SF_ID, PARTNER_NAME, PRODUCT_SKU, QUANTITY, EXTENDED_PRICE, INVOICE_ID
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_BILLING_MATCHED
        WHERE BILLING_MONTH='2026-06-01'::DATE
          AND (
            PARTNER_NAME ILIKE ANY ('%Pure Technology /1%','%Keystone%','%Virtual Communication Specialists 1%','%In-Telecom%','%The Learning Exchange%','%GSG Computers, Inc.%','%SHARKTOOTH NETWORKS INC%')
            OR SF_ID IN ('ACT-00151751','ACT-00048610','ACT-00103890','ACT-00246010','ACT-00246692','ACT-00239688','ACT-00240157','ACT-00153605','ACT-00055050','ACT-00099576')
          )
        ORDER BY SF_ID, PARTNER_NAME, PRODUCT_SKU
    '''
}

for n,q in queries.items():
    print(f"\n=== {n} ===")
    try:
        df = fetch_dataframe(q)
        print(df.to_string(index=False, max_colwidth=120) if not df.empty else '(no rows)')
    except Exception as e:
        print(f'ERROR: {e}')
