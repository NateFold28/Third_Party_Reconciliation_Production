from TEMPLATES.Python.connection import fetch_dataframe

print('April SentinelOne invoice files:')
print(fetch_dataframe("""
select file_path,
       count(*) as lines,
       sum(quantity) as qty,
       sum(amount) as amt
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='SENTINELONE'
  and billing_month='2026-04-01'::date
group by 1
order by 1
""").to_string(index=False))

print('\nApril SentinelOne invoice rows (all files):')
print(fetch_dataframe("""
select file_path, vendor_product_sku, quantity, unit_price, amount
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='SENTINELONE'
  and billing_month='2026-04-01'::date
order by file_path, vendor_product_sku
""").to_string(index=False, max_colwidth=120))
