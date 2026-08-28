from pathlib import Path
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

print('Usage table ESET April totals:')
print(fetch_dataframe("""
select sum(quantity) qty, sum(amount) amt, count(*) row_count
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD
where upper(vendor)='ESET' and billing_month='2026-04-01'::date
""").to_string(index=False))

print('\nESET April encryption usage rows:')
print(fetch_dataframe("""
select vendor_partner_name, vendor_product_sku, quantity, unit_price, amount
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD
where upper(vendor)='ESET' and billing_month='2026-04-01'::date
  and upper(vendor_product_sku) like '%ENCRYPT%'
order by 1,2
""").to_string(index=False, max_colwidth=120))

sql = Path('PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/10_vendor_invoice_usage_intra_prod.sql').read_text(encoding='utf-8')
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()
for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
    cur.execute(stmt)
conn.commit(); cur.close(); conn.close()
print('\nintra rebuilt')

print('\nESET Apr intra snapshot after next pass:')
print(fetch_dataframe("""
select sku, vendor_invoice_sku, vendor_usage_sku, vendor_invoice_seats, vendor_raw_usage_seats,
       vendor_invoice_amount, vendor_raw_usage_amount, delta_seats, delta_amount, source_status
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where upper(vendor)='ESET' and billing_month='2026-04-01'::date
order by abs(delta_amount) desc, sku
""").to_string(index=False, max_colwidth=140))

print('\nESET Apr intra invoice-side total check:')
print(fetch_dataframe("""
select sum(coalesce(vendor_invoice_amount,0)) as intra_invoice_amt,
       sum(coalesce(vendor_raw_usage_amount,0)) as intra_usage_amt,
       sum(coalesce(vendor_invoice_seats,0)) as intra_invoice_qty,
       sum(coalesce(vendor_raw_usage_seats,0)) as intra_usage_qty
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where upper(vendor)='ESET' and billing_month='2026-04-01'::date
""").to_string(index=False))
