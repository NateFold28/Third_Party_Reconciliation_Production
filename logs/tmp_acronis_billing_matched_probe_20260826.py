from TEMPLATES.Python.connection import fetch_dataframe

queries = {
    'billing_matched_june_sfids': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, SF_ID, CW_ACCOUNT_NAME, PRODUCT_SKU,
               ZUORA_QUANTITY, ZUORA_CHARGE_AMOUNT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_BILLING_MATCHED
        WHERE BILLING_MONTH='2026-06-01'::DATE
          AND SF_ID IN ('ACT-00151751','ACT-00048610','ACT-00103890','ACT-00246010','ACT-00246692','ACT-00239688','ACT-00240157','ACT-00153605','ACT-00055050','ACT-00099576')
        ORDER BY SF_ID, PRODUCT_SKU
    ''',
    'billing_matched_june_name_like': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, SF_ID, CW_ACCOUNT_NAME, PRODUCT_SKU,
               ZUORA_QUANTITY, ZUORA_CHARGE_AMOUNT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_BILLING_MATCHED
        WHERE BILLING_MONTH='2026-06-01'::DATE
          AND CW_ACCOUNT_NAME ILIKE ANY ('%Pure Technology /1%','%Keystone%','%Virtual Communication Specialists 1%','%In-Telecom%','%The Learning Exchange%','%GSG Computers, Inc.%','%SHARKTOOTH NETWORKS INC%')
        ORDER BY CW_ACCOUNT_NAME, SF_ID, PRODUCT_SKU
    ''',
    'zuora_resolved_june_name_like': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, SF_ID, SKU_MATCH_GROUP, ZUORA_QUANTITY, ZUORA_AMOUNT, ZUORA_SKUS
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_ZUORA_RESOLVED
        WHERE BILLING_MONTH='2026-06-01'::DATE
          AND SF_ID IN (SELECT DISTINCT SF_ID FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_BILLING_MATCHED WHERE BILLING_MONTH='2026-06-01'::DATE AND CW_ACCOUNT_NAME ILIKE ANY ('%Pure Technology /1%','%Keystone%','%Virtual Communication Specialists 1%','%In-Telecom%','%The Learning Exchange%','%GSG Computers, Inc.%','%SHARKTOOTH NETWORKS INC%'))
        ORDER BY SF_ID, SKU_MATCH_GROUP
    '''
}

for n,q in queries.items():
    print(f"\n=== {n} ===")
    df = fetch_dataframe(q)
    print(df.to_string(index=False, max_colwidth=120) if not df.empty else '(no rows)')
