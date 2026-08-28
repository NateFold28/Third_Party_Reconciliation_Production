import sys, os
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection
conn = get_snowflake_connection()
cur = conn.cursor()

src = r'c:\Users\Nate.Fold\projects\PROJECTS\Production_Renewal_Forecasting_Pipeline\streamlit\Development_Forecast_App_V1.py'
src_unix = src.replace('\\', '/')

# Try the stage with quoted identifiers and /streamlit/ subpath
attempts = [
    f"PUT 'file://{src_unix}' @\"STREAMLIT_APPS\".\"DBO\".\"LO8PU71ZBTTI6DX9\" OVERWRITE=TRUE AUTO_COMPRESS=FALSE",
    f"PUT 'file://{src_unix}' @STREAMLIT_APPS.DBO.\"LO8PU71ZBTTI6DX9\" OVERWRITE=TRUE AUTO_COMPRESS=FALSE",
]
for stmt in attempts:
    try:
        cur.execute(stmt)
        print("SUCCESS:", stmt[:80])
        print("Result:", cur.fetchall())
        break
    except Exception as e:
        print(f"FAIL ({stmt[:60]}): {str(e)[:200]}")

cur.close()
conn.close()
