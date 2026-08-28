from pathlib import Path
import sys
from textwrap import dedent
sys.path.insert(0, str(Path.cwd()))
from TEMPLATES.Python.connection import fetch_dataframe
q = dedent("""
SELECT PARTNER_NAME, SF_ID, RAW_SF_ID, SF_ID_SOURCE
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
WHERE UPPER(TRIM(PARTNER_NAME)) IN (
  'ACCESS GROUP INC','ELEVITYIT','ELEVITY IT','EXECUTECH','GFLEX','KMICRO','NUMSP','SFY','SFY IT'
)
ORDER BY 1;
""")
print(fetch_dataframe(q).to_string(index=False))
