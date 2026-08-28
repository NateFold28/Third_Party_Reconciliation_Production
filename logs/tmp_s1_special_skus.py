from TEMPLATES.Python.connection import fetch_dataframe
print(fetch_dataframe("""
select billing_month, file_path, vendor_product_sku, description, quantity, unit_price, amount
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='SENTINELONE'
  and upper(vendor_product_sku) in ('S1ES CTL EN T2 SA','S1ES CMP EN T2 SA','MSSP','WATCHTOWER','PURPLE AI','REMOTEOPS')
order by billing_month, vendor_product_sku
""").to_string(index=False, max_colwidth=160))
