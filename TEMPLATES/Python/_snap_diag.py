import sys, os
sys.stdout.reconfigure(encoding="utf-8")
from connection import get_snowflake_connection
import pandas as pd

conn = get_snowflake_connection()

# Backtest monthly aggregate
bt = pd.read_sql("""
SELECT RENEWAL_MONTH,
    ROUND(SUM(PREDICTED_RETAINED)/SUM(NULLIF(ATR,0))*100,2) AS PORT_ML_RATE,
    ROUND(SUM(PREDICTED_RETAINED_CONTRACT)/SUM(NULLIF(ATR,0))*100,2) AS CONTRACT_ML_RATE,
    ROUND(SUM(ACTUAL_RETAINED)/SUM(NULLIF(ATR,0))*100,2) AS ACTUAL_RATE,
    ROUND(SUM(ATR)/1e6,1) AS ATR_M
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_BACKTEST
GROUP BY 1 ORDER BY 1
""", conn)
print("=== BACKTEST MONTHLY AGGREGATE ===")
print(bt.to_string(index=False))

# All snapshot-related tables
cur = conn.cursor()
cur.execute("SHOW TABLES IN SCHEMA STREAMLIT_APPS.DBO")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
import pandas as pd
tbl_df = pd.DataFrame(rows, columns=cols)
snap_tbls = tbl_df[tbl_df["name"].str.contains("SNAPSHOT", case=False, na=False)]["name"].tolist()
print("\n=== SNAPSHOT-RELATED TABLES ===")
print(snap_tbls)

# Try the snapshot table the app uses
try:
    sn = pd.read_sql("SELECT * FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_FORECAST_SNAPSHOTS LIMIT 5", conn)
    print("\n=== V5_SANDBOX_APP_FORECAST_SNAPSHOTS COLS ===")
    print(sorted(sn.columns.tolist()))
    sn2 = pd.read_sql("""
        SELECT RENEWAL_MONTH, SNAPSHOT_DATE::DATE AS SNAP_DT,
               LAST_DAY(RENEWAL_MONTH) AS MONTH_END,
               (SNAPSHOT_DATE::DATE <= LAST_DAY(RENEWAL_MONTH)) AS PRE_CLOSE
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_FORECAST_SNAPSHOTS
        GROUP BY 1,2,3,4 ORDER BY 1 LIMIT 20
    """, conn)
    print(sn2.to_string(index=False))
except Exception as e:
    print("APP_FORECAST_SNAPSHOTS error:", e)

conn.close()
