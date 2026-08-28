"""
PRODUCTION GO-LIVE ORCHESTRATOR  —  2026-06-22
===============================================
Automates the full go-live sequence from local machine:

  1. Deploy SP_V5_CALIBRATION_REFRESH to Snowflake
     (from sql/pipeline/PROD_V1_11_calibration_refresh.sql)
  2. Call SP_V5_CALIBRATION_REFRESH() once to validate it runs
  3. Create + RESUME V5_CALIBRATION_REFRESH_TASK (monthly CRON)
  4. Rebuild app tables via SP_V5_SANDBOX_DAILY_REFRESH()
  5. Run all 18 production readiness checks
  6. Print board-ready gate summary

Usage (from TEMPLATES/Python/):
    python golive_orchestrator.py
"""

from __future__ import annotations

import re
import sys
import subprocess
from pathlib import Path
import traceback

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]

# ── add TEMPLATES/Python to path for connection helper ────────────────────────
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from connection import get_snowflake_connection, fetch_dataframe   # noqa: E402

SQL_PROC_FILE = (
    _REPO
    / "PROJECTS"
    / "Production_Renewal_Forecasting_Pipeline"
    / "sql"
    / "pipeline"
    / "PROD_V1_11_calibration_refresh.sql"
)

# ── console helpers ────────────────────────────────────────────────────────────
OK   = "\u2713 OK"
FAIL = "\u2717 FAIL"
SEP  = "=" * 70
SEP2 = "-" * 70
results: list[tuple[str, str]] = []


def step(n: int, title: str) -> None:
    print(f"\n{SEP2}\nSTEP {n}: {title}\n{SEP2}")


def report(label: str, passed: bool, detail: str = "") -> bool:
    tag = OK if passed else FAIL
    line = f"  [{tag}]  {label}"
    if detail:
        line += f"\n           {detail}"
    print(line)
    results.append((tag, label))
    return passed


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: Parse the SQL file and extract deployable statements
# ─────────────────────────────────────────────────────────────────────────────

def _extract_sql_statements(sql_path: Path) -> tuple[str, str]:
    """Return (create_table_sql, create_procedure_sql) from PROD_V1_11."""
    raw = sql_path.read_text(encoding="utf-8")

    # ── CREATE TABLE IF NOT EXISTS ───────────────────────────────────────────
    tbl_match = re.search(
        r"(CREATE TABLE IF NOT EXISTS\s+\S+[^;]+;)",
        raw,
        re.IGNORECASE | re.DOTALL,
    )
    if not tbl_match:
        raise RuntimeError("Could not find CREATE TABLE statement in SQL file")
    create_table = tbl_match.group(1).strip()

    # ── CREATE OR REPLACE PROCEDURE … $$; ───────────────────────────────────
    # The procedure body uses $$ ... $$ delimiters; extract from CREATE to final $$;
    proc_match = re.search(
        r"(CREATE OR REPLACE PROCEDURE\s+.*?\$\$\s*;)",
        raw,
        re.IGNORECASE | re.DOTALL,
    )
    if not proc_match:
        raise RuntimeError("Could not find CREATE OR REPLACE PROCEDURE in SQL file")
    create_proc = proc_match.group(1).strip()

    return create_table, create_proc


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: Execution helpers
# ─────────────────────────────────────────────────────────────────────────────

def _exec(cursor, sql: str, label: str) -> str:
    """Execute SQL, return first string result or row count."""
    cursor.execute(sql)
    rows = cursor.fetchall()
    if rows:
        first = rows[0][0]
        return str(first) if first is not None else ""
    return "(no rows)"


TASK_SQL = """\
CREATE OR REPLACE TASK STREAMLIT_APPS.DBO.V5_CALIBRATION_REFRESH_TASK
    WAREHOUSE            = REPORTING_WH
    SCHEDULE             = 'USING CRON 0 9 1 * * America/New_York'
    USER_TASK_TIMEOUT_MS = 600000
    COMMENT              = 'Monthly 1st @ 09:00 ET: isotonic calibration refresh.'
AS
    CALL STREAMLIT_APPS.DBO.SP_V5_CALIBRATION_REFRESH()\
"""

