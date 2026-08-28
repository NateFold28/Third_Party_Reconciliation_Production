"""
Rebuild V5 app tables with fixed ML_FORECAST (FINAL_DOLLARS instead of ML_DOLLARS)
and redeploy app. Runs the app-tables SQL directly since the fix is in the SQL file,
not in the stored proc definition.

Steps:
  1. Execute the fixed PROD_V1_3_app_tables.sql directly in Snowflake
  2. Run quick validation: confirm ML_FORECAST >= FINANCE_FORECAST for all rows
  3. Redeploy Streamlit app with updated exec_insights fix
"""
import sys, os, pathlib, tempfile, shutil
sys.stdout.reconfigure(encoding="utf-8")
os.environ["PYTHONIOENCODING"] = "utf-8"

from connection import get_snowflake_connection
import pandas as pd

conn = get_snowflake_connection()
print("Connected to Snowflake OK")

# ── Step 1: Run the app-tables SQL ────────────────────────────────────────────
SQL_PATH = pathlib.Path(r"C:\Users\Nate.Fold\projects\PROJECTS\Production_Renewal_Forecasting_Pipeline\sql\pipeline\PROD_V1_3_app_tables.sql")
print(f"\nSTEP 1: Rebuild app tables from {SQL_PATH.name}")
sql_text = SQL_PATH.read_text(encoding="utf-8")
cur = conn.cursor()

# Strategy: extract the CREATE OR REPLACE PROCEDURE block (from the keyword to
# the closing $$;) as a single string, then execute it in one shot so the
# Snowflake parser sees a complete procedure definition. Then CALL it.
import re

# Find the proc block: everything from "CREATE OR REPLACE PROCEDURE" to the
# final closing "$$;" (the last occurrence after the opening AS $$).
proc_start = sql_text.find("CREATE OR REPLACE PROCEDURE")
if proc_start == -1:
    print("  ERROR: could not find CREATE OR REPLACE PROCEDURE in SQL file")
    sys.exit(1)

# The proc body is delimited by $$ ... $$. Find the SECOND $$ after proc_start
# (first opens, second closes) and then advance past the trailing semicolon.
dollar_positions = [m.start() for m in re.finditer(r'\$\$', sql_text[proc_start:])]
if len(dollar_positions) < 2:
    print("  ERROR: could not locate $$ delimiters for proc body")
    sys.exit(1)
# dollar_positions[0] = opening $$, dollar_positions[1] = closing $$
closing_dollar_end = proc_start + dollar_positions[1] + 2  # past the $$
# Advance past the optional semicolon after $$
proc_end = closing_dollar_end
while proc_end < len(sql_text) and sql_text[proc_end] in (' ', '\n', '\r', ';'):
    if sql_text[proc_end] == ';':
        proc_end += 1
        break
    proc_end += 1

proc_sql = sql_text[proc_start:proc_end].strip()
print(f"  Extracted proc definition: {len(proc_sql)} chars")

# Deploy the proc definition
try:
    cur.execute(proc_sql)
    print("  [OK] SP_V5_BUILD_APP_TABLES_V5_SHADOW deployed")
except Exception as e:
    print(f"  [FAIL] Deploy proc: {e}")
    sys.exit(1)

# Also run the compat view (just before the proc, between STEP 1 and STEP 2 markers)
step1_start = sql_text.find("CREATE OR REPLACE VIEW STREAMLIT_APPS.DBO.V5_SANDBOX_FORECAST_COMPAT")
step2_start = sql_text.find("CREATE OR REPLACE PROCEDURE")
if step1_start != -1 and step1_start < step2_start:
    view_sql = sql_text[step1_start:step2_start].strip().rstrip(";").strip()
    # Strip any trailing comments between the view and the proc
    view_sql = re.split(r'--\s*={5,}', view_sql)[0].strip().rstrip(";").strip()
    try:
        cur.execute(view_sql)
        print("  [OK] V5_SANDBOX_FORECAST_COMPAT view updated")
    except Exception as e:
        print(f"  [WARN] View update skipped: {e}")

# Call the proc to rebuild all app tables
print("\n  Calling SP_V5_BUILD_APP_TABLES_V5_SHADOW() to rebuild all app tables...")
cur.execute("CALL STREAMLIT_APPS.DBO.SP_V5_BUILD_APP_TABLES_V5_SHADOW()")
proc_result = cur.fetchone()
result_str = proc_result[0] if proc_result else "(no result)"
print(f"  Proc result: {result_str}")
if "ERROR" in str(result_str).upper() or "FAIL" in str(result_str).upper():
    print("  [FAIL] Proc reported an error")
    sys.exit(1)
print("  [OK] App tables rebuilt")

# ── Step 2: Validate the fix ──────────────────────────────────────────────────
print("\nSTEP 2: Validate ML_FORECAST >= FINANCE_FORECAST")

