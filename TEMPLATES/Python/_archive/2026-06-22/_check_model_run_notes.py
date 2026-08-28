"""Quick check of model run notes — halflife, debias, calibration shifts."""
import sys, json
sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection()
q = """
SELECT * FROM (
    SELECT * FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_MODEL_RUNS
    ORDER BY 1 DESC LIMIT 3
)
"""
df = fetch_dataframe(q, conn=conn)
for _, row in df.iterrows():
    print(f"\n--- {row['RUN_ID']}  forecast_rate={row['FORECAST_RATE_PCT']}% ---")
    try:
        notes = json.loads(row['NOTES'])
        for k in ['halflife_by_segment', 'global_fallback_rate', 'small_volatile_segs', 'portfolio_calibration']:
            if k in notes:
                print(f"  {k}: {notes[k]}")
    except Exception as ex:
        print(f"  NOTES parse error: {ex}")
        print(f"  NOTES raw: {str(row['NOTES'])[:600]}")

conn.close()
