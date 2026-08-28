from TEMPLATES.Python.connection import get_snowflake_connection
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()
cur.execute("""
delete from THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='ESET' and billing_month >= '2026-06-01'::date
""")
conn.commit(); cur.close(); conn.close()
print('Deleted ESET invoice rows for >= 2026-06-01.')
