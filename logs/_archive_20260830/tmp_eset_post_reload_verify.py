from TEMPLATES.Python.connection import fetch_dataframe

print('ESET Apr invoice totals by file after reload:')
print(fetch_dataframe("""
select file_path, count(*) lines, sum(quantity) qty, sum(amount) amt
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where vendor ilike '%eset%'
  and billing_month='2026-04-01'::date
group by 1
order by 1
""").to_string(index=False, max_colwidth=120))

print('\nESET Apr invoice total:')
print(fetch_dataframe("""
select sum(quantity) qty, sum(amount) amt
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where vendor ilike '%eset%'
  and billing_month='2026-04-01'::date
""").to_string(index=False))

print('\nESET Apr intra invoice-side total:')
print(fetch_dataframe("""
select sum(coalesce(vendor_invoice_seats,0)) as intra_invoice_qty,
       sum(coalesce(vendor_invoice_amount,0)) as intra_invoice_amt
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where vendor ilike '%eset%'
  and billing_month='2026-04-01'::date
""").to_string(index=False))

print('\nESET Apr intra rows snapshot:')
print(fetch_dataframe("""
select vendor,billing_month,sku,vendor_invoice_sku,vendor_usage_sku,vendor_invoice_seats,vendor_raw_usage_seats,vendor_invoice_amount,vendor_raw_usage_amount,delta_seats,delta_amount,source_status
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where vendor ilike '%eset%'
  and billing_month='2026-04-01'::date
order by sku
""").to_string(index=False, max_colwidth=120))
