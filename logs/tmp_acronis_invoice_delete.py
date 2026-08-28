from TEMPLATES.Python.connection import get_snowflake_connection
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()
cur.execute("""
delete from THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='ACRONIS' and billing_month >= '2026-01-01'::date
""")
conn.commit(); cur.close(); conn.close()
print('Deleted Acronis invoice rows from 2026-01 onward.')
