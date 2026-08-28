from TEMPLATES.Python.connection import fetch_dataframe

df = fetch_dataframe("""
select
  billing_month,
  sku,
  source_status,
  sum(vendor_invoice_amount) as invoice_amount,
  sum(vendor_raw_usage_amount) as usage_amount,
  sum(delta_amount) as delta_amount,
  sum(vendor_invoice_seats) as invoice_qty,
  sum(vendor_raw_usage_seats) as usage_qty
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where vendor='Acronis'
  and source_status='INVOICE_ONLY'
group by 1,2,3
order by billing_month, abs(delta_amount) desc
""")
print(df.to_string(index=False))

roll = fetch_dataframe("""
select
  sku,
  count(distinct billing_month) as months_invoice_only,
  sum(vendor_invoice_amount) as invoice_only_amount
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where vendor='Acronis' and source_status='INVOICE_ONLY'
group by 1
order by invoice_only_amount desc
""")
print('\\nInvoice-only rollup:')
print(roll.to_string(index=False))
