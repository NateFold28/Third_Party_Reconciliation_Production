from TEMPLATES.Python.connection import fetch_dataframe

summary = fetch_dataframe("""
select
  billing_month,
  sum(vendor_invoice_amount) as invoice_amount,
  sum(vendor_raw_usage_amount) as usage_amount,
  sum(delta_amount) as net_delta_amount,
  sum(abs(delta_amount)) as abs_delta_amount,
  sum(vendor_invoice_seats) as invoice_qty,
  sum(vendor_raw_usage_seats) as usage_qty,
  sum(delta_seats) as net_delta_qty
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where vendor='Acronis' and billing_month='2026-05-01'::date
group by 1
""")
print('Acronis May summary:')
print(summary.to_string(index=False))

top = fetch_dataframe("""
select
  sku,
  sum(vendor_invoice_amount) as invoice_amount,
  sum(vendor_raw_usage_amount) as usage_amount,
  sum(delta_amount) as delta_amount,
  sum(vendor_invoice_seats) as invoice_qty,
  sum(vendor_raw_usage_seats) as usage_qty,
  sum(delta_seats) as delta_qty,
  max(source_status) as source_status
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where vendor='Acronis' and billing_month='2026-05-01'::date
group by 1
order by abs(sum(delta_amount)) desc
limit 12
""")
print('\nTop Acronis May deltas:')
print(top.to_string(index=False))
