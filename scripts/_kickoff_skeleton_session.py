"""
Fresh-session kickoff for the recon skeleton rebuild.

Run this FIRST in a fresh chat to:
  1. Print the handoff doc so the new session has full context
  2. Print current state of Snowflake tables (what exists, what's empty)
  3. Print current OUTCOME_FLAG distribution in DETAIL_PROD
  4. Print current EXCEPTION_TYPE distribution in OUTPUT_PROD (the 14 buckets)

Read-only. Nothing is modified.

Usage from a fresh session:
    C:\\Users\\Nate.Fold\\projects\\.venv\\Scripts\\python.exe ^
        PROJECTS\\Third_Party_Reconciliation\\Combined_Recon_Prod_Pipeline\\scripts\\_kickoff_skeleton_session.py
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, r"C:\Users\Nate.Fold\projects\TEMPLATES\Python")

from connection import get_snowflake_connection  # type: ignore  # noqa: E402


BANNER = "=" * 80


def _print_header(title: str) -> None:
    print(f"\n{BANNER}\n  {title}\n{BANNER}")


def _fetchall(cur, sql: str):
    cur.execute(sql)
    return cur.fetchall()


def main() -> int:
    # ── 1. Handoff doc pointer ────────────────────────────────────────────
    _print_header("HANDOFF")
    print("""
  Load these into context BEFORE any tool call:
    /memories/session/HANDOFF_recon_skeleton_rebuild.md   <- primary plan
    /memories/repo/vendor_recon_rebuild_2026_08_23.md    <- architecture + acceptance
    scripts/build_third_party_recon_output_prod.py       <- classifier (DO NOT edit)

  User directive (locked, do not question):
    * Vendor SQLs write directly to THIRD_PARTY_RECON_DETAIL_PROD.
    * No STANDALONE reads. No TRANSLATIONS dict. No union step. No <VENDOR>_RECON_DETAIL persistent tables.
    * 14-bucket EXCEPTION_TYPE flags in the app are correct - keep as-is.
    * Skeleton first, fine-tune per-vendor after.
