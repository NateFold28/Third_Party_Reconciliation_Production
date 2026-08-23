"""
Preserve STANDALONE reconciliation calibration work — dated Snowflake snapshots
+ parquet exports to the repo.

Rationale
---------
The 9 THIRD_PARTY_STANDALONE_RECON_DETAIL__<VENDOR> tables and the composite
THIRD_PARTY_STANDALONE_RECON_DETAIL_GENERIC were created in Snowflake on
2026-08-18 by a Python process that was never committed to git. That process
represents weeks of vendor-by-vendor calibration work: partner map cleanup,
SKU catalog reconciliation, contract-rate overlay, duplicate-billing detection,
and outcome-flag taxonomy tuning. Losing the input code means we can no longer
regenerate the STANDALONE tables from raw sources.

To make sure that calibration work can NEVER be lost:
    1.  Copy every STANDALONE table into a dated snapshot table:
            THIRD_PARTY_STANDALONE_RECON_DETAIL__<VENDOR>_SNAPSHOT_YYYYMMDD
        These are immutable references and are the acceptance targets for
        rebuilding each vendor's live SQL pipeline.

    2.  Export every STANDALONE table to parquet under
            snapshots/standalone_2026_08_21/<TABLE>.parquet
        and commit it to git. Even if a Snowflake schema is dropped, the
        parquet copy is a self-contained restore.

    3.  Verify snapshot row counts and $ totals match the originals exactly.

This script is idempotent: rerunning on the same day is a no-op after the
snapshot tables and parquet files exist.
"""
from __future__ import annotations
import sys, time
from pathlib import Path
from datetime import date

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

REPO = Path(r"C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline")
SNAPSHOT_DATE = date.today().strftime("%Y%m%d")
PARQUET_DIR = REPO / "snapshots" / f"standalone_{SNAPSHOT_DATE}"

USE = ("USE ROLE DEVELOPER; USE WAREHOUSE REPORTING_WH; "
       "USE DATABASE ANALYTICS_DEV; USE SCHEMA DBT_NFOLD_TRANSFORMATION;")

STANDALONE_TABLES = [
    "THIRD_PARTY_STANDALONE_RECON_DETAIL_GENERIC",
    "THIRD_PARTY_STANDALONE_RECON_DETAIL__ACRONIS",
    "THIRD_PARTY_STANDALONE_RECON_DETAIL__AUVIK",
    "THIRD_PARTY_STANDALONE_RECON_DETAIL__BITDEFENDER",
    "THIRD_PARTY_STANDALONE_RECON_DETAIL__ESET",
    "THIRD_PARTY_STANDALONE_RECON_DETAIL__EXIUM",
    "THIRD_PARTY_STANDALONE_RECON_DETAIL__KEEPIT",
    "THIRD_PARTY_STANDALONE_RECON_DETAIL__PROOFPOINT",
    "THIRD_PARTY_STANDALONE_RECON_DETAIL__SENTINELONE",
    "THIRD_PARTY_STANDALONE_RECON_DETAIL__WEBROOT",
]


def run(conn, sql: str) -> None:
    for cur in conn.execute_string(sql, return_cursors=True):
        try:
            cur.fetchall()
        except Exception:
            pass
    conn.commit()


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        PARQUET_DIR.mkdir(parents=True, exist_ok=True)
        report_lines = [
            f"# STANDALONE preservation snapshot — {SNAPSHOT_DATE}",
            "",
            "| Source table | Rows | Vendor $ | Bill $ | Snapshot table | Parquet |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]

        cur = conn.cursor()
        run(conn, USE)

        for src in STANDALONE_TABLES:
            snap_tbl = f"{src}_SNAPSHOT_{SNAPSHOT_DATE}"
            parquet_path = PARQUET_DIR / f"{src}.parquet"

            # 1. Row/amount summary on source.
            cur.execute(f"""
                SELECT
                    COUNT(*),
                    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 0),
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 0)
                FROM {src}
            """)
            src_rows, src_v_amt, src_b_amt = cur.fetchone()
            print(f"\n{src}: {src_rows:,} rows / vendor ${src_v_amt or 0:,} / bill ${src_b_amt or 0:,}")

            # 2. Snowflake immutable snapshot table (idempotent).
            snap_sql = f"""{USE}
                CREATE TABLE IF NOT EXISTS {snap_tbl} CLONE {src};
                COMMENT ON TABLE {snap_tbl} IS
                    'Immutable snapshot of {src} taken {SNAPSHOT_DATE} to preserve reconciliation calibration work. Do not modify.';
            """
            t0 = time.perf_counter()
            run(conn, snap_sql)
            print(f"  Snowflake snapshot ready in {time.perf_counter()-t0:.1f}s: {snap_tbl}")

            # 3. Verify snapshot row/amount match source.
            cur.execute(f"""
                SELECT
                    COUNT(*),
                    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 0),
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 0)
                FROM {snap_tbl}
            """)
            s_rows, s_v_amt, s_b_amt = cur.fetchone()
            assert (s_rows, s_v_amt, s_b_amt) == (src_rows, src_v_amt, src_b_amt), (
                f"SNAPSHOT MISMATCH for {src}: source=({src_rows},{src_v_amt},{src_b_amt}) "
                f"snapshot=({s_rows},{s_v_amt},{s_b_amt})"
            )
            print(f"  verified: snapshot matches source exactly")

            # 4. Parquet export.
            if parquet_path.exists():
                print(f"  parquet already exists, skipping: {parquet_path.name}")
            else:
                import pandas as pd
                cur.execute(f"SELECT * FROM {src}")
                cols = [d[0] for d in cur.description]
                data = cur.fetchall()
                df = pd.DataFrame(data, columns=cols)
                df.to_parquet(parquet_path, index=False, compression="snappy")
                print(f"  parquet written: {parquet_path.name} ({parquet_path.stat().st_size/1024:.0f} KB, {len(df):,} rows)")

            report_lines.append(
                f"| `{src}` | {src_rows:,} | ${src_v_amt or 0:,} | ${src_b_amt or 0:,} | "
                f"`{snap_tbl}` | `snapshots/standalone_{SNAPSHOT_DATE}/{src}.parquet` |"
            )

        # Write markdown ledger for repo record.
        ledger_path = PARQUET_DIR / "README.md"
        report_lines.extend([
            "",
            "## Restore instructions",
            "",
            "If a live STANDALONE table is accidentally dropped or corrupted:",
            "",
            "1. In Snowflake, clone from the immutable snapshot:",
            "   ```sql",
            "   CREATE OR REPLACE TABLE <SOURCE_TABLE> CLONE <SOURCE_TABLE>_SNAPSHOT_" + SNAPSHOT_DATE + ";",
            "   ```",
            "2. If the entire Snowflake schema is lost, restore from the parquet file:",
            "   ```python",
            "   import pandas as pd",
            "   df = pd.read_parquet('snapshots/standalone_" + SNAPSHOT_DATE + "/<TABLE>.parquet')",
            "   df.to_sql(<TABLE>, snowflake_engine, if_exists='replace', index=False)",
            "   ```",
            "",
            "These files represent the reconciliation calibration state as of the snapshot date.",
            "They are the acceptance target for rebuilding each vendor's SQL pipeline.",
        ])
        ledger_path.write_text("\n".join(report_lines), encoding="utf-8")
        print(f"\nLedger written: {ledger_path}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
