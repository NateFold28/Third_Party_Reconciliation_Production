from TEMPLATES.Python.connection import fetch_dataframe

print(fetch_dataframe("""
with inv as (
  select billing_month,
         upper(vendor_product_sku) as vendor_invoice_sku,
         sum(quantity) as inv_qty,
         sum(amount) as inv_amt
  from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
  where upper(vendor)='SENTINELONE'
    and billing_month='2026-04-01'::date
  group by 1,2
), m as (
  select upper(vendor_sku) as vendor_invoice_sku,
         sku_match_key
  from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SKU_MAP_PROD
  where upper(vendor)='SENTINELONE'
)
select i.vendor_invoice_sku,
       listagg(distinct m.sku_match_key, ' | ') within group(order by m.sku_match_key) as mapped_keys,
       i.inv_qty,
       i.inv_amt
from inv i
left join m on m.vendor_invoice_sku = i.vendor_invoice_sku
group by 1,3,4
order by 1
""").to_string(index=False))