""".rstrip())

    # ── 2. Snowflake state ────────────────────────────────────────────────
    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        cur = conn.cursor()

        _print_header("STATE: canonical output tables")
        for t in (
            "THIRD_PARTY_RECON_VENDOR_USAGE_PROD",
            "THIRD_PARTY_RECON_DETAIL_PROD",
            "THIRD_PARTY_RECON_OUTPUT_PROD",
            "THIRD_PARTY_RECON_SUMMARY",
        ):
            try:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                n = cur.fetchone()[0]
                print(f"  {t:<45} rows: {n:>10,}")
            except Exception as exc:
                print(f"  {t:<45} MISSING ({exc})")

        _print_header("STATE: STANDALONE snapshots (Phase 0 preservation)")
        rows = _fetchall(
            cur,
            """
            SELECT TABLE_NAME
            FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = 'DBT_NFOLD_TRANSFORMATION'
              AND TABLE_NAME LIKE 'THIRD_PARTY_STANDALONE_RECON_DETAIL%SNAPSHOT_20260823'
            ORDER BY TABLE_NAME
            """,
        )
        for (name,) in rows:
            print(f"  {name}")
        if not rows:
            print("  WARN: no snapshots found (unexpected - Phase 0 committed 2026-08-23)")

        _print_header("STATE: DETAIL_PROD rows per vendor (current baseline)")
        rows = _fetchall(
            cur,
            """
            SELECT VENDOR, COUNT(*) AS row_count,
                   ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 0) AS vendor_dollars,
                   ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 0) AS billing_dollars
            FROM THIRD_PARTY_RECON_DETAIL_PROD
            GROUP BY 1
            ORDER BY 2 DESC
            """,
        )
        print(f"  {'VENDOR':<15} {'ROWS':>10} {'VENDOR $':>15} {'BILLING $':>15}")
        print(f"  {'-'*15} {'-'*10} {'-'*15} {'-'*15}")
        for v, n, va, ba in rows:
            print(f"  {v:<15} {n:>10,} ${(va or 0):>14,.0f} ${(ba or 0):>14,.0f}")

        _print_header("STATE: OUTCOME_FLAG distribution in DETAIL_PROD (what vendors emit)")
        rows = _fetchall(
            cur,
            """
            SELECT OUTCOME_FLAG, COUNT(*) AS n
            FROM THIRD_PARTY_RECON_DETAIL_PROD
            GROUP BY 1 ORDER BY 2 DESC
            """,
        )
        for f, n in rows:
            print(f"  {str(f):<50} {n:>8,}")

        _print_header("STATE: EXCEPTION_TYPE distribution in OUTPUT_PROD (14 app buckets)")
        rows = _fetchall(
            cur,
            """
            SELECT EXCEPTION_TYPE, COUNT(*) AS n,
                   ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 0) AS dollar_impact
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            GROUP BY 1 ORDER BY 2 DESC
            """,
        )
        print(f"  {'BUCKET':<50} {'ROWS':>8} {'$ IMPACT':>15}")
        print(f"  {'-'*50} {'-'*8} {'-'*15}")
        for f, n, d in rows:
            print(f"  {str(f):<50} {n:>8,} ${(d or 0):>14,.0f}")

        _print_header("STATE: uncommitted files in repo")

    finally:
        conn.close()

    # ── 3. Uncommitted file list ──────────────────────────────────────────
    import subprocess
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(REPO), capture_output=True, text=True, timeout=10,
        )
        print(result.stdout or "  (clean)")
    except Exception as exc:
        print(f"  git status failed: {exc}")

    _print_header("NEXT ACTIONS (in order)")
    print("""
  1. Read /memories/session/HANDOFF_recon_skeleton_rebuild.md fully
  2. Refactor scripts/_run_reports.py:
       - remove STANDALONE_VENDOR_TABLES, standalone_insert, TRANSLATIONS
       - remove STEP 1e (normalize) - vendor SQLs emit canonical directly
       - keep STEP 0/0b/0c/1c3/1c3b/1c4/1c5/1d2/3/4
       - insert a new STEP 1d that runs each vendor SQL file directly
       - patch 1c3/1c4/1c5 to update THIRD_PARTY_RECON_DETAIL_PROD WHERE VENDOR=<X>
         instead of updating <VENDOR>_RECON_DETAIL_PROD (which no longer exists)
  3. For each of 9 vendor SQL files:
       - change persistent CREATE OR REPLACE TABLE <V>_RECON_DETAIL
         to CREATE OR REPLACE TEMPORARY TABLE <V>_RECON_DETAIL
       - delete <V>_RECON_SUMMARY block and any _RAW_PARTNER_COVERAGE etc.
       - append canonical emit tail: DELETE + INSERT INTO THIRD_PARTY_RECON_DETAIL_PROD
       - handle vendors whose OUTCOME_FLAG is composite (Auvik/Bitdefender/ESET):
         use BASE_OUTCOME_FLAG column and map to the 12 canonical values
  4. For blocked vendors (Exium, SentinelOne, Webroot):
       - if V5 tables still missing, add a stub INSERT of one PIPELINE_NOT_YET_WIRED row
         so the app shows the vendor exists but flags it as unwired
  5. Run:
       cd PROJECTS\\Third_Party_Reconciliation\\Combined_Recon_Prod_Pipeline
       python scripts\\_run_reports.py 2>&1 | Tee-Object logs\\skeleton_run_2026_08_23.txt
  6. Verify OUTPUT_PROD shows a flag distribution across all vendors that ran
  7. Delete stale exploratory scripts (list in HANDOFF doc)
  8. Write docs/ARCHITECTURE.md
  9. Commit as 'Phase 1: Skeleton wired - vendor SQLs write direct to DETAIL_PROD'
""".rstrip())
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
