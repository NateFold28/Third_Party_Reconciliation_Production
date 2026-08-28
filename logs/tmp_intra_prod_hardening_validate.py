from pathlib import Path
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

sql = Path('PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/10_vendor_invoice_usage_intra_prod.sql').read_text(encoding='utf-8')
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()
for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
    cur.execute(stmt)
conn.commit(); cur.close(); conn.close()
print('intra rebuilt with production date floor')

print('\nCheck for pre-2026 rows (should be 0):')
print(fetch_dataframe("""
select count(*) as row_count
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where billing_month < '2026-01-01'::date
""").to_string(index=False))

print('\nExium intra monthly summary (post-hardening):')
print(fetch_dataframe("""
select billing_month,
       round(sum(coalesce(vendor_raw_usage_seats,0)-coalesce(vendor_invoice_seats,0)),3) as net_delta_seats,
       round(sum(coalesce(vendor_raw_usage_amount,0)-coalesce(vendor_invoice_amount,0)),2) as net_delta_amount,
       round(sum(abs(coalesce(delta_amount,0))),2) as abs_delta_amount,
       count_if(source_status='MATCH') as match_rows,
       count_if(source_status='VARIANCE') as variance_rows,
       count_if(source_status='INVOICE_ONLY') as invoice_only_rows,
       count_if(source_status='RAW_USAGE_ONLY') as usage_only_rows
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where upper(vendor)='EXIUM'
group by 1
order by 1
""").to_string(index=False))

print('\nAll-vendor sanity (latest built rows by vendor):')
print(fetch_dataframe("""
select vendor, min(billing_month) as first_month, max(billing_month) as last_month, count(*) as row_count
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
group by 1
order by 1
""").to_string(index=False))
