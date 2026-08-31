from TEMPLATES.Python.connection import fetch_dataframe

print('EXIUM_RECON_DETAIL timing signal (existing output):')
print(fetch_dataframe("""
select
  billing_month,
  count(*) as row_count,
  sum(case when outcome_flag='SKU_MISMATCH_BILLING_ON_OTHER_SKU' then 1 else 0 end) as sku_mismatch_rows,
  sum(case when nearby_billing_month_offset is not null then 1 else 0 end) as has_nearby_rows,
  sum(case when nearby_billing_month_offset = 1 then 1 else 0 end) as nearby_plus1_rows,
  sum(case when nearby_billing_month_offset = -1 then 1 else 0 end) as nearby_minus1_rows
from analytics_dev.dbt_nfold_transformation.exium_recon_detail
group by 1
order by 1
""").to_string(index=False))

print('\nExium recon rows by nearby_billing_month_offset:')
print(fetch_dataframe("""
select nearby_billing_month_offset, count(*) as row_count,
       round(sum(coalesce(vendor_amount,0)),2) as vendor_amt,
       round(sum(coalesce(total_billing_amount,0)),2) as billing_amt
from analytics_dev.dbt_nfold_transformation.exium_recon_detail
where nearby_billing_month_offset is not null
group by 1
order by 1
""").to_string(index=False))

print('\nShift experiment on final-recon keys (sf_id + sku_match_group):')
print(fetch_dataframe("""
with v as (
  select sf_id, billing_month::date as billing_month, sku_match_group,
         sum(coalesce(vendor_quantity,0)) as vendor_qty,
         sum(coalesce(vendor_amount,0)) as vendor_amt
  from analytics_dev.dbt_nfold_transformation.exium_recon_detail
  group by 1,2,3
),
b as (
  select sf_id, billing_month::date as billing_month, sku_match_group,
         sum(coalesce(total_billing_quantity,0)) as bill_qty,
         sum(coalesce(total_billing_amount,0)) as bill_amt
  from analytics_dev.dbt_nfold_transformation.exium_recon_detail
  group by 1,2,3
),
shifts as (select -1 as shift_mo union all select 0 union all select 1),
cmp as (
  select
    s.shift_mo,
    v.sf_id,
    v.billing_month,
    v.sku_match_group,
    v.vendor_qty,
    v.vendor_amt,
    b.bill_qty,
    b.bill_amt,
    abs(coalesce(v.vendor_qty,0)-coalesce(b.bill_qty,0)) as abs_qty_delta,
    abs(coalesce(v.vendor_amt,0)-coalesce(b.bill_amt,0)) as abs_amt_delta
  from v
  cross join shifts s
  left join b
    on b.sf_id=v.sf_id
   and b.sku_match_group=v.sku_match_group
   and b.billing_month=dateadd('month', s.shift_mo, v.billing_month)
)
select shift_mo,
       count(*) as key_rows,
       round(sum(abs_qty_delta),2) as total_abs_qty_delta,
       round(sum(abs_amt_delta),2) as total_abs_amt_delta
from cmp
group by 1
order by 1
""").to_string(index=False))
