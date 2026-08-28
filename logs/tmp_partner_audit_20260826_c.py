from TEMPLATES.Python.connection import fetch_dataframe

queries = {
    'null_partner_sf_presence': '''
        SELECT
          COUNT(*) AS null_partner_rows,
          COUNT_IF(SF_ID IS NULL OR TRIM(SF_ID)='') AS sf_id_missing_rows,
          COUNT_IF(SF_ID IS NOT NULL AND TRIM(SF_ID)<>'') AS sf_id_present_rows,
          COUNT_IF(VENDOR_QUANTITY > 0) AS has_vendor_usage_rows,
          COUNT_IF(TOTAL_BILLING_QUANTITY > 0) AS has_cw_billing_rows
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE BILLING_MONTH >= '2026-01-01'
          AND (VENDOR_PARTNER_NAME IS NULL OR TRIM(VENDOR_PARTNER_NAME)='')
    ''',
    'null_partner_vendor_bucket_sf': '''
        SELECT
          VENDOR,
          EXCEPTION_TYPE,
          COUNT(*) AS row_count,
          COUNT_IF(SF_ID IS NULL OR TRIM(SF_ID)='') AS sf_id_missing_rows,
          COUNT_IF(VENDOR_QUANTITY > 0) AS vendor_usage_rows,
          COUNT_IF(TOTAL_BILLING_QUANTITY > 0) AS cw_billing_rows,
          SUM(ABS(COALESCE(AMOUNT_DELTA,0))) AS abs_amt_delta
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE BILLING_MONTH >= '2026-01-01'
          AND (VENDOR_PARTNER_NAME IS NULL OR TRIM(VENDOR_PARTNER_NAME)='')
        GROUP BY 1,2
        ORDER BY row_count DESC
        LIMIT 80
    ''',
    'pipe_partner_top_values': '''
        SELECT
          VENDOR,
          VENDOR_PARTNER_NAME,
          COUNT(*) AS row_count,
          COUNT_IF(SF_ID IS NULL OR TRIM(SF_ID)='') AS sf_id_missing_rows,
          COUNT(DISTINCT SF_ID) AS distinct_sf_ids,
          SUM(ABS(COALESCE(AMOUNT_DELTA,0))) AS abs_amt_delta
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE BILLING_MONTH >= '2026-01-01'
          AND VENDOR_PARTNER_NAME LIKE '%|%'
        GROUP BY 1,2
        ORDER BY row_count DESC, abs_amt_delta DESC
        LIMIT 50
    '''
}

for name, sql in queries.items():
    print(f"\n=== {name} ===")
    df = fetch_dataframe(sql)
    print(df.to_string(index=False, max_colwidth=140))
