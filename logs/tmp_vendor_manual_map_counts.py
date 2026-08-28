from pathlib import Path
import sys
from textwrap import dedent
sys.path.insert(0, str(Path.cwd()))
from TEMPLATES.Python.connection import fetch_dataframe
q = dedent("""
SELECT vendor, COUNT(*) AS row_count
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_VENDOR_PARTNER_MANUAL_MAP
GROUP BY 1 ORDER BY 2 DESC
""")
print(fetch_dataframe(q).to_string(index=False))
