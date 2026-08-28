from TEMPLATES.Python.connection import fetch_dataframe

print(fetch_dataframe("""
select billing_month, vendor, partner, vendor_product_sku, description, quantity, unit_price, amount, file_path
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where vendor ilike '%sentinel%'
  and billing_month = '2026-04-01'::date
  and file_path ilike '%INV124686%'
order by vendor_product_sku
""").to_string(index=False, max_colwidth=120))

print('\nTotals:')
print(fetch_dataframe("""
select sum(quantity) as qty, sum(amount) as amount
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where vendor ilike '%sentinel%'
  and billing_month = '2026-04-01'::date
  and file_path ilike '%INV124686%'
""").to_string(index=False))
