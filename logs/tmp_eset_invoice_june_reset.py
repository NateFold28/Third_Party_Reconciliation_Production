from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()
cur.execute("""
delete from THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='ESET' and billing_month='2026-06-01'::date
""")
conn.commit(); cur.close(); conn.close()
print('Deleted existing ESET June invoice rows (if any).')

print(fetch_dataframe("""
select billing_month, count(*) row_count, sum(amount) amount
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='ESET'
group by 1
order by 1
""").to_string(index=False))
