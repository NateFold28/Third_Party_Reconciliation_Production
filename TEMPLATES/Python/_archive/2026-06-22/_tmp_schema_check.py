import sys; sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection()
for tbl in ['V5_SANDBOX_APP_CONTRACT_DETAIL', 'V5_SANDBOX_APP_SHAP_DRIVERS']:
    q = "SELECT COLUMN_NAME FROM STREAMLIT_APPS.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='DBO' AND TABLE_NAME='" + tbl + "' ORDER BY ORDINAL_POSITION"
    df = fetch_dataframe(q, conn=conn)
    print('--- ' + tbl + ' ---')
    print(df['COLUMN_NAME'].tolist())
