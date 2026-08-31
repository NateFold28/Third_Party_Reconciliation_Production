from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd()))
from TEMPLATES.Python.connection import get_snowflake_connection
conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
with conn.cursor() as cur:
    cur.execute('DESC TABLE THIRD_PARTY_RECON_PARTNER_MAP_PROD')
    for r in cur.fetchall():
        print(r[0])
conn.close()
