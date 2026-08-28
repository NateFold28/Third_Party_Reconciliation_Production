"""
_fix_snapshot_contract_cols.py
===============================
1. ALTER TABLE to add contract-grain columns to V5_APP_FORECAST_SNAPSHOTS
2. Redeploy SP_V5_SNAPSHOT_MONTHLY_FORECAST (includes contract join)
3. Redeploy SP_V5_SNAPSHOT_OPEN_RENEWALS (fix: :open_opp_total in VALUES clause)
4. Run SP_V5_SNAPSHOT_MONTHLY_FORECAST to backfill today
5. Validate rows populated
"""
from __future__ import annotations
import re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_snowflake_connection, fetch_dataframe  # noqa: E402

conn = get_snowflake_connection()
cur  = conn.cursor()
for stmt in [
    "USE WAREHOUSE REPORTING_WH",
    "USE DATABASE STREAMLIT_APPS",
    "USE SCHEMA DBO",
]:
    cur.execute(stmt)

SEP = "\n" + "=" * 70

# ---------------------------------------------------------------------------
# Step 1 — ALTER TABLE: add contract-grain columns (idempotent)
# ---------------------------------------------------------------------------
print(f"{SEP}\nSTEP 1: ALTER TABLE — add missing columns to both snapshot tables")
alters = [
    # V5_APP_FORECAST_SNAPSHOTS — portfolio monthly rollup
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS LOW_MODEL_FORECAST FLOAT",
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS MEDIUM_MODEL_FORECAST FLOAT",
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS HIGH_MODEL_FORECAST FLOAT",
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS MANUAL_FORECAST_DOLLARS FLOAT",
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS LOW_MODEL_RATE_PCT FLOAT",
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS MEDIUM_MODEL_RATE_PCT FLOAT",
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS HIGH_MODEL_RATE_PCT FLOAT",
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS CONTRACT_ATR FLOAT",
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS CONTRACT_RENEWED FLOAT",
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS CONTRACT_ACTUAL_PCT FLOAT",
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS CONTRACT_FORECAST_RATE_PCT FLOAT",
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS NETTING_PP FLOAT",
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS ADD COLUMN IF NOT EXISTS BLENDED_NETTING_PP FLOAT",
    # V5_APP_CONTRACT_SNAPSHOTS — contract-level daily snapshot
    "ALTER TABLE STREAMLIT_APPS.DBO.V5_APP_CONTRACT_SNAPSHOTS ADD COLUMN IF NOT EXISTS MANUAL_FORECAST FLOAT",
]
for a in alters:
    cur.execute(a)
    print(f"  OK: {a[:90]}")

# ---------------------------------------------------------------------------
# Step 2 — Redeploy SP_V5_SNAPSHOT_MONTHLY_FORECAST (contract join)
# ---------------------------------------------------------------------------
print(f"{SEP}\nSTEP 2: Redeploy SP_V5_SNAPSHOT_MONTHLY_FORECAST")
base = Path(__file__).resolve().parents[2]
sql_path = base / "PROJECTS" / "Production_Renewal_Forecasting_Pipeline" / "sql" / "pipeline" / "03_snapshot_monthly_rollup.sql"
full_sql = sql_path.read_text(encoding="utf-8")

# Extract the CREATE OR REPLACE PROCEDURE block (ends with END;\n$$;)
proc_match = re.search(
    r"(CREATE OR REPLACE PROCEDURE STREAMLIT_APPS\.DBO\.SP_V5_SNAPSHOT_MONTHLY_FORECAST\(\)"
    r".*?END;\s*\$\$;)",
    full_sql,
    re.DOTALL,
)
if not proc_match:
    print("ERROR: Could not find SP_V5_SNAPSHOT_MONTHLY_FORECAST in SQL file")
    sys.exit(1)

proc_ddl = proc_match.group(1)
print(f"  Deploying ({len(proc_ddl):,} chars)...")
cur.execute(proc_ddl)
print("  DEPLOYED OK")

# ---------------------------------------------------------------------------
# Step 3 — Redeploy SP_V5_SNAPSHOT_OPEN_RENEWALS (VALUES bug fix)
# ---------------------------------------------------------------------------
print(f"{SEP}\nSTEP 3: Redeploy SP_V5_SNAPSHOT_OPEN_RENEWALS (VALUES clause bug fix)")
sql_path2 = base / "PROJECTS" / "Production_Renewal_Forecasting_Pipeline" / "sql" / "pipeline" / "04_snapshot_open_renewals_eom.sql"
full_sql2 = sql_path2.read_text(encoding="utf-8")

