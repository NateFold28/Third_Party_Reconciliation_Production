import sys; sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection()
df = fetch_dataframe("SELECT COLUMN_NAME FROM STREAMLIT_APPS.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='DBO' AND TABLE_NAME='V5_SANDBOX_APP_CONTRACT_DETAIL' ORDER BY ORDINAL_POSITION", conn=conn)
print(df.to_string(index=False))
conn.close()
