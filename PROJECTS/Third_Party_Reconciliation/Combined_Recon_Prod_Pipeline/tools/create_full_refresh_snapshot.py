"""Create local and Snowflake rollback anchors before a full recon refresh."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import snowflake.connector

REPO = Path(r"C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline")
PROJECT_ROOT = Path(r"C:\Users\Nate.Fold\projects")

import sys

sys.path.insert(0, str(PROJECT_ROOT))
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

DB = "ANALYTICS_DEV"
SCHEMA = "DBT_NFOLD_TRANSFORMATION"

CORE_OBJECTS = [
    "THIRD_PARTY_RECON_VENDOR_USAGE_PROD",
    "THIRD_PARTY_RECON_VENDOR_INVOICES",
    "THIRD_PARTY_RECON_PARTNER_MAP_PROD",
    "THIRD_PARTY_RECON_SKU_MAP_PROD",
    "RECON_PARTNER_MAP",
    "RECON_PARTNER_MAP_MONTHLY",
    "RECON_SKU_MAP",
    "RECON_ACCOUNT_MERGE_RESOLVER",
    "RECON_VENDOR_PARTNER_MANUAL_MAP",
    "THIRD_PARTY_RECON_DETAIL_PROD",
    "THIRD_PARTY_RECON_DETAIL_PROD_STAGING",
    "THIRD_PARTY_RECON_OUTPUT_PROD",
    "THIRD_PARTY_RECON_SUMMARY_PROD",
    "THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD",
]

VENDOR_OBJECTS = [
    "ACRONIS_RECON_DETAIL",
    "ACRONIS_RECON_SUMMARY",
    "AUVIK_RECON_DETAIL",
    "AUVIK_RECON_SUMMARY",
    "BITDEFENDER_RECON_DETAIL",
    "BITDEFENDER_RECON_SUMMARY",
    "ESET_RECON_DETAIL",
    "ESET_RECON_SUMMARY",
    "EXIUM_RECON_DETAIL",
    "EXIUM_RECON_SUMMARY",
    "KEEPIT_RECON_DETAIL",
    "KEEPIT_RECON_SUMMARY",
    "PROOFPOINT_RECON_DETAIL",
    "PROOFPOINT_RECON_SUMMARY",
    "SENTINELONE_RECON_DETAIL",
    "SENTINELONE_RECON_SUMMARY",
    "WEBROOT_RECON_DETAIL",
    "WEBROOT_RECON_DETAIL_APP",
    "WEBROOT_RECON_SUMMARY",
]

ARCHITECTURE_PATHS = [
    "README.md",
    "Ingestion",
    "Maps",
    "Reconciliation",
    "app",
    "App",
    "tools",
]


def run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.stdout


def copy_architecture_files(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "git_head.txt").write_text(run_git(["rev-parse", "HEAD"]), encoding="utf-8")
    (target / "git_status.txt").write_text(run_git(["status", "--short"]), encoding="utf-8")
    (target / "git_diff.patch").write_text(run_git(["diff", "--binary"]), encoding="utf-8")
    (target / "git_diff_cached.patch").write_text(run_git(["diff", "--cached", "--binary"]), encoding="utf-8")

    source_root = target / "source_files"
    for rel in ARCHITECTURE_PATHS:
        src = REPO / rel
        if not src.exists():
            continue
        dst = source_root / rel
        if src.is_dir():
            ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "output", "logs")
            shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def fq(name: str) -> str:
    return f"{DB}.{SCHEMA}.{name}"


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def create_snowflake_snapshot(snapshot_name: str, target: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database=DB,
        schema=SCHEMA,
    )
    try:
        with conn.cursor(snowflake.connector.DictCursor) as cur:
            cur.execute(
                """
                SELECT TABLE_NAME, TABLE_TYPE
                FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME IN ({})
                """.format(",".join(["%s"] * len(set(CORE_OBJECTS + VENDOR_OBJECTS)))),
                [SCHEMA, *sorted(set(CORE_OBJECTS + VENDOR_OBJECTS))],
            )
            objects = {r["TABLE_NAME"]: r["TABLE_TYPE"] for r in cur.fetchall()}

            for name in sorted(set(CORE_OBJECTS + VENDOR_OBJECTS)):
                object_type = objects.get(name)
                if not object_type:
                    rows.append({"source": name, "snapshot": "", "status": "missing", "type": ""})
                    continue
                snapshot_table = f"{name}__SNAPSHOT_{snapshot_name}"
                if object_type in {"BASE TABLE", "TRANSIENT TABLE"}:
                    cur.execute(f"CREATE OR REPLACE TABLE {quote_ident(snapshot_table)} CLONE {fq(name)}")
                    rows.append(
                        {
                            "source": name,
                            "snapshot": snapshot_table,
                            "status": "cloned",
                            "type": object_type,
                        }
                    )
                else:
                    ddl_dir = target / "snowflake_view_ddl"
                    ddl_dir.mkdir(parents=True, exist_ok=True)
                    cur.execute("SELECT GET_DDL(%s, %s)", ("VIEW", fq(name)))
                    ddl_row = cur.fetchone()
                    ddl = next(iter(ddl_row.values()))
                    (ddl_dir / f"{name}.sql").write_text(ddl, encoding="utf-8")
                    rows.append({"source": name, "snapshot": str(ddl_dir / f"{name}.sql"), "status": "ddl", "type": object_type})
        conn.commit()
    finally:
        conn.close()
    return rows


def main() -> int:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot_name = timestamp
    target = REPO / "output" / f"full_refresh_snapshot_{timestamp}"
    copy_architecture_files(target)

    snowflake_rows = create_snowflake_snapshot(snapshot_name, target)
    (target / "snowflake_table_snapshots.json").write_text(
        json.dumps(snowflake_rows, indent=2),
        encoding="utf-8",
    )
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "repo": str(REPO),
                "git_head": run_git(["rev-parse", "HEAD"]).strip(),
                "snowflake_database": DB,
                "snowflake_schema": SCHEMA,
                "snapshot_suffix": f"__SNAPSHOT_{snapshot_name}",
                "snapshot_dir": str(target),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Snapshot written to {target}")
    cloned = [r for r in snowflake_rows if r["status"] == "cloned"]
    missing = [r for r in snowflake_rows if r["status"] == "missing"]
    print(f"Snowflake tables cloned: {len(cloned)}")
    if missing:
        print(f"Snowflake objects missing/skipped: {len(missing)}")
        for row in missing:
            print(f"  missing: {row['source']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
