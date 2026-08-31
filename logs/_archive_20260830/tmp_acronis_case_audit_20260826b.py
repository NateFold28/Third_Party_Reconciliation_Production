from TEMPLATES.Python.connection import fetch_dataframe

ids = "'ACT-00246798','ACT-00150133','ACT-00239634','ACT-00238000','ACT-00287953','ACT-00151761','ACT-00031023','ACT-00069994','ACT-00098437'"

queries = {
    'seed_rows_for_ids': f'''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, TENANT_NAME, SF_ID, CMS_ID, BILLING_TYPE, VENDOR_SKU, CW_SKU
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_COMBINED_MAPPING_SEED
        WHERE SF_ID IN ({ids})
           OR TENANT_NAME ILIKE ANY ('%Rx Technology%','%Bulletproof%','%Serit Ostereng IT%','%SecureTech%','%Origami Technology Group%','%AlphaKOR%','%System Go%','%Palmetto%','%Flexxa%')
        ORDER BY BILLING_MONTH, TENANT_NAME, SF_ID, VENDOR_SKU
    ''',
    'detail_rows_for_ids_jun': f'''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, VENDOR_PRODUCT,
               BILLING_SOURCE_MIX, EXCEPTION_TYPE, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_DETAIL_PROD
        WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
          AND SF_ID IN ({ids})
        ORDER BY SF_ID, VENDOR_PRODUCT
    ''',
    'detail_rows_problem_names_jun': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, VENDOR_PRODUCT,
               BILLING_SOURCE_MIX, EXCEPTION_TYPE, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_DETAIL_PROD
        WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
          AND (
            VENDOR_PARTNER_NAME ILIKE '%Rx Technology%'
            OR VENDOR_PARTNER_NAME ILIKE '%Bulletproof%'
            OR VENDOR_PARTNER_NAME ILIKE '%Serit Ostereng IT%'
            OR VENDOR_PARTNER_NAME ILIKE '%SecureTech%'
            OR VENDOR_PARTNER_NAME ILIKE '%Origami Technology Group%'
            OR VENDOR_PARTNER_NAME ILIKE '%AlphaKOR%'
            OR VENDOR_PARTNER_NAME ILIKE '%Flexxa%'
          )
        ORDER BY VENDOR_PARTNER_NAME, SF_ID, VENDOR_PRODUCT
    ''',
    'acronis_usage_problem_names_jun': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU,
               SUM(QUANTITY) AS QTY, SUM(AMOUNT) AS AMT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_USAGE
        WHERE BILLING_MONTH='2026-06-01'::DATE
          AND (
            VENDOR_PARTNER_NAME ILIKE '%Rx Technology%'
            OR VENDOR_PARTNER_NAME ILIKE '%Bulletproof%'
            OR VENDOR_PARTNER_NAME ILIKE '%Serit Ostereng IT%'
            OR VENDOR_PARTNER_NAME ILIKE '%SecureTech%'
            OR VENDOR_PARTNER_NAME ILIKE '%Origami Technology Group%'
            OR VENDOR_PARTNER_NAME ILIKE '%AlphaKOR%'
            OR VENDOR_PARTNER_NAME ILIKE '%Flexxa%'
          )
        GROUP BY 1,2,3
        ORDER BY VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU
    ''',
    'partner_map_candidates': '''
        SELECT PARTNER_NAME, SF_ID, RAW_SF_ID, SF_ID_SOURCE, CMS_ID, ZUORA_NAME
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
        WHERE PARTNER_NAME ILIKE ANY (
          '%rx technology%','%bulletproof%','%serit ostereng it%','%securetech%','%origami technology group%','%alphakor%','%system go%','%palmetto technology%','%flexxa%'
        )
        ORDER BY PARTNER_NAME, SF_ID
    ''',
    'output_rows_explicit_ids': f'''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, VENDOR, SF_ID, VENDOR_PARTNER_NAME, VENDOR_PRODUCT,
               EXCEPTION_BUCKET, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
          AND SF_ID IN ({ids})
        ORDER BY SF_ID, VENDOR_PRODUCT
    '''
}

for n, q in queries.items():
    print(f"\n=== {n} ===")
    df = fetch_dataframe(q)
    if df.empty:
        print('(no rows)')
    else:
        print(df.to_string(index=False, max_colwidth=120))
