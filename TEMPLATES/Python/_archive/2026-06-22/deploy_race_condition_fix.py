"""
Deploy race-condition guard fix: HAVING COUNT(DISTINCT SEGMENT) >= 4
Reads the fixed SQL directly from the local pipeline SQL file.
"""

import sys, re
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

def run():
    conn = get_snowflake_connection()
    cur = conn.cursor()
    for sql in ["USE ROLE STREAMLIT_USER", "USE WAREHOUSE REPORTING_WH",
                "USE DATABASE STREAMLIT_APPS", "USE SCHEMA DBO"]:
        cur.execute(sql)
    print("✓ Connected\n")

    with open(r'PROJECTS\Production_Renewal_Forecasting_Pipeline\sql\pipeline\PROD_V1_3_app_tables.sql',
              encoding='utf-8') as f:
        full_sql = f.read()

    # Extract the compat view DDL only (ends at the first semicolon that closes it)
    view_match = re.search(
        r'(CREATE OR REPLACE VIEW STREAMLIT_APPS\.DBO\.V5_SANDBOX_FORECAST_COMPAT AS.*?'
        r'FROM STREAMLIT_APPS\.DBO\.ML_SANDBOX_V5_PREDICTIONS p\s*'
        r'JOIN latest_run lr ON lr\.RUN_ID = p\.RUN_ID)',
        full_sql, re.DOTALL
    )

    if view_match:
        view_ddl = view_match.group(1).strip().rstrip(';')
        # Confirm the guard is in there
        if 'HAVING COUNT(DISTINCT SEGMENT) >= 4' in view_ddl:
            print("Race-condition guard confirmed in view DDL ✓")
        else:
            print("⚠ Guard NOT found in extracted DDL — check regex")
        print("Deploying V5_SANDBOX_FORECAST_COMPAT...")
        cur.execute(view_ddl)
        print("  ✓ View deployed")
    else:
        print("  ⚠ Could not extract view DDL — run manually in Snowsight")

    # Smoke test
    cur.execute("""
        SELECT SOURCE_RUN_ID, COUNT(DISTINCT SEGMENT) AS segs, COUNT(*) AS rows
        FROM V5_SANDBOX_FORECAST_COMPAT GROUP BY SOURCE_RUN_ID
    """)
    for run_id, segs, n in cur.fetchall():
        status = "✓ COMPLETE RUN SELECTED" if segs >= 4 else "⚠ PARTIAL"
        print(f"  Compat view → {run_id}: {segs} segments, {n:,} rows  {status}")

    print("""
SP update (manual — paste in Snowsight):
  In SP_V5_BUILD_APP_TABLES_V5_SHADOW, change the v_run_id block to:
    SELECT RUN_ID INTO :v_run_id
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
    GROUP BY RUN_ID
    HAVING COUNT(DISTINCT SEGMENT) >= 4
    ORDER BY MAX(PREDICTION_TS) DESC
    LIMIT 1;
  (Local file already patched: sql/pipeline/PROD_V1_3_app_tables.sql)
""")
    conn.close()

if __name__ == '__main__':
    run()

