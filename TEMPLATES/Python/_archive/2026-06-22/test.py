#%%
import snowflake.connector
print("✓ snowflake.connector imported successfully")
 
conn = snowflake.connector.connect(
    user="nate.fold@connectwise.com",
    account="connectwise_ent.us-east-1",
    authenticator="externalbrowser",
    role="STREAMLIT_USER",
    warehouse="CORTEX_WH",
    insecure_mode=False  # Try False first, or set to True if behind corporate proxy
)
 
cursor = conn.cursor()
cursor.execute("SELECT * from STREAMLIT_APPS.DBO.ML_SANDBOX_BEHAVIOR_CLUSTERS limit 10;")
print(cursor.fetchall())

'''#%% 
import pandas as pd
import snowflake.connector

def run_query():
    conn = snowflake.connector.connect(...)
    
    query = """
    SELECT * FROM sales LIMIT 100
    """
    
    df = pd.read_sql(query, conn)
    return df

df = run_query()
print(df.head())'''
# %%
