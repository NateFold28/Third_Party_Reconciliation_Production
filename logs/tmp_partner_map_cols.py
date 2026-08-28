from TEMPLATES.Python.connection import get_snowflake_connection
conn=get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur=conn.cursor()
cur.execute("select column_name from ANALYTICS_DEV.information_schema.columns where table_schema='DBT_NFOLD_TRANSFORMATION' and table_name='THIRD_PARTY_RECON_PARTNER_MAP_PROD' order by ordinal_position")
for r in cur.fetchall():
    print(r[0])
cur.close(); conn.close()
