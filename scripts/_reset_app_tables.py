"""Reset app-facing tables so the next full pipeline run rebuilds them fresh.

Drops the 4 tables the Streamlit app reads:

  * THIRD_PARTY_RECON_OUTPUT_PROD    (built by build_third_party_recon_output_prod.py)
  * THIRD_PARTY_RECON_SUMMARY        (built by build_third_party_recon_output_prod.py)
  * THIRD_PARTY_RECON_SUMMARY_PROD   (built by _run_reports.py STEP 4)
  * THIRD_PARTY_RECON_DETAIL_PROD    (built by _run_reports.py STEP 1d)

Does NOT touch upstream inputs:

  * THIRD_PARTY_RECON_VENDOR_USAGE_PROD          (fresh vendor usage)
  * THIRD_PARTY_RECON_SKU_MAP_PROD / _PARTNER_MAP_PROD  (curated seeds)
  * THIRD_PARTY_STANDALONE_RECON_DETAIL__<VENDOR>       (static intermediates)
  * THIRD_PARTY_RECON_SOURCE_*_PROD                     (unified billing sources)

Use to prove that the app tables are 100% rebuilt by this repo.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

USE = (
    "USE ROLE DEVELOPER; "
    "USE WAREHOUSE REPORTING_WH; "
    "USE DATABASE ANALYTICS_DEV; "
    "USE SCHEMA DBT_NFOLD_TRANSFORMATION;"
)

APP_TABLES = [
    "THIRD_PARTY_RECON_OUTPUT_PROD",
    "THIRD_PARTY_RECON_SUMMARY",
    "THIRD_PARTY_RECON_SUMMARY_PROD",
    "THIRD_PARTY_RECON_DETAIL_PROD",
]

# Upstream inputs the pipeline needs — must be present, must NOT be dropped.
REQUIRED_INPUTS = [
    "THIRD_PARTY_RECON_VENDOR_USAGE_PROD",
    "THIRD_PARTY_RECON_SKU_MAP_PROD",
    "THIRD_PARTY_RECON_PARTNER_MAP_PROD",
    "THIRD_PARTY_RECON_SOURCE_ZUORA_PROD",
    "THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD",
    "THIRD_PARTY_RECON_SOURCE_TRT_PROD",
    "THIRD_PARTY_RECON_SOURCE_ROYALTIES_PROD",
]


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        cur = conn.cursor()

        # Preflight: refuse to drop if upstream inputs are missing/empty.
        placeholders = ",".join(f"'{t}'" for t in REQUIRED_INPUTS)
        cur.execute(
            f"""
            SELECT TABLE_NAME, ROW_COUNT
            FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = 'DBT_NFOLD_TRANSFORMATION'
              AND TABLE_NAME IN ({placeholders})
            ORDER BY TABLE_NAME
            """
        )
        rows = {t: n for t, n in cur.fetchall()}
        missing = [t for t in REQUIRED_INPUTS if t not in rows]
        empty = [t for t, n in rows.items() if n == 0]
        if missing:
            print(f"ABORT: required inputs missing: {missing}")
            return 2
        if empty:
            print(f"ABORT: required inputs empty: {empty}")
            return 2
        print("Upstream inputs verified:")
        for t in REQUIRED_INPUTS:
            print(f"  {t:<48} {rows[t]:>12,} rows")

        # Before snapshot.
        placeholders = ",".join(f"'{t}'" for t in APP_TABLES)
        cur.execute(
            f"""
            SELECT TABLE_NAME, ROW_COUNT, LAST_ALTERED
            FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = 'DBT_NFOLD_TRANSFORMATION'
              AND TABLE_NAME IN ({placeholders})
            ORDER BY TABLE_NAME
            """
        )
        before = cur.fetchall()
        print("\nApp-facing tables BEFORE drop:")
        for name, rowcount, ts in before:
            print(f"  {name:<40} {rowcount:>10,} rows   last_altered={ts}")

        # Drop.
        print("\nDropping app-facing tables ...")
        for stmt in USE.split(";"):
            if stmt.strip():
                cur.execute(stmt)
        for tbl in APP_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {tbl}")
            print(f"  DROPPED  {tbl}")

        # After snapshot.
        cur.execute(
            f"""
            SELECT TABLE_NAME
            FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = 'DBT_NFOLD_TRANSFORMATION'
              AND TABLE_NAME IN ({placeholders})
            """
        )
        still_there = [r[0] for r in cur.fetchall()]
        if still_there:
            print(f"\nWARNING: still present after DROP: {still_there}")
            return 1
        print("\nAll 4 app-facing tables dropped cleanly. Ready for rebuild.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
