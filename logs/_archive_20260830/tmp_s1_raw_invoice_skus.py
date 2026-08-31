from TEMPLATES.Python.connection import fetch_dataframe
print(fetch_dataframe("""
select vendor_product_sku, count(*) c, sum(quantity) qty, sum(amount) amt
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='SENTINELONE'
group by 1
order by 2 desc,1
""").to_string(index=False, max_colwidth=120))
