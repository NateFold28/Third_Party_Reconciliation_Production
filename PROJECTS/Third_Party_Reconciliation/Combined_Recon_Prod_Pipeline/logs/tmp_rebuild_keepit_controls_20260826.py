from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
PIPELINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from TEMPLATES.Python.connection import get_snowflake_connection


def execute_file(conn, path: Path, label: str) -> None:
    start = time.perf_counter()
    print(f"## {label}")
    for cur in conn.execute_string(path.read_text(encoding="utf-8"), return_cursors=True):
        try:
            cur.fetchall()
        except Exception:
            pass
    conn.commit()
    print(f"OK {label} ({time.perf_counter() - start:.1f}s)")


def main() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        execute_file(
            conn,
            PIPELINE_ROOT / "Reconciliation" / "10_vendor_invoice_usage_intra_prod.sql",
            "THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD",
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
