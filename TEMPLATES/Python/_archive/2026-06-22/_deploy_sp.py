"""
Deploy updated SP_V5_BUILD_APP_TABLES_V5_SHADOW from the local SQL file,
then call it to rebuild sandbox tables with PRODUCT_GROUP grain fix.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()

SQL_FILE = Path(r'c:\Users\Nate.Fold\projects\PROJECTS\Production_Renewal_Forecasting_Pipeline\sql\modeling\sandbox_v5\03_app_tables_v5.sql')

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: Extract the CREATE OR REPLACE PROCEDURE block and deploy it
# Strategy: find the line starting "CREATE OR REPLACE PROCEDURE" and take
# everything through the closing "$$;" — this is the complete SP definition.
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("STEP 1 — Deploying updated SP definition from local file")
print(f"  File: {SQL_FILE}")
print("=" * 70)

lines = SQL_FILE.read_text(encoding="utf-8").splitlines()

# Find the start of the SP
sp_start = None
for i, line in enumerate(lines):
    if line.strip().startswith("CREATE OR REPLACE PROCEDURE"):
        sp_start = i
        break

if sp_start is None:
    print("ERROR: Could not find CREATE OR REPLACE PROCEDURE in file.")
    sys.exit(1)

# Find the closing $$; (end of the SP body)
sp_end = None
for i in range(sp_start + 1, len(lines)):
    if lines[i].strip() == "$$;":
        sp_end = i
        break

if sp_end is None:
    print("ERROR: Could not find closing $$; in file.")
    sys.exit(1)

sp_sql = "\n".join(lines[sp_start : sp_end + 1])
# Remove trailing ; so we can submit it cleanly
sp_sql_submit = sp_sql.rstrip().rstrip(";")

print(f"  SP block: lines {sp_start+1}–{sp_end+1} ({sp_end - sp_start + 1} lines)")
print(f"  First line: {lines[sp_start][:80]}")

try:
    cur.execute("USE WAREHOUSE CORTEX_WH")
    t0 = time.time()
    cur.execute(sp_sql_submit)
    elapsed = time.time() - t0
    print(f"  ✅ SP deployed successfully ({elapsed:.1f}s)")
except Exception as e:
    print(f"\n  ERROR deploying SP: {e}")
    print()
    print("  → Run the SQL file in Snowsight manually:")
    print(f"    File: {SQL_FILE}")
    print("    Then call: CALL STREAMLIT_APPS.DBO.SP_V5_BUILD_APP_TABLES_V5_SHADOW();")
    cur.close()
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: Call the SP to rebuild tables
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 70)
print("STEP 2 — Calling updated SP_V5_BUILD_APP_TABLES_V5_SHADOW()")
print("  This rebuilds sandbox tables. May take 2–5 minutes.")
print("=" * 70)

try:
    t0 = time.time()
    cur.execute("CALL STREAMLIT_APPS.DBO.SP_V5_BUILD_APP_TABLES_V5_SHADOW()")
    result = cur.fetchone()
    elapsed = time.time() - t0
    print(f"  SP returned: {result}")
    print(f"  Elapsed: {elapsed:.1f}s")
except Exception as e:
    print(f"  ERROR calling SP: {e}")
    cur.close()
    sys.exit(1)

cur.close()
print("\nDone — run _rebuild_and_validate_sandbox.py to validate parity.")

