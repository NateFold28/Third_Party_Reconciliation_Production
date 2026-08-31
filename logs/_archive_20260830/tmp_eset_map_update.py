from TEMPLATES.Python.connection import get_snowflake_connection
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()

cur.execute("""
update THIRD_PARTY_RECON_SKU_MAP_PROD
set SKU_MATCH_KEY='FULL_DISK_ENCRYPTION'
where upper(vendor)='ESET'
  and (
    upper(coalesce(vendor_product,'')) in ('MSP - FULL DISK ENCRYPTION','FULL DISK ENCRYPTION')
    or upper(coalesce(vendor_sku,'')) in ('MSP - FULL DISK ENCRYPTION','MSP2FDE1')
  )
""")

cur.execute("""
update THIRD_PARTY_RECON_SKU_MAP_PROD
set SKU_MATCH_KEY='ENDPOINT_ENCRYPTION_PRO'
where upper(vendor)='ESET'
  and (
    upper(coalesce(vendor_product,'')) in ('MSP - ENDPOINT ENCRYPTION PRO','DESLOCK / ENDPOINT ENCRYPTION')
    or upper(coalesce(vendor_sku,'')) in ('MSP - ENDPOINT ENCRYPTION PRO','DSLP')
  )
""")

conn.commit()
cur.close(); conn.close()
print('ESET SKU map keys updated in Snowflake.')
