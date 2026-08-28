from TEMPLATES.Python.connection import fetch_dataframe
print(fetch_dataframe("""
select vendor_sku, vendor_product, sku_match_key, mapping_notes
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SKU_MAP_PROD
where upper(vendor)='SENTINELONE'
order by 1,2
""").to_string(index=False, max_colwidth=120))
