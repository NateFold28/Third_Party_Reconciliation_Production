from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd()))
from TEMPLATES.Python.connection import fetch_dataframe
q = "SELECT * FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.PROOFPOINT_RECON_DETAIL LIMIT 1"
df = fetch_dataframe(q)
print('\n'.join(df.columns))
