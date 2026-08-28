import sys; sys.path.insert(0,'.')
from TEMPLATES.Python.connection import get_snowflake_connection
conn = get_snowflake_connection(); cur = conn.cursor()
for s in ['USE ROLE STREAMLIT_USER','USE WAREHOUSE REPORTING_WH','USE DATABASE STREAMLIT_APPS','USE SCHEMA DBO']:
    cur.execute(s)
for tbl in ['V5_SANDBOX_APP_SHAP_DRIVERS','V5_SANDBOX_APP_SHAP_GLOBAL']:
    try:
        cur.execute(f'SELECT * FROM {tbl} LIMIT 3')
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print(f'\n{tbl}:\n  cols: {cols}')
        for r in rows: print(' ', r)
    except Exception as e:
        print(f'{tbl}: ERROR {e}')
conn.close()
