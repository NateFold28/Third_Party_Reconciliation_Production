import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(role="DEVELOPER", warehouse="REPORTING_WH",
                                 database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION")
try:
    q = "SELECT GET_DDL('VIEW', 'ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.WEBROOT_TRT_ENDPOINT_RMM_DISCOUNT_MONTHLY') AS DDL"
    df = fetch_dataframe(q, conn=conn)
    ddl = df.iloc[0, 0]
    with open(r"c:/Users/Nate.Fold/projects/logs/webroot_rmm_discount_ddl.sql", "w", encoding="utf-8") as f:
        f.write(ddl)
    print("DDL length:", len(ddl))
    # Try again as a TABLE too, in case it's a table
    if not ddl or "SQL compilation" in ddl:
        q2 = "SELECT GET_DDL('TABLE', 'ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.WEBROOT_TRT_ENDPOINT_RMM_DISCOUNT_MONTHLY') AS DDL"
        df2 = fetch_dataframe(q2, conn=conn)
        print("Try as table:", df2.iloc[0, 0][:500])
finally:
    conn.close()
