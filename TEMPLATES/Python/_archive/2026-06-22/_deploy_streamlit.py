import sys, os
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection
conn = get_snowflake_connection()
cur = conn.cursor()

# List stage files for the DEVELOPMENT app (LO8PU71ZBTTI6DX9)
try:
    cur.execute("LIST @STREAMLIT_APPS.DBO.LO8PU71ZBTTI6DX9")
    print("Stage files:")
    for r in cur.fetchall():
        print(" ", r)
except Exception as e:
    print("LIST error:", e)

# Try PUT to upload the updated app
src = r'c:\Users\Nate.Fold\projects\PROJECTS\Production_Renewal_Forecasting_Pipeline\streamlit\Development_Forecast_App_V1.py'
try:
    result = conn.execute_string(
        f"PUT 'file://{src.replace(chr(92), '/')}' @STREAMLIT_APPS.DBO.LO8PU71ZBTTI6DX9/streamlit/ OVERWRITE=TRUE AUTO_COMPRESS=FALSE",
        remove_comments=False
    )
    print("PUT result:", result)
except Exception as e:
    print("PUT error:", e)

cur.close()
conn.close()
