from TEMPLATES.Python.connection import fetch_dataframe

print(fetch_dataframe("""
select vendor, count(*) as row_count, min(billing_month) as min_mo, max(billing_month) as max_mo
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
group by 1 order by 1
""").to_string(index=False))

print('\nSentinelOne sample rows:')
print(fetch_dataframe("""
select billing_month, vendor, file_path, vendor_product_sku, quantity, amount
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor) like '%SENTINEL%'
order by billing_month desc
limit 20
""").to_string(index=False, max_colwidth=120))
