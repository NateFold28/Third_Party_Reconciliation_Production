"""
rebuild_recon_reference_maps.py — explicit rebuild of the governed reference-map layer.

Runs `Maps/sql/02_unified_reference_maps.sql` against Snowflake to refresh:
  - RECON_ACCOUNT_MERGE_RESOLVER
  - RECON_PARTNER_MAP
  - RECON_PARTNER_MAP_MONTHLY
  - RECON_SKU_MAP (VIEW — CREATE OR REPLACE is a no-op if unchanged)
  - V_RECON_PARTNER_MAP_MONTHLY_NORM
  - V_RECON_PRICEBOOK_TIER_LOOKUP

Run this AFTER editing any of the seed tables:
  - THIRD_PARTY_RECON_PARTNER_MAP_PROD
  - THIRD_PARTY_RECON_SKU_MAP_PROD
  - RECON_VENDOR_PARTNER_MANUAL_MAP
  - RECON_PRICEBOOK

Then rerun the skeleton pipeline so vendor SQL sees the refreshed governed layer.

This is intentionally decoupled from the pipeline. Every skeleton pipeline run
is idempotent against a STATIC governed layer, which prevents subtle regressions
in `02_unified_reference_maps.sql` from silently altering the mapping between runs.
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, r"C:\Users\Nate.Fold\projects")

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

USE = (
    "USE ROLE DEVELOPER; "
    "USE WAREHOUSE REPORTING_WH; "
    "USE DATABASE ANALYTICS_DEV; "
    "USE SCHEMA DBT_NFOLD_TRANSFORMATION;"
)

SQL_PATH = REPO / "Maps" / "sql" / "02_unified_reference_maps.sql"


def main() -> int:
    if not SQL_PATH.exists():
        print(f"ERROR: {SQL_PATH} not found", file=sys.stderr)
        return 1
    sql_body = SQL_PATH.read_text(encoding="utf-8")
    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        print(f"[rebuild] executing {SQL_PATH.relative_to(REPO)} ...")
        t0 = time.time()
        # Use Snowflake's execute_string to handle multi-statement SQL correctly.
        # Splitting on ";" is unreliable when semicolons appear inside string
        # literals or comments (e.g. the manual_partner_overrides CTE).
        with conn.cursor() as cur:
            cur.execute("USE ROLE DEVELOPER")
            cur.execute("USE WAREHOUSE REPORTING_WH")
            cur.execute("USE DATABASE ANALYTICS_DEV")
            cur.execute("USE SCHEMA DBT_NFOLD_TRANSFORMATION")
        conn.execute_string(sql_body)
        print(f"[rebuild] OK ({time.time() - t0:.1f}s)")

        # Post-rebuild row-count sanity report
        print("\n[rebuild] governed-layer row counts:")
        with conn.cursor() as cur:
            for obj in (
                "RECON_ACCOUNT_MERGE_RESOLVER",
                "RECON_PARTNER_MAP",
                "RECON_PARTNER_MAP_MONTHLY",
            ):
                cur.execute(f"SELECT COUNT(*) FROM {obj}")
                (n,) = cur.fetchone()
                print(f"  {obj:<38} {n:>10,} rows")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
