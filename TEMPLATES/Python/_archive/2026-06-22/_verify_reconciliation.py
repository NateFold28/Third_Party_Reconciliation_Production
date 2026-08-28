import sys; sys.path.insert(0,'.')
from TEMPLATES.Python.connection import get_snowflake_connection
conn = get_snowflake_connection()
cur = conn.cursor()
for s in ['USE ROLE STREAMLIT_USER','USE WAREHOUSE REPORTING_WH','USE DATABASE STREAMLIT_APPS','USE SCHEMA DBO']:
    cur.execute(s)

cur.execute('SELECT SOURCE_RUN_ID, COUNT(DISTINCT SEGMENT) AS segs, COUNT(*) AS row_count FROM V5_SANDBOX_FORECAST_COMPAT GROUP BY SOURCE_RUN_ID')
for r in cur.fetchall(): print('Compat view:', r)

cur.execute('SELECT COUNT(*) AS total, SUM(CASE WHEN IS_MATURE THEN 1 ELSE 0 END) AS matured FROM V5_SANDBOX_APP_CONTRACT_RECONCILIATION')
for r in cur.fetchall(): print('Reconciliation table rows:', r)

cur.execute("""
SELECT RENEWAL_MONTH, ROUND(AVG(NETTING_PP),2) AS avg_netting
FROM V5_SANDBOX_APP_CONTRACT_RECONCILIATION
WHERE RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
  AND RENEWAL_MONTH >= DATEADD('MONTH', -6, DATE_TRUNC('MONTH', CURRENT_DATE()))
  AND NETTING_PP IS NOT NULL
GROUP BY RENEWAL_MONTH ORDER BY RENEWAL_MONTH
""")
print('Netting per month (trailing 6m):')
for r in cur.fetchall(): print(' ', r)
conn.close()
print('done')
