"""Rebuild the unified reference maps (RECON_PARTNER_MAP + RECON_SKU_MAP + views).

This is a MANUAL step. STEP 0a used to run this on every pipeline execution,
but that was retired 2026-08-30 (static-maps directive) because a silent
dedup regression in the map build cascaded into 950+ Unmapped Partner rows
across the whole pipeline.

Run this script AFTER:
  * updating the partner map seed workbook (reloads THIRD_PARTY_RECON_PARTNER_MAP_PROD),
  * updating the SKU map seed workbook (reloads THIRD_PARTY_RECON_SKU_MAP_PROD),
  * updating RECON_VENDOR_PARTNER_MANUAL_MAP or RECON_ACCOUNT_MERGE_RESOLVER seed data.

Usage:
    .venv\\Scripts\\python.exe tools\\rebuild_recon_reference_maps.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "PROJECTS" / "Third_Party_Reconciliation" / "Combined_Recon_Prod_Pipeline" / "Reconciliation"))

from TEMPLATES.Python.connection import get_snowflake_connection
from _run_skeleton_pipeline import run_repo_sql_file


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    print("Rebuilding unified reference maps ...")
    ok = run_repo_sql_file(
        conn,
        r"Maps\sql\02_unified_reference_maps.sql",
        label="rebuild RECON_PARTNER_MAP + RECON_SKU_MAP + norm view",
    )
    if not ok:
        return 1

    cur = conn.cursor()
    cur.execute("""
        SELECT
            (SELECT COUNT(*) FROM RECON_PARTNER_MAP)                                              AS partner_map_rows,
            (SELECT COUNT(DISTINCT PARTNER_NAME_NORMALIZED) FROM RECON_PARTNER_MAP)               AS distinct_norm_keys,
            (SELECT COUNT(*) FROM RECON_PARTNER_MAP_MONTHLY WHERE billing_month = CURRENT_DATE()) AS monthly_rows_today,
            (SELECT COUNT(*) FROM RECON_SKU_MAP)                                                  AS sku_map_rows
    """)
    row = cur.fetchone()
    cols = [d[0] for d in cur.description]
    print()
    print("Post-build verification:")
    for c, v in zip(cols, row):
        print(f"  {c}: {v:,}")
    print()
    print("OK. Pipeline runs will now use this map version until the next manual rebuild.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
