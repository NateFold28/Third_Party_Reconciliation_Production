from pathlib import Path
import sys
from textwrap import dedent
sys.path.insert(0, str(Path.cwd()))
from TEMPLATES.Python.connection import fetch_dataframe
q = dedent("""
SELECT
  COALESCE(PARTNER_MATCH_METHODS, '(null)') AS PARTNER_MATCH_METHODS,
  COUNT(*) AS ROW_COUNT,
  SUM(IFF(OUTCOME_FLAG='Clear',1,0)) AS CLEAR_ROWS,
  ROUND(100.0 * SUM(IFF(OUTCOME_FLAG='Clear',1,0)) / NULLIF(COUNT(*),0), 2) AS CLEAR_PCT
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.SENTINELONE_RECON_DETAIL
GROUP BY 1
ORDER BY ROW_COUNT DESC
""")
print(fetch_dataframe(q).to_string(index=False))
