from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
PIPELINE_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PROJECT_ROOT))

from TEMPLATES.Python.connection import get_snowflake_connection


def execute_string(conn, sql: str, label: str) -> None:
    start = time.perf_counter()
    print(f"## {label}")
    for cur in conn.execute_string(sql, return_cursors=True):
        try:
            cur.fetchall()
        except Exception:
            pass
    conn.commit()
    print(f"OK {label} ({time.perf_counter() - start:.1f}s)")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        keepit_sql = (PIPELINE_ROOT / "Reconciliation" / "KeepIT_Reconciliation_Script_Prod.sql").read_text()
        execute_string(conn, keepit_sql, "KEEPIT_RECON_DETAIL + KEEPIT_RECON_SUMMARY")

        skeleton = load_module(PIPELINE_ROOT / "Reconciliation" / "_run_skeleton_pipeline.py", "skeleton")
        execute_string(conn, skeleton.live_emit_block("KeepIT", "KEEPIT_RECON_DETAIL"), "emit KeepIT into DETAIL_PROD")

        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                billing_month,
                source_family,
                total_rows,
                perfect_match_pct,
                actionable_clear_pct,
                abs_qty_variance,
                total_vendor_seats,
                total_billing_seats,
                no_billing_rows,
                billing_only_rows,
                unmapped_rows
            FROM keepit_recon_summary
            ORDER BY 1, 2
            """
        )
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        print("## KeepIT summary after patch")
        print(" | ".join(cols))
        for row in rows:
            print(" | ".join("" if value is None else str(value) for value in row))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
