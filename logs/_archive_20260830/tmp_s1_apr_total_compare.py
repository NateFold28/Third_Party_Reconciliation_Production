from TEMPLATES.Python.connection import fetch_dataframe

print('April SentinelOne totals - invoice table (all files):')
print(fetch_dataframe("""
select sum(quantity) as invoice_qty, sum(amount) as invoice_amt
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='SENTINELONE'
  and billing_month='2026-04-01'::date
""").to_string(index=False))

print('\nApril SentinelOne totals - intra table:')
print(fetch_dataframe("""
select sum(coalesce(vendor_invoice_seats,0)) as intra_invoice_qty,
       sum(coalesce(vendor_invoice_amount,0)) as intra_invoice_amt
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where upper(vendor)='SENTINELONE'
  and billing_month='2026-04-01'::date
""").to_string(index=False))

print('\nOnly INV124686 totals (for reference):')
print(fetch_dataframe("""
select sum(quantity) as inv124686_qty, sum(amount) as inv124686_amt
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='SENTINELONE'
  and billing_month='2026-04-01'::date
  and file_path ilike '%INV124686%'
""").to_string(index=False))
