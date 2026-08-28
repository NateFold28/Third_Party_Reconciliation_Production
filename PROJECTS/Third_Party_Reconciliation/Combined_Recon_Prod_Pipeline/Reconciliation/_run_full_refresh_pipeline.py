"""Run a full fresh production refresh: ingestion -> invoices -> maps -> sources -> recon."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(r"C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline")
PROJECT_ROOT = Path(r"C:\Users\Nate.Fold\projects")
sys.path.insert(0, str(PROJECT_ROOT))

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

INGESTION_SCRIPTS = [
    r"Ingestion\Acronis_Vendor_Usage_Ingestion_Prod.py",
    r"Ingestion\Auvik_Vendor_Usage_Ingestion_Prod.py",
    r"Ingestion\Bitdefender_Vendor_Usage_Ingestion_Prod.py",
    r"Ingestion\ESET_Vendor_Usage_Ingestion_Prod.py",
    r"Ingestion\Exium_Vendor_Usage_Ingestion_Prod.py",
    r"Ingestion\KeepIT_Vendor_Usage_Ingestion_Prod.py",
    r"Ingestion\Proofpoint_Vendor_Usage_Ingestion_Prod.py",
    r"Ingestion\SentinelOne_Vendor_Usage_Ingestion_Prod.py",
    r"Ingestion\Webroot_Vendor_Usage_Ingestion_Prod.py",
]

INGESTION_EXTRA_ARGS: dict[str, list[str]] = {
    # These scripts require explicit overwrite semantics when rows already exist.
    "ESET_Vendor_Usage_Ingestion_Prod.py": ["--replace-month"],
    "Exium_Vendor_Usage_Ingestion_Prod.py": ["--replace-month"],
}

INGESTION_VENDOR_BY_SCRIPT: dict[str, str] = {
    "Acronis_Vendor_Usage_Ingestion_Prod.py": "Acronis",
    "Auvik_Vendor_Usage_Ingestion_Prod.py": "Auvik",
    "Bitdefender_Vendor_Usage_Ingestion_Prod.py": "Bitdefender",
    "ESET_Vendor_Usage_Ingestion_Prod.py": "ESET",
    "Exium_Vendor_Usage_Ingestion_Prod.py": "Exium",
    "KeepIT_Vendor_Usage_Ingestion_Prod.py": "KeepIT",
    "Proofpoint_Vendor_Usage_Ingestion_Prod.py": "Proofpoint",
    "SentinelOne_Vendor_Usage_Ingestion_Prod.py": "SentinelOne",
    "Webroot_Vendor_Usage_Ingestion_Prod.py": "Webroot",
}

STATE_FILE = REPO / "logs" / "full_refresh_state.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _signature(paths: list[Path], extra: str = "") -> str:
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: str(x).lower()):
        h.update(str(p).encode("utf-8"))
        h.update(_sha256_file(p).encode("utf-8"))
    if extra:
        h.update(extra.encode("utf-8"))
    return h.hexdigest()


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def run_cmd(cmd: list[str], label: str, cwd: Path) -> bool:
    t0 = time.perf_counter()
    print(f"\n=== {label} ===", flush=True)
    print(" ".join(cmd), flush=True)
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    root_path = str(PROJECT_ROOT)
    env["PYTHONPATH"] = root_path if not existing_pythonpath else f"{root_path};{existing_pythonpath}"
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line.rstrip(), flush=True)
    returncode = process.wait()
    if returncode != 0:
        print(f"FAILED in {time.perf_counter() - t0:.1f}s", flush=True)
        return False
    print(f"OK ({time.perf_counter() - t0:.1f}s)", flush=True)
    return True


def run_sql_file(path: Path, label: str) -> bool:
    t0 = time.perf_counter()
    print(f"\n=== {label} ===", flush=True)
    # Strip comment-only lines before statement splitting so semicolons inside
    # comments do not create invalid SQL fragments.
    raw_sql = path.read_text(encoding="utf-8")
    sql_text = "\n".join(
        ln for ln in raw_sql.splitlines()
        if not ln.lstrip().startswith("--")
    )
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        with conn.cursor() as cur:
            for stmt in sql_text.split(";"):
                s = stmt.strip()
                if not s:
                    continue
                cur.execute(s)
        conn.commit()
        print(f"OK ({time.perf_counter() - t0:.1f}s)", flush=True)
        return True
    except Exception as exc:
        print(f"FAILED ({time.perf_counter() - t0:.1f}s): {exc}", flush=True)
        return False
    finally:
        conn.close()


def delete_vendor_usage_rows(vendor: str) -> bool:
    t0 = time.perf_counter()
    print(f"\n=== Full-refresh preflight: clear vendor slice ({vendor}) ===", flush=True)
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD "
                "WHERE UPPER(COALESCE(VENDOR, '')) = UPPER(%s)",
                (vendor,),
            )
        conn.commit()
        print(f"OK ({time.perf_counter() - t0:.1f}s)", flush=True)
        return True
    except Exception as exc:
        print(f"FAILED ({time.perf_counter() - t0:.1f}s): {exc}", flush=True)
        return False
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-month", default="2026-01", help="Invoice parser lower bound YYYY-MM.")
    p.add_argument("--skip-ingestion", action="store_true", help="Skip vendor usage ingestion.")
    p.add_argument("--skip-invoices", action="store_true", help="Skip Netsuite invoice parsing.")
    p.add_argument("--skip-maps", action="store_true", help="Skip unified reference map rebuild.")
    p.add_argument("--skip-sources", action="store_true", help="Skip unified billing source rebuild.")
    p.add_argument("--skip-recon", action="store_true", help="Skip skeleton reconciliation rebuild.")
    p.add_argument("--force-ingestion", action="store_true", help="Force vendor usage ingestion even if smart-skip marks it unchanged.")
    p.add_argument("--force-invoices", action="store_true", help="Force invoice parsing even if smart-skip marks it unchanged.")
    p.add_argument("--disable-smart-skip", action="store_true", help="Disable automatic unchanged-step skipping for ingestion/invoices.")
    p.add_argument(
        "--full-refresh-now",
        action="store_true",
        help="Run a one-time full refresh now (vendor-by-vendor replacement in shared usage table, then rebuild invoices/maps/sources/recon).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    state = _load_state()

    full_refresh_now = args.full_refresh_now

    ingestion_paths = [REPO / rel for rel in INGESTION_SCRIPTS]
    ingestion_sig = _signature(ingestion_paths)

    invoice_script = REPO / "Ingestion" / "Netsuite_Invoice_JSON_Ingestion_Prod.py"
    invoice_sig = _signature([invoice_script], extra=f"from_month={args.from_month}")

    smart_skip_enabled = not args.disable_smart_skip and not full_refresh_now

    skip_ingestion_unchanged = (
        smart_skip_enabled
        and not args.force_ingestion
        and not args.skip_ingestion
        and state.get("ingestion_status") == "ok"
        and state.get("ingestion_signature") == ingestion_sig
    )

    skip_invoices_unchanged = (
        smart_skip_enabled
        and not args.force_invoices
        and not args.skip_invoices
        and state.get("invoice_status") == "ok"
        and state.get("invoice_signature") == invoice_sig
    )

    if skip_ingestion_unchanged:
        print("\n=== Ingestion refresh ===", flush=True)
        print("Skipped (smart-skip): ingestion scripts unchanged since last successful run.", flush=True)
    elif not args.skip_ingestion:
        for rel in INGESTION_SCRIPTS:
            script_name = Path(rel).name
            if full_refresh_now:
                vendor_name = INGESTION_VENDOR_BY_SCRIPT.get(script_name)
                if not vendor_name:
                    print(f"FAILED: no vendor mapping configured for {script_name}", flush=True)
                    state["ingestion_status"] = "failed"
                    _save_state(state)
                    return 1
                if not delete_vendor_usage_rows(vendor_name):
                    state["ingestion_status"] = "failed"
                    _save_state(state)
                    return 1

            cmd = [
                sys.executable,
                str(REPO / rel),
                "--all-months",
                *INGESTION_EXTRA_ARGS.get(script_name, []),
            ]
            if not run_cmd(cmd, f"Ingestion {Path(rel).name}", PROJECT_ROOT):
                state["ingestion_status"] = "failed"
                state["ingestion_signature"] = ingestion_sig
                _save_state(state)
                return 1
        state["ingestion_status"] = "ok"
        state["ingestion_signature"] = ingestion_sig
        state["ingestion_completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        _save_state(state)
    else:
        state["ingestion_status"] = "skipped_by_flag"
        _save_state(state)

    if skip_invoices_unchanged:
        print("\n=== Invoice parsing refresh ===", flush=True)
        print("Skipped (smart-skip): invoice parser unchanged for the selected --from-month.", flush=True)
    elif not args.skip_invoices:
        cmd = [
            sys.executable,
            str(REPO / "Ingestion" / "Netsuite_Invoice_JSON_Ingestion_Prod.py"),
            "--from",
            args.from_month,
        ]
        if not run_cmd(cmd, "Invoice parsing Netsuite_Invoice_JSON_Ingestion_Prod.py", PROJECT_ROOT):
            state["invoice_status"] = "failed"
            state["invoice_signature"] = invoice_sig
            _save_state(state)
            return 1
        state["invoice_status"] = "ok"
        state["invoice_signature"] = invoice_sig
        state["invoice_completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        _save_state(state)
    else:
        state["invoice_status"] = "skipped_by_flag"
        _save_state(state)

    if not args.skip_maps:
        if not run_sql_file(REPO / "Maps" / "sql" / "02_unified_reference_maps.sql", "Rebuild unified reference maps"):
            return 1

    if not args.skip_sources:
        if not run_sql_file(REPO / "Maps" / "sql" / "01_unified_billing_sources.sql", "Rebuild unified billing sources"):
            return 1

    if not args.skip_recon:
        cmd = [sys.executable, str(REPO / "Reconciliation" / "_run_skeleton_pipeline.py")]
        if not run_cmd(cmd, "Run skeleton reconciliation pipeline", REPO / "Reconciliation"):
            state["recon_status"] = "failed"
            _save_state(state)
            return 1

    state["recon_status"] = "ok"
    state["last_full_refresh_completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    print("\nFull refresh pipeline complete.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