proc_match2 = re.search(
    r"(CREATE OR REPLACE PROCEDURE STREAMLIT_APPS\.DBO\.SP_V5_SNAPSHOT_OPEN_RENEWALS\(\)"
    r".*?END;\s*\$\$;)",
    full_sql2,
    re.DOTALL,
)
if not proc_match2:
    print("ERROR: Could not find SP_V5_SNAPSHOT_OPEN_RENEWALS in SQL file")
    sys.exit(1)

proc_ddl2 = proc_match2.group(1)
print(f"  Deploying ({len(proc_ddl2):,} chars)...")
cur.execute(proc_ddl2)
print("  DEPLOYED OK")

# ---------------------------------------------------------------------------
# Step 4 — Run SP_V5_SNAPSHOT_MONTHLY_FORECAST to backfill today's snapshot
# ---------------------------------------------------------------------------
print(f"{SEP}\nSTEP 4: Run SP_V5_SNAPSHOT_MONTHLY_FORECAST (backfill today)")
t0 = time.time()
cur.execute("CALL STREAMLIT_APPS.DBO.SP_V5_SNAPSHOT_MONTHLY_FORECAST()")
result = cur.fetchone()
elapsed = time.time() - t0
print(f"  Result: {result[0]}")
print(f"  Elapsed: {elapsed:.1f}s")

# ---------------------------------------------------------------------------
# Step 5 — Test SP_V5_SNAPSHOT_OPEN_RENEWALS (should return SKIPPED cleanly)
# ---------------------------------------------------------------------------
print(f"{SEP}\nSTEP 5: Test SP_V5_SNAPSHOT_OPEN_RENEWALS (expect SKIPPED, no ERROR)")
cur.execute("CALL STREAMLIT_APPS.DBO.SP_V5_SNAPSHOT_OPEN_RENEWALS()")
result2 = cur.fetchone()
print(f"  Result: {result2[0]}")

# ---------------------------------------------------------------------------
# Step 6 — Validate
# ---------------------------------------------------------------------------
print(f"{SEP}\nSTEP 6: Validate CONTRACT_ACTUAL_PCT populated")
import pandas as pd
df = fetch_dataframe("""
    SELECT RENEWAL_MONTH, ACTUAL_PCT, CONTRACT_ACTUAL_PCT, NETTING_PP, BLENDED_NETTING_PP
    FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS
    WHERE SNAPSHOT_DATE = CURRENT_DATE()
      AND CONTRACT_ACTUAL_PCT IS NOT NULL
    ORDER BY RENEWAL_MONTH
    LIMIT 15
""", conn=conn)
print(f"  Rows with CONTRACT_ACTUAL_PCT today: {len(df)}")
if not df.empty:
    print(df.to_string(index=False))
else:
    # Show all today's rows to diagnose
    df2 = fetch_dataframe("""
        SELECT RENEWAL_MONTH, ACTUAL_PCT, CONTRACT_ACTUAL_PCT, NETTING_PP
        FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS
        WHERE SNAPSHOT_DATE = CURRENT_DATE()
        ORDER BY RENEWAL_MONTH DESC LIMIT 10
    """, conn=conn)
    print("  All today's rows (first 10):")
    print(df2.to_string(index=False))

# Also check the latest view
print()
df3 = fetch_dataframe("""
    SELECT RENEWAL_MONTH, MODEL_RATE_PCT, MANUAL_ADJUSTED_PCT, ACTUAL_PCT,
           CONTRACT_ACTUAL_PCT, NETTING_PP
    FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOT_LATEST
    WHERE CONTRACT_ACTUAL_PCT IS NOT NULL
    ORDER BY RENEWAL_MONTH
    LIMIT 12
""", conn=conn)
print(f"  V5_APP_FORECAST_SNAPSHOT_LATEST rows with CONTRACT_ACTUAL_PCT: {len(df3)}")
if not df3.empty:
    print(df3.to_string(index=False))

print(f"\n{'='*70}\nALL STEPS COMPLETE\n{'='*70}")
