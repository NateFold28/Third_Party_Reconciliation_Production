from TEMPLATES.Python.connection import fetch_dataframe

print('Monthly totals: Exium usage vs Exium Zuora billing source')
print(fetch_dataframe("""
with u as (
  select billing_month::date as month, sum(amount) as usage_amt, sum(quantity) as usage_qty
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_usage_prod
  where upper(vendor)='EXIUM'
  group by 1
), z as (
  select billing_month::date as month, sum(charge_amount_usd) as zuora_amt, sum(qty) as zuora_qty
  from analytics_dev.dbt_nfold_transformation.third_party_recon_source_zuora_prod
  where upper(vendor)='EXIUM'
  group by 1
)
select coalesce(u.month,z.month) as month, usage_qty, usage_amt, zuora_qty, zuora_amt
from u full outer join z on u.month=z.month
order by 1
""").to_string(index=False))

print('\nShift test totals: usage month m vs zuora month m+shift')
print(fetch_dataframe("""
with u as (
  select billing_month::date as month, sum(amount) as usage_amt, sum(quantity) as usage_qty
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_usage_prod
  where upper(vendor)='EXIUM'
  group by 1
), z as (
  select billing_month::date as month, sum(charge_amount_usd) as zuora_amt, sum(qty) as zuora_qty
  from analytics_dev.dbt_nfold_transformation.third_party_recon_source_zuora_prod
  where upper(vendor)='EXIUM'
  group by 1
), s as (select -1 as shift_mo union all select 0 union all select 1)
select s.shift_mo,
       round(sum(abs(coalesce(u.usage_qty,0)-coalesce(z.zuora_qty,0))),2) as total_abs_qty_delta,
       round(sum(abs(coalesce(u.usage_amt,0)-coalesce(z.zuora_amt,0))),2) as total_abs_amt_delta,
       round(sum(coalesce(u.usage_amt,0)-coalesce(z.zuora_amt,0)),2) as net_amt_delta
from u
cross join s
left join z on z.month = dateadd('month', s.shift_mo, u.month)
group by 1
order by 1
""").to_string(index=False))
