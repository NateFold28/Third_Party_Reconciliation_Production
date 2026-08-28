from TEMPLATES.Python.connection import fetch_dataframe
print(fetch_dataframe("""
select vendor, vendor_product, vendor_sku, cw_sku, sku_match_key, mapping_notes, contract_cost_rate, cw_retail_rate
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SKU_MAP_PROD
where upper(vendor)='SENTINELONE'
  and upper(vendor_sku) in ('PM-CFL-ND-T1-SA','S1ES-CMP-EN-T8-SA','S1ES-CTL-EN-T9-SA')
order by vendor_sku, cw_sku
""").to_string(index=False, max_colwidth=140))
