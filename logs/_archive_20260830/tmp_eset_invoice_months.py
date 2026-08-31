from TEMPLATES.Python.connection import fetch_dataframe
print(fetch_dataframe("""
select billing_month, count(*) rows, sum(amount) amount, sum(quantity) qty
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='ESET'
group by 1
order by 1
""").to_string(index=False))