q_check = """
SELECT
    COUNT(*) AS TOTAL_ROWS,
    SUM(CASE WHEN ML_FORECAST < FINANCE_FORECAST - 0.01 THEN 1 ELSE 0 END) AS VIOLATIONS,
    SUM(CASE WHEN RENEWAL_MONTH = '2026-06-01' AND ML_FORECAST < FINANCE_FORECAST - 0.01
             THEN 1 ELSE 0 END) AS JUNE_VIOLATIONS,
    ROUND(SUM(CASE WHEN RENEWAL_MONTH = '2026-06-01' THEN ML_FORECAST ELSE 0 END)
        / NULLIF(SUM(CASE WHEN RENEWAL_MONTH = '2026-06-01' THEN ATR ELSE 0 END), 0) * 100, 2)
        AS JUNE_ML_RATE,
    ROUND(SUM(CASE WHEN RENEWAL_MONTH = '2026-06-01' THEN FINANCE_FORECAST ELSE 0 END)
        / NULLIF(SUM(CASE WHEN RENEWAL_MONTH = '2026-06-01' THEN ATR ELSE 0 END), 0) * 100, 2)
        AS JUNE_FINANCE_RATE,
    ROUND(SUM(CASE WHEN RENEWAL_MONTH = '2026-07-01' THEN ML_FORECAST ELSE 0 END)
        / NULLIF(SUM(CASE WHEN RENEWAL_MONTH = '2026-07-01' THEN ATR ELSE 0 END), 0) * 100, 2)
        AS JULY_ML_RATE,
    ROUND(SUM(CASE WHEN RENEWAL_MONTH = '2026-07-01' THEN FINANCE_FORECAST ELSE 0 END)
        / NULLIF(SUM(CASE WHEN RENEWAL_MONTH = '2026-07-01' THEN ATR ELSE 0 END), 0) * 100, 2)
        AS JULY_FINANCE_RATE
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE COALESCE(ATR, 0) > 0
"""
df_check = pd.read_sql(q_check, conn)
print(df_check.to_string(index=False))

violations = int(df_check["VIOLATIONS"].iloc[0])
june_viol  = int(df_check["JUNE_VIOLATIONS"].iloc[0])
june_ml    = float(df_check["JUNE_ML_RATE"].iloc[0])
june_fin   = float(df_check["JUNE_FINANCE_RATE"].iloc[0])

if violations > 0:
    print(f"  [FAIL] {violations} rows still violate ML >= Finance invariant")
    sys.exit(1)
print(f"  [OK] 0 violations — ML_FORECAST >= FINANCE_FORECAST for all rows")
print(f"  June: ML {june_ml}% vs Finance {june_fin}% (gap: {june_ml-june_fin:.2f}pp) — should be +1-2pp")

# ── Step 3: Redeploy the app ──────────────────────────────────────────────────
print("\nSTEP 3: Redeploy Streamlit app with exec_insights fix")

APP_PY = pathlib.Path(r"C:\Users\Nate.Fold\projects\PROJECTS\Production_Renewal_Forecasting_Pipeline\streamlit\Production_Forecast_App_V2.py")
STAGE  = "STREAMLIT_APPS.DBO.RENEWALS_OUTLOOK_PROD_APP_STAGE"
APP_ID = "STREAMLIT_APPS.DBO.FPOHZEPPAB9O9KA7"
WH     = "CORTEX_WH"

PYPROJECT = """[tool.streamlit]
query_warehouse = "CORTEX_WH"

[build-system]
requires = ["streamlit"]
"""
tmpdir = tempfile.mkdtemp()
try:
    app_dest = pathlib.Path(tmpdir) / "streamlit_app.py"
    shutil.copy2(APP_PY, app_dest)
    pp_dest  = pathlib.Path(tmpdir) / "pyproject.toml"
    pp_dest.write_text(PYPROJECT, encoding="utf-8")

    def run_sql(sql, label):
        try:
            cur.execute(sql)
            row = cur.fetchone()
            print(f"  [OK] {label}: {row[0] if row else 'done'}")
            return True
        except Exception as e:
            print(f"  [FAIL] {label}: {e}")
            return False

    run_sql(f"CREATE STAGE IF NOT EXISTS {STAGE} DIRECTORY = (ENABLE = TRUE)", "Create stage")

    for fpath in [app_dest, pp_dest]:
        fpath_fwd = str(fpath).replace("\\", "/")
        cur.execute(f"PUT 'file://{fpath_fwd}' @{STAGE} OVERWRITE=TRUE AUTO_COMPRESS=FALSE")
        print(f"  [OK] Uploaded {fpath.name}")

    run_sql(
        f"CREATE OR REPLACE STREAMLIT {APP_ID} FROM @{STAGE} "
        f"MAIN_FILE = 'streamlit_app.py' QUERY_WAREHOUSE = '{WH}'",
        "Create streamlit"
    )
    run_sql(f"ALTER STREAMLIT {APP_ID} ADD LIVE VERSION FROM LAST", "Add live version")
    run_sql(f"DESCRIBE STREAMLIT {APP_ID}", "Describe app")

finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

print("\n" + "=" * 60)
print("REBUILD + REDEPLOY COMPLETE")
print("  ML_FORECAST now uses FINAL_DOLLARS (Stage-1 calibrated)")
print("  INVARIANT: ML >= Finance enforced for every row")
print("  Exec insights uses actual blend for mature/settled contracts")
print("  App live: Ctrl+Shift+R to hard-refresh")
print("=" * 60)
conn.close()
