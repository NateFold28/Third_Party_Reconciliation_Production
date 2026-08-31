from TEMPLATES.Python.connection import fetch_dataframe

summary = fetch_dataframe("""
select
  billing_month,
  sum(vendor_invoice_amount) as invoice_amount,
  sum(vendor_raw_usage_amount) as usage_amount,
  sum(delta_amount) as net_delta_amount,
  count_if(vendor_invoice_amount is null) as null_invoice_rows,
  count(*) as total_rows
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where vendor='Acronis'
  and billing_month between '2026-01-01'::date and '2026-07-01'::date
group by 1
order by 1
""")
print(summary.to_string(index=False))

june = fetch_dataframe("""
select sku, vendor_invoice_amount, vendor_raw_usage_amount, source_status
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where vendor='Acronis' and billing_month='2026-06-01'::date
order by abs(coalesce(delta_amount,0)) desc
limit 10
""")
print('\\nTop June rows (should show null invoice fields where missing):')
print(june.to_string(index=False))
