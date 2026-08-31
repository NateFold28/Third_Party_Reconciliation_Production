from pathlib import Path
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

print('Acronis May usage totals after reload:')
print(fetch_dataframe("""
select sum(quantity) as qty, sum(amount) as amount, count(*) as row_count
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_usage_prod
where upper(vendor)='ACRONIS' and billing_month='2026-05-01'::date
""").to_string(index=False))

print('\nAcronis May usage top SKU amounts after reload:')
print(fetch_dataframe("""
select vendor_product_sku, sum(quantity) as qty, sum(amount) as amount, max(unit_price) as max_unit_price
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_usage_prod
where upper(vendor)='ACRONIS' and billing_month='2026-05-01'::date
group by 1
order by amount desc
limit 25
""").to_string(index=False))

sql = Path('PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/10_vendor_invoice_usage_intra_prod.sql').read_text(encoding='utf-8')
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()
for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
    cur.execute(stmt)
conn.commit(); cur.close(); conn.close()
print('\nintra rebuilt')

print('\nAcronis May intra summary after fix:')
print(fetch_dataframe("""
select
  sum(coalesce(vendor_invoice_amount,0)) as invoice_amt,
  sum(coalesce(vendor_raw_usage_amount,0)) as usage_amt,
  sum(coalesce(vendor_raw_usage_amount,0)-coalesce(vendor_invoice_amount,0)) as net_delta_amt,
  sum(abs(coalesce(delta_amount,0))) as abs_delta_amt,
  sum(coalesce(vendor_invoice_seats,0)) as invoice_qty,
  sum(coalesce(vendor_raw_usage_seats,0)) as usage_qty,
  sum(coalesce(vendor_raw_usage_seats,0)-coalesce(vendor_invoice_seats,0)) as net_delta_qty
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where upper(vendor)='ACRONIS' and billing_month='2026-05-01'::date
""").to_string(index=False))

print('\nAcronis May top intra amount variances after fix:')
print(fetch_dataframe("""
select sku, vendor_invoice_amount, vendor_raw_usage_amount, delta_amount,
       vendor_invoice_seats, vendor_raw_usage_seats, delta_seats, source_status
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where upper(vendor)='ACRONIS' and billing_month='2026-05-01'::date
order by abs(delta_amount) desc
limit 30
""").to_string(index=False, max_colwidth=140))
