from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd()))
from TEMPLATES.Python.connection import get_snowflake_connection
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
sql = '''
MERGE INTO RECON_VENDOR_PARTNER_MANUAL_MAP t
USING (
  SELECT * FROM VALUES
    ('Proofpoint','MHIT Automatisering','ACT-00232862','25319','MHIT Automatisering',NULL,'onetime_manual_20260827',CURRENT_TIMESTAMP()),
    ('Proofpoint','Summit Digital Networks','ACT-00212035','26097','Summit Digital Networks',NULL,'onetime_manual_20260827',CURRENT_TIMESTAMP()),
    ('Proofpoint','TRITECH CORPORATION AMERICA','ACT-00039364','3316','TRITECH CORPORATION AMERICA',NULL,'onetime_manual_20260827',CURRENT_TIMESTAMP()),
    ('Proofpoint','Applied Network Solutions','ACT-00189434','33661','Applied Network Solutions',NULL,'onetime_manual_20260827',CURRENT_TIMESTAMP()),
    ('Proofpoint','BluWave','ACT-00119428','24752','BluWave',NULL,'onetime_manual_20260827',CURRENT_TIMESTAMP()),
    ('Proofpoint','Baleehoo Media Inc','ACT-00298323','31644','Baleehoo Media Inc',NULL,'onetime_manual_20260827',CURRENT_TIMESTAMP()),
    ('Proofpoint','Jeff Computers','ACT-00004890','26335','Jeff Computers',NULL,'onetime_manual_20260827',CURRENT_TIMESTAMP()),
    ('Proofpoint','Engineered Medical Systems, Inc','ACT-00144238',NULL,'Engineered Medical Systems, Inc',NULL,'onetime_manual_20260827',CURRENT_TIMESTAMP()),
    ('Proofpoint','Windsor Telecom','ACT-00107433',NULL,'Windsor Telecom',NULL,'onetime_manual_20260827',CURRENT_TIMESTAMP()),
    ('Proofpoint','Alt Gr SA','ACT-00136789','19155','Alt Gr SA',NULL,'onetime_manual_20260827',CURRENT_TIMESTAMP())
  v(vendor, partner_name, sf_id, cms_id, zuora_name, parent_company, source_tag, updated_at)
) s
ON UPPER(TRIM(t.vendor)) = UPPER(TRIM(s.vendor))
AND UPPER(TRIM(t.partner_name)) = UPPER(TRIM(s.partner_name))
WHEN MATCHED THEN UPDATE SET
  t.sf_id = s.sf_id,
  t.cms_id = COALESCE(s.cms_id, t.cms_id),
  t.zuora_name = COALESCE(s.zuora_name, t.zuora_name),
  t.parent_company = COALESCE(s.parent_company, t.parent_company),
  t.source_tag = s.source_tag,
  t.updated_at = s.updated_at
WHEN NOT MATCHED THEN INSERT (vendor, partner_name, sf_id, cms_id, zuora_name, parent_company, source_tag, updated_at)
VALUES (s.vendor, s.partner_name, s.sf_id, s.cms_id, s.zuora_name, s.parent_company, s.source_tag, s.updated_at)
'''
with conn.cursor() as cur:
    cur.execute(sql)
conn.commit(); conn.close()
print('One-time Proofpoint manual mapping merge applied to RECON_VENDOR_PARTNER_MANUAL_MAP')
