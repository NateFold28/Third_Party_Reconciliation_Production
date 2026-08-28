import sys, json
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()
run_id = 'V5_20260611_175205'

cur.execute(f"SELECT SEGMENT, COUNT(*), MIN(HORIZON), MAX(HORIZON) FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS WHERE RUN_ID = '{run_id}' AND SPLIT = 'SCORE' GROUP BY 1")
print("SCORE segments:")
for r in cur.fetchall(): print(r)

cur.execute(f"SELECT NOTES FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_MODEL_RUNS WHERE RUN_ID = '{run_id}' LIMIT 1")
rows = cur.fetchall()
if rows and rows[0][0]:
    notes = json.loads(rows[0][0])
    print("Board gates:", notes.get("board_gates"))
    print("Board ready:", notes.get("board_ready"))
    print("Avg MAE:", notes.get("avg_mae_pp"))
    print("Avg bias:", notes.get("avg_bias_pp"))
else:
    # Try model run log
    cur.execute(f"SELECT SEGMENT, NOTES FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_MODEL_RUNS WHERE RUN_ID = '{run_id}' ORDER BY SEGMENT")
    print("Per-segment notes:")
    for r in cur.fetchall():
        seg = r[0]
        notes2 = json.loads(r[1]) if r[1] else {}
        print(f"{seg}: mae={notes2.get('mae_pp')} bias={notes2.get('bias_pp')} board_ready={notes2.get('board_ready')} gates={notes2.get('board_gates')}")

cur.close()
conn.close()
