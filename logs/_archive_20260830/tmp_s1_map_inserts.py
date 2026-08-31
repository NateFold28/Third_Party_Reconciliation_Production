from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()

stmts = [
"""
insert into THIRD_PARTY_RECON_SKU_MAP_PROD
(VENDOR, VENDOR_PRODUCT, VENDOR_SKU, CW_SKU, SKU_MATCH_KEY, MAPPING_NOTES, CONTRACT_COST_RATE, CW_RETAIL_RATE)
select 'SentinelOne','Control','S1ES-CTL-EN-T2-SA','UNMAPPED','CONTROL',
       'Control Tier 2 invoice alias observed Jan/Apr; map to CONTROL canonical group.',
       0.72, null
where not exists (
  select 1 from THIRD_PARTY_RECON_SKU_MAP_PROD
  where upper(vendor)='SENTINELONE' and upper(vendor_sku)='S1ES-CTL-EN-T2-SA'
)
""",
"""
insert into THIRD_PARTY_RECON_SKU_MAP_PROD
(VENDOR, VENDOR_PRODUCT, VENDOR_SKU, CW_SKU, SKU_MATCH_KEY, MAPPING_NOTES, CONTRACT_COST_RATE, CW_RETAIL_RATE)
select 'SentinelOne','Complete','S1ES-CMP-EN-T2-SA','UNMAPPED','COMPLETE',
       'Complete Tier 2 invoice alias observed Jan/Apr; map to COMPLETE canonical group.',
       1.01, null
where not exists (
  select 1 from THIRD_PARTY_RECON_SKU_MAP_PROD
  where upper(vendor)='SENTINELONE' and upper(vendor_sku)='S1ES-CMP-EN-T2-SA'
)
""",
"""
insert into THIRD_PARTY_RECON_SKU_MAP_PROD
(VENDOR, VENDOR_PRODUCT, VENDOR_SKU, CW_SKU, SKU_MATCH_KEY, MAPPING_NOTES, CONTRACT_COST_RATE, CW_RETAIL_RATE)
select 'SentinelOne','Cloud Funnel','MSSP','UNMAPPED','CLOUD_FUNNEL',
       'MSSP invoice alias for Cloud Funnel data export.',
       0.30, null
where not exists (
  select 1 from THIRD_PARTY_RECON_SKU_MAP_PROD
  where upper(vendor)='SENTINELONE' and upper(vendor_sku)='MSSP'
)
"""
]

for s in stmts:
    cur.execute(s)

conn.commit()
print('Inserted mapping aliases (if missing).')
cur.close(); conn.close()
