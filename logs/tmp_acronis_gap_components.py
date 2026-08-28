from TEMPLATES.Python.connection import fetch_dataframe

df = fetch_dataframe("""
with month_totals as (
  select
    billing_month,
    sum(vendor_invoice_amount) as invoice_amount,
    sum(vendor_raw_usage_amount) as usage_amount,
    sum(delta_amount) as net_delta_amount
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
  where vendor='Acronis'
    and billing_month between '2026-01-01'::date and '2026-05-01'::date
  group by 1
), invoice_only as (
  select billing_month, sum(vendor_invoice_amount) as invoice_only_amount
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
  where vendor='Acronis' and source_status='INVOICE_ONLY'
    and billing_month between '2026-01-01'::date and '2026-05-01'::date
  group by 1
), variance as (
  select billing_month, sum(delta_amount) as variance_delta_amount
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
  where vendor='Acronis' and source_status='VARIANCE'
    and billing_month between '2026-01-01'::date and '2026-05-01'::date
  group by 1
)
select
  m.billing_month,
  m.invoice_amount,
  m.usage_amount,
  m.net_delta_amount,
  (m.net_delta_amount / nullif(m.invoice_amount,0))*100 as delta_pct_of_invoice,
  i.invoice_only_amount,
  (i.invoice_only_amount / nullif(m.invoice_amount,0))*100 as invoice_only_pct_of_invoice,
  v.variance_delta_amount,
  ((m.net_delta_amount + i.invoice_only_amount) / nullif(m.invoice_amount,0))*100 as non_invoice_only_pct_of_invoice
from month_totals m
left join invoice_only i using (billing_month)
left join variance v using (billing_month)
order by m.billing_month
""")
print(df.to_string(index=False))
