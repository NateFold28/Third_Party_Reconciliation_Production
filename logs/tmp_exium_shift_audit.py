from TEMPLATES.Python.connection import fetch_dataframe

print('Exium monthly totals: usage vs invoice (raw table months)')
print(fetch_dataframe("""
with u as (
  select billing_month::date as month, sum(quantity) as usage_qty, sum(amount) as usage_amt
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_usage_prod
  where upper(vendor)='EXIUM'
  group by 1
), i as (
  select billing_month::date as month, sum(quantity) as inv_qty, sum(amount) as inv_amt
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoices
  where upper(vendor)='EXIUM'
  group by 1
)
select coalesce(u.month,i.month) as month, usage_qty, usage_amt, inv_qty, inv_amt
from u full outer join i on u.month=i.month
order by 1
""").to_string(index=False))

print('\nShift test (compare usage month m to invoice month m+shift):')
print(fetch_dataframe("""
with usage_sku as (
  select billing_month::date as usage_month,
         upper(trim(vendor_product_sku)) as sku,
         sum(quantity) as usage_qty,
         sum(amount) as usage_amt
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_usage_prod
  where upper(vendor)='EXIUM'
  group by 1,2
),
invoice_sku as (
  select billing_month::date as invoice_month,
         upper(trim(vendor_product_sku)) as sku,
         sum(quantity) as inv_qty,
         sum(amount) as inv_amt
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoices
  where upper(vendor)='EXIUM'
  group by 1,2
),
shifts as (
  select -1 as shift_mo union all select 0 union all select 1
),
cmp as (
  select
    u.usage_month,
    s.shift_mo,
    dateadd('month', s.shift_mo, u.usage_month) as mapped_invoice_month,
    u.sku,
    u.usage_qty,
    u.usage_amt,
    i.inv_qty,
    i.inv_amt,
    coalesce(u.usage_qty,0)-coalesce(i.inv_qty,0) as delta_qty,
    coalesce(u.usage_amt,0)-coalesce(i.inv_amt,0) as delta_amt
  from usage_sku u
  cross join shifts s
  left join invoice_sku i
    on i.invoice_month = dateadd('month', s.shift_mo, u.usage_month)
   and i.sku = u.sku
)
select
  shift_mo,
  count(*) as sku_rows,
  round(sum(abs(delta_qty)),3) as total_abs_delta_qty,
  round(sum(abs(delta_amt)),2) as total_abs_delta_amt,
  round(sum(delta_amt),2) as net_delta_amt
from cmp
group by 1
order by shift_mo
""").to_string(index=False))

print('\nBest-shift detail by usage month (abs amount delta):')
print(fetch_dataframe("""
with usage_m as (
  select billing_month::date as usage_month, sum(amount) as usage_amt
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_usage_prod
  where upper(vendor)='EXIUM'
  group by 1
),
invoice_m as (
  select billing_month::date as invoice_month, sum(amount) as inv_amt
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoices
  where upper(vendor)='EXIUM'
  group by 1
),
shifts as (select -1 as shift_mo union all select 0 union all select 1),
cmp as (
  select
    u.usage_month,
    s.shift_mo,
    dateadd('month', s.shift_mo, u.usage_month) as mapped_invoice_month,
    u.usage_amt,
    i.inv_amt,
    abs(coalesce(u.usage_amt,0)-coalesce(i.inv_amt,0)) as abs_delta_amt
  from usage_m u
  cross join shifts s
  left join invoice_m i
    on i.invoice_month = dateadd('month', s.shift_mo, u.usage_month)
),
r as (
  select *, row_number() over (partition by usage_month order by abs_delta_amt asc, abs(shift_mo) asc) as rk
  from cmp
)
select usage_month, shift_mo as best_shift, mapped_invoice_month, usage_amt, inv_amt, abs_delta_amt
from r
where rk=1
order by usage_month
""").to_string(index=False))

print('\nExium invoice file coverage by billing_month:')
print(fetch_dataframe("""
select billing_month::date as billing_month, file_path, vendor_product_sku, quantity, amount
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoices
where upper(vendor)='EXIUM'
order by 1,2
""").to_string(index=False, max_colwidth=160))

print('\nFinal reconciliation output coverage (Exium):')
print(fetch_dataframe("""
select billing_month::date as billing_month,
       count(*) as row_count,
       round(sum(coalesce(vendor_quantity,0)),3) as vendor_qty,
       round(sum(coalesce(vendor_amount,0)),2) as vendor_amt,
       round(sum(coalesce(total_billing_amount,0)),2) as billing_amt
from analytics_dev.dbt_nfold_transformation.third_party_recon_output_prod
where upper(vendor)='EXIUM'
group by 1
order by 1
""").to_string(index=False))
