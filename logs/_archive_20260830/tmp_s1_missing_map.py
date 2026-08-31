from TEMPLATES.Python.connection import fetch_dataframe
print(fetch_dataframe("""
with inv as (
  select distinct upper(vendor_product_sku) sku
  from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
  where upper(vendor)='SENTINELONE'
), mp as (
  select distinct upper(vendor_sku) sku
  from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SKU_MAP_PROD
  where upper(vendor)='SENTINELONE'
)
select inv.sku
from inv
left join mp using(sku)
where mp.sku is null
order by 1
""").to_string(index=False))