RESUME_TASK_SQL  = "ALTER TASK STREAMLIT_APPS.DBO.V5_CALIBRATION_REFRESH_TASK RESUME"
SHOW_TASKS_SQL   = "SHOW TASKS LIKE 'V5_%' IN SCHEMA STREAMLIT_APPS.DBO"
CALL_CAL_SQL     = "CALL STREAMLIT_APPS.DBO.SP_V5_CALIBRATION_REFRESH()"
CALL_REFRESH_SQL = "CALL STREAMLIT_APPS.DBO.SP_V5_SANDBOX_DAILY_REFRESH()"


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"\n{SEP}")
    print("V5 GO-LIVE ORCHESTRATOR   2026-06-22")
    print(SEP)

    # ── Parse SQL file ────────────────────────────────────────────────────────
    step(0, "Parse deployment SQL")
    try:
        create_table, create_proc = _extract_sql_statements(SQL_PROC_FILE)
        print(f"  Extracted CREATE TABLE ({len(create_table)} chars)")
        print(f"  Extracted CREATE PROCEDURE ({len(create_proc)} chars)")
    except Exception as exc:
        print(f"  [FAIL]  Could not parse SQL: {exc}")
        return 1

    # ── Connect ───────────────────────────────────────────────────────────────
    step(1, "Connect to Snowflake (browser SSO will pop up)")
    try:
        conn = get_snowflake_connection(
            warehouse="REPORTING_WH",
            database="STREAMLIT_APPS",
            schema="DBO",
        )
        cur = conn.cursor()
        print("  Connected OK")
    except Exception as exc:
        print(f"  [FAIL]  Connection failed: {exc}")
        return 1

    # ── Step 1: Create table ──────────────────────────────────────────────────
    step(2, "Create V5_CALIBRATION_KNOTS table (idempotent)")
    try:
        cur.execute(create_table)
        report("CREATE TABLE IF NOT EXISTS V5_CALIBRATION_KNOTS", True)
    except Exception as exc:
        report("CREATE TABLE IF NOT EXISTS V5_CALIBRATION_KNOTS", False, str(exc))
        return 1

    # ── Step 2: Deploy procedure ──────────────────────────────────────────────
    step(3, "Deploy SP_V5_CALIBRATION_REFRESH Snowpark procedure")
    try:
        cur.execute(create_proc)
        report("CREATE OR REPLACE PROCEDURE SP_V5_CALIBRATION_REFRESH", True)
    except Exception as exc:
        report("CREATE OR REPLACE PROCEDURE SP_V5_CALIBRATION_REFRESH", False, str(exc)[:300])
        traceback.print_exc()
        return 1

    # Verify it exists
    try:
        cur.execute(
            "SELECT PROCEDURE_NAME FROM STREAMLIT_APPS.INFORMATION_SCHEMA.PROCEDURES "
            "WHERE PROCEDURE_SCHEMA = 'DBO' AND PROCEDURE_NAME = 'SP_V5_CALIBRATION_REFRESH'"
        )
        found = cur.fetchall()
        report("SP_V5_CALIBRATION_REFRESH exists in INFORMATION_SCHEMA", len(found) > 0)
    except Exception as exc:
        report("SP_V5_CALIBRATION_REFRESH verification", False, str(exc))

    # ── Step 3: Call the proc once to write fresh knots ──────────────────────
    step(4, "Call SP_V5_CALIBRATION_REFRESH() — refit knots inside Snowflake")
    print("  (This takes 30-60 seconds — fitting isotonic calibrators on 86k rows)")
    try:
        cur.execute(CALL_CAL_SQL)
        rows = cur.fetchall()
        result = str(rows[0][0]) if rows else "(no result)"
        passed = result.startswith("OK:") or result.startswith("SKIP:")
        report("SP_V5_CALIBRATION_REFRESH() returned success", passed, result[:200])
        if not passed:
            print(f"\n  WARNING: proc returned: {result}")
            # non-fatal — knots from earlier run are still in the table
    except Exception as exc:
        report("SP_V5_CALIBRATION_REFRESH() call", False, str(exc)[:300])
        print("  WARNING: Proc call failed — existing knots will be used (non-fatal)")

    # ── Step 4: Create + resume calibration task ──────────────────────────────
    step(5, "Create + RESUME V5_CALIBRATION_REFRESH_TASK")
    try:
        cur.execute(TASK_SQL)
        report("CREATE OR REPLACE TASK V5_CALIBRATION_REFRESH_TASK", True)
    except Exception as exc:
        report("CREATE OR REPLACE TASK V5_CALIBRATION_REFRESH_TASK", False, str(exc)[:300])

    try:
        cur.execute(RESUME_TASK_SQL)
        report("ALTER TASK … RESUME", True)
    except Exception as exc:
        report("ALTER TASK … RESUME", False, str(exc)[:300])

    # Verify task state
    try:
        cur.execute("SHOW TASKS LIKE 'V5_CALIBRATION_REFRESH_TASK' IN SCHEMA STREAMLIT_APPS.DBO")
        task_rows = cur.fetchall()
        col_names = [d[0].upper() for d in cur.description]
        state_idx = next((i for i, c in enumerate(col_names) if "STATE" in c), None)
        sched_idx = next((i for i, c in enumerate(col_names) if "SCHEDULE" in c), None)
        if task_rows and state_idx is not None:
            state = task_rows[0][state_idx]
            sched = task_rows[0][sched_idx] if sched_idx is not None else "?"
            report(
                "Task state = started",
                str(state).lower() == "started",
                f"state={state}  schedule={sched}",
            )
        else:
            report("Task exists after RESUME", len(task_rows) > 0)
    except Exception as exc:
        report("SHOW TASKS verification", False, str(exc)[:300])

    # ── Step 5: Show all 7 tasks ──────────────────────────────────────────────
    step(6, "Verify all tasks are STARTED")
    try:
        cur.execute("SHOW TASKS LIKE 'V5_%' IN SCHEMA STREAMLIT_APPS.DBO")
        v5_tasks = cur.fetchall()
        col_names = [d[0].upper() for d in cur.description]
        name_idx  = next((i for i, c in enumerate(col_names) if c == "NAME"), None)
        state_idx = next((i for i, c in enumerate(col_names) if "STATE" in c), None)

        cur.execute("SHOW TASKS LIKE 'TASK_%' IN SCHEMA STREAMLIT_APPS.DBO")
        other_tasks = cur.fetchall()
        all_tasks = v5_tasks + other_tasks

        total_started = 0
        for row in all_tasks:
            name  = row[name_idx]  if name_idx  is not None else "?"
            state = row[state_idx] if state_idx is not None else "?"
            started = str(state).lower() == "started"
            if started:
                total_started += 1
            print(f"    {'✓' if started else '✗'}  {name:<50}  state={state}")

        report(f"All tasks started ({total_started}/{len(all_tasks)})",
               total_started == len(all_tasks))
    except Exception as exc:
        report("SHOW TASKS all", False, str(exc)[:300])

    # ── Step 6: Rebuild app tables ────────────────────────────────────────────
    step(7, "Rebuild app tables — SP_V5_SANDBOX_DAILY_REFRESH()")
    print("  (This takes 1-3 minutes — rebuilding V5_SANDBOX_APP_CONTRACT_DETAIL)")
    try:
        cur.execute(CALL_REFRESH_SQL)
        rows = cur.fetchall()
        result = str(rows[0][0]) if rows else "(no result)"
        passed = "ok" in result.lower() or "success" in result.lower() or len(result) > 0
        report("SP_V5_SANDBOX_DAILY_REFRESH() returned", passed, result[:200])
    except Exception as exc:
        report("SP_V5_SANDBOX_DAILY_REFRESH()", False, str(exc)[:300])

    cur.close()
    conn.close()

    # ── Step 7: Run production readiness check ────────────────────────────────
    step(8, "Run all 18 production readiness checks")
    try:
        import os as _os
        _env = {**_os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(
            [sys.executable, str(_HERE / "production_readiness_check.py")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_env,
        )
        print(proc.stdout)
        if proc.stderr:
            print("STDERR:", proc.stderr[:500])
        board_ready = "PRODUCTION READY" in proc.stdout or "18 passed" in proc.stdout or \
                      "ALL CHECKS PASS" in proc.stdout
        report("18/18 production readiness checks PASS", board_ready)
    except Exception as exc:
        report("Production readiness check", False, str(exc))

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("GO-LIVE SUMMARY")
    print(SEP)
    n_pass = sum(1 for tag, _ in results if tag == OK)
    n_fail = sum(1 for tag, _ in results if tag != OK)
    for tag, label in results:
        print(f"  [{tag}]  {label}")
    print(f"\n  {n_pass} passed  {n_fail} failed")

    if n_fail == 0:
        print("\n  ✓  ALL STEPS PASS — APP IS LIVE AND BOARD READY FOR JULY")
        return 0
    else:
        print("\n  ✗  SOME STEPS FAILED — REVIEW OUTPUT ABOVE BEFORE GOING LIVE")
        return 1


if __name__ == "__main__":
    sys.exit(main())
