from TEMPLATES.Python.connection import fetch_dataframe

queries = {
    'detail_columns': '''
        SELECT COLUMN_NAME
        FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION' AND TABLE_NAME='THIRD_PARTY_RECON_DETAIL_PROD'
        ORDER BY ORDINAL_POSITION
    ''',
    'output_columns': '''
        SELECT COLUMN_NAME
        FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION' AND TABLE_NAME='THIRD_PARTY_RECON_OUTPUT_PROD'
        ORDER BY ORDINAL_POSITION
    ''',
    'detail_june_focus': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, VENDOR_PRODUCT,
               BILLING_SOURCE_MIX, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_DETAIL_PROD
        WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
          AND (
            SF_ID IN ('ACT-00246798','ACT-00150133','ACT-00239634','ACT-00238000','ACT-00287953','ACT-00151761','ACT-00031023','ACT-00069994','ACT-00098437')
            OR VENDOR_PARTNER_NAME ILIKE '%Rx Technology%'
            OR VENDOR_PARTNER_NAME ILIKE '%Bulletproof%'
            OR VENDOR_PARTNER_NAME ILIKE '%Serit Ostereng IT%'
            OR VENDOR_PARTNER_NAME ILIKE '%SecureTech%'
            OR VENDOR_PARTNER_NAME ILIKE '%Origami Technology Group%'
            OR VENDOR_PARTNER_NAME ILIKE '%AlphaKOR%'
            OR VENDOR_PARTNER_NAME ILIKE '%Flexxa%'
          )
        ORDER BY SF_ID, VENDOR_PARTNER_NAME, VENDOR_PRODUCT
    ''',
    'output_june_focus': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, VENDOR, SF_ID, VENDOR_PARTNER_NAME, VENDOR_PRODUCT,
               EXCEPTION_BUCKET, OUTCOME_FLAG, VENDOR_QUANTITY, TOTAL_BILLING_QUANTITY
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE VENDOR='Acronis' AND BILLING_MONTH='2026-06-01'::DATE
          AND (
            SF_ID IN ('ACT-00246798','ACT-00150133','ACT-00239634','ACT-00238000','ACT-00287953','ACT-00151761','ACT-00031023','ACT-00069994','ACT-00098437')
            OR VENDOR_PARTNER_NAME ILIKE '%Rx Technology%'
            OR VENDOR_PARTNER_NAME ILIKE '%Bulletproof%'
            OR VENDOR_PARTNER_NAME ILIKE '%Serit Ostereng IT%'
            OR VENDOR_PARTNER_NAME ILIKE '%SecureTech%'
            OR VENDOR_PARTNER_NAME ILIKE '%Origami Technology Group%'
            OR VENDOR_PARTNER_NAME ILIKE '%AlphaKOR%'
            OR VENDOR_PARTNER_NAME ILIKE '%Flexxa%'
          )
        ORDER BY SF_ID, VENDOR_PARTNER_NAME, VENDOR_PRODUCT
    ''',
    'usage_june_focus': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU,
               SUM(QUANTITY) AS VQTY, SUM(AMOUNT) AS VAMT
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
    'partner_map_focus': '''
        SELECT PARTNER_NAME, SF_ID, RAW_SF_ID, SF_ID_SOURCE, CMS_ID
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
        WHERE PARTNER_NAME ILIKE '%bulletproof%'
           OR PARTNER_NAME ILIKE '%origami technology group%'
           OR PARTNER_NAME ILIKE '%securetech%'
           OR PARTNER_NAME ILIKE '%serit ostereng it%'
           OR PARTNER_NAME ILIKE '%rx technology%'
           OR PARTNER_NAME ILIKE '%alphakor%'
           OR PARTNER_NAME ILIKE '%flexxa%'
           OR PARTNER_NAME ILIKE '%system go%'
        ORDER BY PARTNER_NAME, SF_ID
    '''
}

for n, q in queries.items():
    print(f"\n=== {n} ===")
    try:
        df = fetch_dataframe(q)
    except Exception as e:
        print(f"ERROR: {e}")
        continue
    if df.empty:
        print('(no rows)')
    else:
        print(df.to_string(index=False, max_colwidth=120))
