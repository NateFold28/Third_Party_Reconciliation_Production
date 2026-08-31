from TEMPLATES.Python.connection import fetch_dataframe

queries = {
    'sf_account_identity': '''
        SELECT CWS_ACCOUNT_UNIQUE_IDENTIFIER_C AS SF_ID, NAME, IS_DELETED
        FROM ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT
        WHERE CWS_ACCOUNT_UNIQUE_IDENTIFIER_C IN ('ACT-00133333','ACT-00274986','ACT-00230106')
        ORDER BY 1
    ''',
    'merged_map_for_ids': '''
        SELECT old_account, new_account, merged_by_date, old_account_is_deleted, merged_by_user
        FROM ANALYTICS.DBO.CW_DW__MERGED_ACCOUNT_MAP
        WHERE old_account IN ('ACT-00133333','ACT-00274986','ACT-00230106')
           OR new_account IN ('ACT-00133333','ACT-00274986','ACT-00230106')
        ORDER BY merged_by_date
    ''',
    'partner_map_for_concerto_promsyst': '''
        SELECT PARTNER_NAME, SF_ID, RAW_SF_ID, SF_ID_SOURCE, CMS_ID, ZUORA_NAME
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
        WHERE PARTNER_NAME ILIKE '%concerto%'
           OR PARTNER_NAME ILIKE '%promsyst%'
        ORDER BY PARTNER_NAME, SF_ID
    ''',
    'acronis_usage_presence': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU,
               SUM(QUANTITY) AS QTY, SUM(AMOUNT) AS AMT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_USAGE
        WHERE VENDOR_PARTNER_NAME ILIKE '%concerto%'
           OR VENDOR_PARTNER_NAME ILIKE '%promsyst%'
        GROUP BY 1,2,3
        ORDER BY BILLING_MONTH, VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU
    ''',
    'webroot_usage_presence': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU,
               SUM(QUANTITY) AS QTY, SUM(AMOUNT) AS AMT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.WEBROOT_USAGE
        WHERE VENDOR_PARTNER_NAME ILIKE '%concerto%'
           OR VENDOR_PARTNER_NAME ILIKE '%promsyst%'
        GROUP BY 1,2,3
        ORDER BY BILLING_MONTH, VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU
    ''',
    's1_usage_presence': '''
        SELECT BILLING_MONTH::DATE AS BILLING_MONTH, VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU,
               SUM(QUANTITY) AS QTY, SUM(AMOUNT) AS AMT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.SENTINELONE_USAGE
        WHERE VENDOR_PARTNER_NAME ILIKE '%concerto%'
           OR VENDOR_PARTNER_NAME ILIKE '%promsyst%'
        GROUP BY 1,2,3
        ORDER BY BILLING_MONTH, VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU
    '''
}

for name, sql in queries.items():
    print(f"\n=== {name} ===")
    df = fetch_dataframe(sql)
    if df.empty:
        print('(no rows)')
    else:
        print(df.to_string(index=False, max_colwidth=120))
