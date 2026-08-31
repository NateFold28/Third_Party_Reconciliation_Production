from TEMPLATES.Python.connection import fetch_dataframe

pat = "('%Pure Technology /1%','%Keystone%','%Virtual Communication Specialists 1%','%In-Telecom%','%The Learning Exchange%','%GSG Computers, Inc.%','%SHARKTOOTH NETWORKS INC%','%Total Group International Ltd.%','%DigitalBrainz%','%Strong Conne%')"

queries = {
    'detail_june_focus': f'''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, VENDOR_PRODUCT,
               BILLING_SOURCE_MIX, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_DETAIL_PROD
        WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
          AND VENDOR_PARTNER_NAME ILIKE ANY {pat}
        ORDER BY VENDOR_PARTNER_NAME, SF_ID, VENDOR_PRODUCT
    ''',
    'output_june_focus': f'''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, VENDOR_PRODUCT,
               EXCEPTION_TYPE, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
          AND VENDOR_PARTNER_NAME ILIKE ANY {pat}
        ORDER BY VENDOR_PARTNER_NAME, SF_ID, VENDOR_PRODUCT
    ''',
    'usage_june_focus': f'''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU, MODIFIER,
               SUM(QUANTITY) AS VQTY, SUM(AMOUNT) AS VAMT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD
        WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
          AND VENDOR_PARTNER_NAME ILIKE ANY {pat}
        GROUP BY 1,2,3,4
        ORDER BY VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU
    ''',
    'zuora_resolved_june_for_sfids': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, SF_ID, SKU_MATCH_GROUP, ZUORA_QUANTITY, ZUORA_AMOUNT, ZUORA_SKUS
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_ZUORA_RESOLVED
        WHERE BILLING_MONTH='2026-06-01'::DATE
          AND SF_ID IN (
              'ACT-00151751','ACT-00048610','ACT-00103890','ACT-00246010','ACT-00246692',
              'ACT-00239688','ACT-00240157','ACT-00153605','ACT-00055050','ACT-00099576'
          )
        ORDER BY SF_ID, SKU_MATCH_GROUP
    ''',
    'partner_map_focus': '''
        SELECT PARTNER_NAME, SF_ID, RAW_SF_ID, SF_ID_SOURCE, CMS_ID
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
        WHERE PARTNER_NAME ILIKE ANY (
            '%Pure Technology /1%','%Keystone%','%Virtual Communication Specialists 1%','%In-Telecom%',
            '%The Learning Exchange%','%GSG Computers, Inc.%','%SHARKTOOTH NETWORKS INC%',
            '%Total Group International Ltd.%','%DigitalBrainz%','%Strong Conne%'
        )
        ORDER BY PARTNER_NAME, SF_ID
    '''
}

for n, q in queries.items():
    print(f"\n=== {n} ===")
    df = fetch_dataframe(q)
    print(df.to_string(index=False, max_colwidth=120) if not df.empty else '(no rows)')
