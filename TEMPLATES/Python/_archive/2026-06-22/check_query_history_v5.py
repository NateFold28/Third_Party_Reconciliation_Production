import sys
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()
cur.execute("""
SELECT START_TIME, END_TIME, EXECUTION_STATUS, ERROR_MESSAGE, QUERY_TEXT
FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(
    END_TIME_RANGE_START=>DATEADD('hour', -2, CURRENT_TIMESTAMP()),
    END_TIME_RANGE_END=>CURRENT_TIMESTAMP(),
    RESULT_LIMIT=>50
))
WHERE QUERY_TEXT ILIKE '%SP_V5_SANDBOX_RUN_PIPELINE%'
   OR QUERY_TEXT ILIKE '%run_pipeline_e2e_and_verify%'
ORDER BY START_TIME DESC
""")
for r in cur.fetchall():
    print(f"{r[0]} | end={r[1]} | {r[2]} | {r[3]}")
    print(r[4][:220])
