from TEMPLATES.Python.connection import fetch_dataframe

queries = {
    'null_partner_outcome_mix': '''
        SELECT
          EXCEPTION_TYPE,
          OUTCOME_FLAG,
          COUNT(*) AS row_count,
          SUM(ABS(COALESCE(AMOUNT_DELTA,0))) AS abs_amt_delta
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE BILLING_MONTH >= '2026-01-01'
          AND (VENDOR_PARTNER_NAME IS NULL OR TRIM(VENDOR_PARTNER_NAME)='')
        GROUP BY 1,2
        ORDER BY row_count DESC
        LIMIT 40
    ''',
    'pipe_partner_outcome_mix': '''
        SELECT
          VENDOR,
          EXCEPTION_TYPE,
          COUNT(*) AS row_count,
          COUNT_IF(SF_ID IS NULL OR TRIM(SF_ID)='') AS sf_null_rows,
          SUM(ABS(COALESCE(AMOUNT_DELTA,0))) AS abs_amt_delta
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE BILLING_MONTH >= '2026-01-01'
          AND VENDOR_PARTNER_NAME LIKE '%|%'
        GROUP BY 1,2
        ORDER BY row_count DESC
        LIMIT 60
    ''',
    'top_partner_keys_multi_sf': '''
        SELECT
          UPPER(TRIM(PARTNER_NAME)) AS partner_key,
          COUNT(DISTINCT SF_ID) AS sf_id_count,
          LISTAGG(DISTINCT SF_ID, ' | ') WITHIN GROUP (ORDER BY SF_ID) AS sf_ids
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
        WHERE PARTNER_NAME IS NOT NULL AND TRIM(PARTNER_NAME) <> ''
          AND SF_ID IS NOT NULL AND TRIM(SF_ID) <> ''
        GROUP BY 1
        HAVING COUNT(DISTINCT SF_ID) > 1
        ORDER BY sf_id_count DESC, partner_key
        LIMIT 40
    ''',
    'top_sf_multi_partner': '''
        SELECT
          SF_ID,
          COUNT(DISTINCT UPPER(TRIM(PARTNER_NAME))) AS partner_name_count,
          LISTAGG(DISTINCT PARTNER_NAME, ' | ') WITHIN GROUP (ORDER BY PARTNER_NAME) AS partner_names
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
        WHERE SF_ID IS NOT NULL AND TRIM(SF_ID) <> ''
          AND PARTNER_NAME IS NOT NULL AND TRIM(PARTNER_NAME) <> ''
        GROUP BY 1
        HAVING COUNT(DISTINCT UPPER(TRIM(PARTNER_NAME))) > 1
        ORDER BY partner_name_count DESC, SF_ID
        LIMIT 40
    ''',
    'prod_map_vendor_overlap': '''
        SELECT
          UPPER(TRIM(PARTNER_NAME)) AS partner_key,
          COUNT(DISTINCT VENDOR) AS vendor_count,
          COUNT(DISTINCT SF_ID) AS sf_id_count,
          LISTAGG(DISTINCT VENDOR, ' | ') WITHIN GROUP (ORDER BY VENDOR) AS vendors,
          LISTAGG(DISTINCT SF_ID, ' | ') WITHIN GROUP (ORDER BY SF_ID) AS sf_ids
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_PARTNER_MAP_PROD
        WHERE PARTNER_NAME IS NOT NULL AND TRIM(PARTNER_NAME) <> ''
        GROUP BY 1
        HAVING COUNT(DISTINCT SF_ID) > 1
        ORDER BY sf_id_count DESC, vendor_count DESC
        LIMIT 40
    '''
}

for name, sql in queries.items():
    print(f"\n=== {name} ===")
    df = fetch_dataframe(sql)
    if df.empty:
        print('(no rows)')
    else:
        print(df.to_string(index=False, max_colwidth=140))
