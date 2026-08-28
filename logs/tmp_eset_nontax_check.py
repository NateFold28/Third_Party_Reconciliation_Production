from TEMPLATES.Python.connection import fetch_dataframe
print(fetch_dataframe("""
select
  sum(amount) as invoice_amt,
  sum(quantity) as invoice_qty,
  count(*) as invoice_rows
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='ESET' and billing_month='2026-04-01'::date
""").to_string(index=False))

print(fetch_dataframe("""
select count(*) as tax_like_rows, sum(amount) as tax_like_amount
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='ESET' and billing_month='2026-04-01'::date
  and regexp_like(upper(coalesce(item_desc,'')), 'TAX|VAT|GST|HST|SALES TAX')
""").to_string(index=False))
