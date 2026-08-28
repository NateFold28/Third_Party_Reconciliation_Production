"""Check snapshot table columns and netting values."""
import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection()

# First discover columns
df0 = fetch_dataframe("SELECT * FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOT_LATEST LIMIT 1", conn=conn)
print("V5_APP_FORECAST_SNAPSHOT_LATEST columns:")
for c in sorted(df0.columns.tolist()):
    print(f"  {c}")
print()
print(df0.transpose().to_string())
