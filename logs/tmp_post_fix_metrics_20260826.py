from TEMPLATES.Python.connection import fetch_dataframe

queries = {
    'map_partner_to_sf_m2m_after': '''
        SELECT COUNT(*) AS partner_names_with_multi_sf
        FROM (
          SELECT UPPER(TRIM(PARTNER_NAME)) AS partner_key
          FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
          WHERE PARTNER_NAME IS NOT NULL AND TRIM(PARTNER_NAME) <> ''
            AND SF_ID IS NOT NULL AND TRIM(SF_ID) <> ''
          GROUP BY 1
          HAVING COUNT(DISTINCT SF_ID) > 1
        )
    ''',
    'map_sf_to_partner_m2m_after': '''
        SELECT COUNT(*) AS sf_ids_with_multi_partner
        FROM (
          SELECT SF_ID
          FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
          WHERE SF_ID IS NOT NULL AND TRIM(SF_ID) <> ''
          GROUP BY 1
          HAVING COUNT(DISTINCT UPPER(TRIM(PARTNER_NAME))) > 1
        )
    ''',
    'output_null_pipe_summary_after': '''
        SELECT
          COUNT(*) AS total_rows,
          COUNT_IF(VENDOR_PARTNER_NAME IS NULL OR TRIM(VENDOR_PARTNER_NAME)='') AS null_partner_rows,
          COUNT_IF(VENDOR_PARTNER_NAME LIKE '%|%') AS pipe_partner_rows
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE BILLING_MONTH >= '2026-01-01'
    ''',
    'output_null_pipe_by_vendor_after': '''
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
    'null_partner_sf_presence_after': '''
        SELECT
          COUNT(*) AS null_partner_rows,
          COUNT_IF(SF_ID IS NULL OR TRIM(SF_ID)='') AS sf_id_missing_rows,
          COUNT_IF(SF_ID IS NOT NULL AND TRIM(SF_ID)<>'') AS sf_id_present_rows
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE BILLING_MONTH >= '2026-01-01'
          AND (VENDOR_PARTNER_NAME IS NULL OR TRIM(VENDOR_PARTNER_NAME)='')
    ''',
}

for name, sql in queries.items():
    print(f"\n=== {name} ===")
    df = fetch_dataframe(sql)
    print(df.to_string(index=False))
