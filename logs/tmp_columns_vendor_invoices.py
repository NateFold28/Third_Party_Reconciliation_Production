from TEMPLATES.Python.connection import fetch_dataframe
print(fetch_dataframe("""
select column_name
from ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
where table_schema='DBT_NFOLD_TRANSFORMATION' and table_name='THIRD_PARTY_RECON_VENDOR_INVOICES'
order by ordinal_position
""").to_string(index=False))
