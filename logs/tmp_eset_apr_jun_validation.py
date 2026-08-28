from pathlib import Path
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

sql = Path('PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/10_vendor_invoice_usage_intra_prod.sql').read_text(encoding='utf-8')
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()
for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
    cur.execute(stmt)
conn.commit(); cur.close(); conn.close()
print('intra rebuilt')

print('\nESET monthly summary (Apr-Jun 2026):')
print(fetch_dataframe("""
select
  billing_month,
  round(sum(coalesce(vendor_invoice_seats,0)-coalesce(vendor_raw_usage_seats,0)),3) as net_delta_seats,
  round(sum(coalesce(vendor_raw_usage_amount,0)-coalesce(vendor_invoice_amount,0)),2) as net_delta_amount,
  round(sum(abs(coalesce(delta_amount,0))),2) as abs_delta_amount,
  count_if(source_status='MATCH') as match_rows,
  count_if(source_status='VARIANCE') as variance_rows,
  count_if(source_status='INVOICE_ONLY') as invoice_only_rows,
  count_if(source_status='USAGE_ONLY') as usage_only_rows
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where upper(vendor)='ESET'
  and billing_month between '2026-04-01'::date and '2026-06-01'::date
group by 1
order by 1
""").to_string(index=False))

print('\nESET non-zero seat deltas (Apr-Jun):')
print(fetch_dataframe("""
select billing_month, sku, vendor_invoice_seats, vendor_raw_usage_seats, delta_seats, source_status
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where upper(vendor)='ESET'
  and billing_month between '2026-04-01'::date and '2026-06-01'::date
  and abs(coalesce(delta_seats,0)) > 0.0001
order by billing_month, abs(delta_seats) desc
""").to_string(index=False, max_colwidth=140))

print('\nESET top amount variances (Apr-Jun):')
print(fetch_dataframe("""
select billing_month, sku, delta_amount, vendor_invoice_amount, vendor_raw_usage_amount, source_status
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where upper(vendor)='ESET'
  and billing_month between '2026-04-01'::date and '2026-06-01'::date
order by abs(delta_amount) desc
limit 20
""").to_string(index=False, max_colwidth=140))
