from TEMPLATES.Python.connection import fetch_dataframe
from pathlib import Path

out_dir = Path(r'c:/Users/Nate.Fold/projects/logs')
out_dir.mkdir(parents=True, exist_ok=True)

queries = {
    'partner_name_multi_sf_candidates': '''
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
    ''',
    'null_partner_rows_by_vendor_exception': '''
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
    '''
}

for name, sql in queries.items():
    df = fetch_dataframe(sql)
    path = out_dir / f'{name}_20260826.csv'
    df.to_csv(path, index=False)
    print(f'{name}: {len(df)} rows -> {path}')
