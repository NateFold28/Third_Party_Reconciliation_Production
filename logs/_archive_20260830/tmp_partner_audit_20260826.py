from TEMPLATES.Python.connection import fetch_dataframe

queries = {
    'map_partner_to_sf_m2m': '''
        SELECT
          COUNT(*) AS partner_names_with_multi_sf
        FROM (
          SELECT UPPER(TRIM(PARTNER_NAME)) AS partner_key
          FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
          WHERE PARTNER_NAME IS NOT NULL AND TRIM(PARTNER_NAME) <> ''
          GROUP BY 1
          HAVING COUNT(DISTINCT SF_ID) > 1
        )
    ''',
    'map_sf_to_partner_m2m': '''
        SELECT
          COUNT(*) AS sf_ids_with_multi_partner
        FROM (
          SELECT SF_ID
          FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
          WHERE SF_ID IS NOT NULL AND TRIM(SF_ID) <> ''
          GROUP BY 1
          HAVING COUNT(DISTINCT UPPER(TRIM(PARTNER_NAME))) > 1
        )
    ''',
    'output_null_pipe_summary': '''
        SELECT
          COUNT(*) AS total_rows,
          COUNT_IF(VENDOR_PARTNER_NAME IS NULL OR TRIM(VENDOR_PARTNER_NAME)='') AS null_partner_rows,
          COUNT_IF(VENDOR_PARTNER_NAME LIKE '%|%') AS pipe_partner_rows
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE BILLING_MONTH >= '2026-01-01'
    ''',
    'output_null_pipe_by_vendor': '''
        SELECT
          VENDOR,
          COUNT(*) AS total_rows,
          COUNT_IF(VENDOR_PARTNER_NAME IS NULL OR TRIM(VENDOR_PARTNER_NAME)='') AS null_partner_rows,
          COUNT_IF(VENDOR_PARTNER_NAME LIKE '%|%') AS pipe_partner_rows
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE BILLING_MONTH >= '2026-01-01'
        GROUP BY 1
        ORDER BY null_partner_rows DESC, pipe_partner_rows DESC
    ''',
    'null_partner_outcome_mix': '''
        SELECT
          EXCEPTION_TYPE,
          OUTCOME_FLAG,
          COUNT(*) AS rows,
          SUM(ABS(COALESCE(AMOUNT_DELTA,0))) AS abs_amt_delta
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE BILLING_MONTH >= '2026-01-01'
          AND (VENDOR_PARTNER_NAME IS NULL OR TRIM(VENDOR_PARTNER_NAME)='')
        GROUP BY 1,2
        ORDER BY rows DESC
        LIMIT 40
    ''',
}

for name, sql in queries.items():
    print(f"\n=== {name} ===")
    df = fetch_dataframe(sql)
    if df.empty:
        print('(no rows)')
    else:
        print(df.to_string(index=False))
