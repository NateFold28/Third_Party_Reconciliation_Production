from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd()))
from TEMPLATES.Python.connection import get_snowflake_connection
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
sql = '''
MERGE INTO THIRD_PARTY_RECON_PARTNER_MAP_PROD t
USING (
    SELECT * FROM VALUES
      ('Proofpoint','MHIT Automatisering',NULL,'ACT-00232862','25319','MHIT Automatisering'),
      ('Proofpoint','Summit Digital Networks',NULL,'ACT-00212035','26097','Summit Digital Networks'),
      ('Proofpoint','TRITECH CORPORATION AMERICA',NULL,'ACT-00039364','3316','TRITECH CORPORATION AMERICA'),
      ('Proofpoint','Applied Network Solutions',NULL,'ACT-00189434','33661','Applied Network Solutions'),
      ('Proofpoint','BluWave',NULL,'ACT-00119428','24752','BluWave'),
      ('Proofpoint','Baleehoo Media Inc',NULL,'ACT-00298323','31644','Baleehoo Media Inc'),
      ('Proofpoint','Jeff Computers',NULL,'ACT-00004890','26335','Jeff Computers'),
      ('Proofpoint','Engineered Medical Systems, Inc',NULL,'ACT-00144238',NULL,'Engineered Medical Systems, Inc'),
      ('Proofpoint','Windsor Telecom',NULL,'ACT-00107433',NULL,'Windsor Telecom'),
      ('Proofpoint','Alt Gr SA',NULL,'ACT-00136789','19155','Alt Gr SA')
) s(vendor, partner_name, parent_company, sf_id, cms_id, zuora_name)
ON UPPER(TRIM(t.vendor)) = UPPER(TRIM(s.vendor))
AND UPPER(TRIM(t.partner_name)) = UPPER(TRIM(s.partner_name))
WHEN MATCHED THEN UPDATE SET
  t.parent_company = COALESCE(s.parent_company, t.parent_company),
  t.sf_id = s.sf_id,
  t.cms_id = COALESCE(s.cms_id, t.cms_id),
  t.zuora_name = COALESCE(s.zuora_name, t.zuora_name)
WHEN NOT MATCHED THEN INSERT (vendor, partner_name, parent_company, sf_id, cms_id, zuora_name)
VALUES (s.vendor, s.partner_name, s.parent_company, s.sf_id, s.cms_id, s.zuora_name)
'''
with conn.cursor() as cur:
    cur.execute(sql)
conn.commit(); conn.close()
print('One-time merge applied to THIRD_PARTY_RECON_PARTNER_MAP_PROD for Proofpoint mappings')
