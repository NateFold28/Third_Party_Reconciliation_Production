from TEMPLATES.Python.connection import fetch_dataframe

sql = """
with inv as (
  select billing_month,
         case
           when upper(vendor_product_sku)='S1ES-CTL-EN-T2-SA' then 'CONTROL'
           when upper(vendor_product_sku)='S1ES-CMP-EN-T2-SA' then 'COMPLETE'
           when upper(vendor_product_sku)='MSSP' then 'CLOUD FUNNEL'
           when upper(vendor_product_sku)='SP-FOR-ND-T1-SA' then 'REMOTEOPS'
           when upper(vendor_product_sku)='PR-AIAST-ND-T1-SA' then 'PURPLE AI'
           when upper(vendor_product_sku)='S1ES-CTL-EN-T9-SA' then 'CONTROL'
           when upper(vendor_product_sku)='S1ES-CMP-EN-T8-SA' then 'COMPLETE'
           when upper(vendor_product_sku)='SP-RGR-ND-T2-SA' then 'RANGER'
           when upper(vendor_product_sku)='SP-RGI-ND-T2-SA' then 'RANGER INSIGHTS'
           when upper(vendor_product_sku)='PM-RT30-ND-T2-SA' then 'DATA RETENTION 30'
           when upper(vendor_product_sku)='PM-RT90-ND-T2-SA' then 'DATA RETENTION 90'
           when upper(vendor_product_sku)='PM-RT180-ND-T2-SA' then 'DATA RETENTION 180'
           when upper(vendor_product_sku)='PM-RT1Y-ND-T2-SA' then 'DATA RETENTION 365'
           when upper(vendor_product_sku)='PM-TI-ND-T2-SA' then 'THREAT INTELLIGENCE'
           when upper(vendor_product_sku)='PM-CFL-ND-T1-SA' then 'CLOUD FUNNEL'
           when upper(vendor_product_sku)='SS-WAT-ND-T2-SA' then 'WATCHTOWER'
           else upper(vendor_product_sku)
         end as sku,
         sum(quantity) qty,
         sum(amount) amt
  from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
  where upper(vendor)='SENTINELONE'
  group by 1,2
), usage as (
  select billing_month,
         upper(vendor_product_sku) sku,
         sum(quantity) qty,
         sum(amount) amt
  from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD
  where upper(vendor)='SENTINELONE'
  group by 1,2
), j as (
  select coalesce(i.billing_month,u.billing_month) billing_month,
         coalesce(i.sku,u.sku) sku,
         coalesce(i.qty,0) inv_qty,
         coalesce(u.qty,0) use_qty,
         coalesce(i.amt,0) inv_amt,
         coalesce(u.amt,0) use_amt
  from inv i
  full join usage u
    on i.billing_month=u.billing_month and i.sku=u.sku
)
select billing_month,
       sum(use_qty-inv_qty) as delta_qty,
       sum(use_amt-inv_amt) as delta_amt,
       sum(case when sku not in ('PURPLE AI','REMOTEOPS') then use_qty-inv_qty else 0 end) as residual_qty_ex_known,
       sum(case when sku not in ('PURPLE AI','REMOTEOPS') then use_amt-inv_amt else 0 end) as residual_amt_ex_known
from j
group by 1
order by 1
"""
print(fetch_dataframe(sql).to_string(index=False))
