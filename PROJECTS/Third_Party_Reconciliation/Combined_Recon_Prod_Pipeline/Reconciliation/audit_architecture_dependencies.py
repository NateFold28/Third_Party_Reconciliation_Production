"""
Audit active repo dependencies for the production third-party recon architecture.

This is a static code audit. It does not execute SQL and does not mutate
Snowflake. The goal is to prove that the active path is limited to:
  - 8 vendor ingestion scripts + Bitdefender royalty SQL into vendor usage
  - Netsuite invoice parser into vendor invoices
  - unified partner/SKU maps
  - direct Zuora, Marketplace, and raw TRT billing/usage sources
  - 9 vendor recon SQL files
  - shared DETAIL_PROD -> OUTPUT_PROD -> SUMMARY_PROD -> app
"""
from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

ACTIVE_FILES = [
    Path("README.md"),
    Path("Maps/sql/01_unified_billing_sources.sql"),
    Path("Maps/sql/02_unified_reference_maps.sql"),
    Path("Maps/sql/00b_backfill_invoice_prices.sql"),
    Path("Maps/sql/00c_vendor_usage_views.sql"),
    Path("Reconciliation/00_bitdefender_vendor_usage_rebuild.sql"),
    Path("Reconciliation/10_vendor_invoice_usage_intra_prod.sql"),
    Path("Reconciliation/_run_full_refresh_pipeline.py"),
    Path("Reconciliation/_run_skeleton_pipeline.py"),
    Path("Reconciliation/build_third_party_recon_output_prod.py"),
    Path("Reconciliation/Acronis_Reconciliation_Script_Prod.sql"),
    Path("Reconciliation/Auvik_Reconciliation_Script_Prod.sql"),
    Path("Reconciliation/Bitdefender_Reconciliation_Script_Prod.sql"),
    Path("Reconciliation/ESET_Reconciliation_Script_Prod.sql"),
    Path("Reconciliation/Exium_Reconciliation_Script_Prod.sql"),
    Path("Reconciliation/KeepIT_Reconciliation_Script_Prod.sql"),
    Path("Reconciliation/Proofpoint_Reconciliation_Script_Prod.sql"),
    Path("Reconciliation/SentinelOne_Reconciliation_Script_Prod.sql"),
    Path("Reconciliation/Webroot_Reconciliation_Script_Prod.sql"),
    Path("app/combined_recon_app.py"),
    Path("Ingestion/Acronis_Vendor_Usage_Ingestion_Prod.py"),
    Path("Ingestion/Auvik_Vendor_Usage_Ingestion_Prod.py"),
    Path("Ingestion/ESET_Vendor_Usage_Ingestion_Prod.py"),
    Path("Ingestion/Exium_Vendor_Usage_Ingestion_Prod.py"),
    Path("Ingestion/KeepIT_Vendor_Usage_Ingestion_Prod.py"),
    Path("Ingestion/Proofpoint_Vendor_Usage_Ingestion_Prod.py"),
    Path("Ingestion/SentinelOne_Vendor_Usage_Ingestion_Prod.py"),
    Path("Ingestion/Webroot_Vendor_Usage_Ingestion_Prod.py"),
    Path("Ingestion/Netsuite_Invoice_JSON_Ingestion_Prod.py"),
]

BANNED_PATTERNS = {
    "ZUORA_THIRD_PARTY_RECON_BASE": "Legacy Zuora base table is not allowed in active production logic.",
    "_LEGACY": "Legacy snapshot tables must not feed active production logic.",
    "THIRD_PARTY_RECON_SOURCE_TRT_PROD": "Retired TRT bridge; use raw BASE_CW_DP_TRT directly.",
    "THIRD_PARTY_RECON_TRT_BILLING_PROD": "Retired TRT bridge; use raw BASE_CW_DP_TRT directly.",
    "WEBROOT_TRT_USAGE_MONTHLY": "Retired Webroot TRT intermediate; logic should be inline.",
    "WEBROOT_TRT_ENDPOINT_RMM_DISCOUNT_MONTHLY": "Retired Webroot discount intermediate; logic should be inline.",
    "EXIUM_USAGE_RECON_COMPAT": "Retired Exium usage shim; read shared vendor usage table directly.",
    "PROOFPOINT_VENDOR_MATCHED": "Old matched table must not feed active production logic.",
    "PROOFPOINT_BILLING_MATCHED": "Old matched table must not feed active production logic.",
    "VENDOR_MATCHED": "Old matched tables must not feed active production logic.",
    "BILLING_MATCHED": "Old matched tables must not feed active production logic.",
    "RECON_DATA_TAB_AUTO": "Manual/export tab outputs are validation-only, not production inputs.",
}

DEPRECATED_USAGE_SHIMS = {
    "ACRONIS_USAGE",
    "AUVIK_USAGE",
    "BITDEFENDER_USAGE",
    "ESET_USAGE",
    "EXIUM_USAGE",
    "KEEPIT_USAGE",
    "PROOFPOINT_USAGE",
    "SENTINELONE_USAGE",
    "WEBROOT_USAGE",
}

ALLOWED_PATTERNS = {
    "THIRD_PARTY_RECON_VENDOR_USAGE_PROD": "Canonical shared vendor usage table.",
    "THIRD_PARTY_RECON_VENDOR_INVOICES": "Canonical parsed vendor invoice table.",
    "THIRD_PARTY_RECON_PARTNER_MAP_PROD": "Source-of-truth partner map seed table.",
    "THIRD_PARTY_RECON_SKU_MAP_PROD": "Source-of-truth SKU map seed table.",
    "RECON_PARTNER_MAP": "Unified governed partner map.",
    "RECON_PARTNER_MAP_MONTHLY": "Date-aware unified partner map.",
    "V_RECON_PARTNER_MAP_MONTHLY_NORM": "Normalized partner-map fallback view.",
    "RECON_SKU_MAP": "Unified governed SKU map.",
    "RECON_ACCOUNT_MERGE_RESOLVER": "Global merged-account resolver.",
    "RECON_VENDOR_PARTNER_MANUAL_MAP": "Governed manual partner override table.",
    "THIRD_PARTY_RECON_SOURCE_ZUORA_PROD": "Canonical live Zuora billing source.",
    "THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD": "Canonical live Marketplace billing source.",
    "THIRD_PARTY_RECON_SOURCE_ROYALTIES_PROD": "Canonical royalty source view.",
    "ANALYTICS_DEV.DBT_NFOLD.FINAL_TPR_ENGINEERING_ZUORA_SOURCE_V2": "Live Zuora engineering source.",
    "ANALYTICS.DBO.CARR__ALL_TRANSACTIONS": "Live Marketplace/CARR source.",
    "ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE": "Live TRT/API usage source.",
    "ANALYTICS.DBO.PRODUCT_MANAGEMENT__ROYALTIES": "Live royalties source for Bitdefender vendor usage.",
    "THIRD_PARTY_RECON_DETAIL_PROD": "Canonical shared detail mart.",
    "THIRD_PARTY_RECON_OUTPUT_PROD": "Canonical app detail output.",
    "THIRD_PARTY_RECON_SUMMARY_PROD": "Canonical app summary output.",
    "THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD": "Canonical invoice-vs-raw-usage control.",
}


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    reference: str
    status: str
    note: str
    text: str


def strip_sql_comment(line: str) -> str:
    return line.split("--", 1)[0]


def strip_python_comment(line: str) -> str:
    in_quote: str | None = None
    escaped = False
    for i, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in ("'", '"'):
            if in_quote == char:
                in_quote = None
            elif in_quote is None:
                in_quote = char
        elif char == "#" and in_quote is None:
            return line[:i]
    return line


def active_code_line(path: Path, line: str) -> str:
    if path.suffix.lower() == ".sql":
        return strip_sql_comment(line)
    if path.suffix.lower() == ".py":
        return strip_python_comment(line)
    return line


def find_pattern_matches(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        code = active_code_line(path, raw_line)
        if not code.strip():
            continue
        is_doc = path.suffix.lower() == ".md"
        is_retired_drop = bool(re.search(r"\bDROP\s+(VIEW|TABLE)\s+IF\s+EXISTS\b", code, flags=re.IGNORECASE))
        for pattern, note in BANNED_PATTERNS.items():
            if pattern.upper() in code.upper():
                if is_doc:
                    status = "DOC"
                    status_note = "Documentation reference only; not executable production logic."
                elif is_retired_drop:
                    status = "CLEANUP"
                    status_note = "Approved retirement cleanup statement."
                else:
                    status = "FAIL"
                    status_note = note
                findings.append(Finding(str(path), line_number, pattern, status, status_note, raw_line.strip()))
        for shim in DEPRECATED_USAGE_SHIMS:
            if re.search(rf"\b(FROM|JOIN)\s+{shim}\b", code, flags=re.IGNORECASE):
                findings.append(
                    Finding(
                        str(path),
                        line_number,
                        shim,
                        "FAIL",
                        "Deprecated usage shim in active SQL; read THIRD_PARTY_RECON_VENDOR_USAGE_PROD directly.",
                        raw_line.strip(),
                    )
                )
        for pattern, note in ALLOWED_PATTERNS.items():
            if pattern.upper() in code.upper():
                findings.append(Finding(str(path), line_number, pattern, "OK", note, raw_line.strip()))
    return findings


def summarize_components() -> list[tuple[str, str, str]]:
    ingestion_files = sorted((REPO / "Ingestion").glob("*_Vendor_Usage_Ingestion_Prod.py"))
    active_ingestion = [p for p in ingestion_files if not p.name.startswith("Bitdefender_")]
    bitdefender_archive = REPO / "Ingestion/_archive/Bitdefender_Vendor_Usage_Ingestion_Prod.py.archived_20260830"
    recon_files = sorted((REPO / "Reconciliation").glob("*_Reconciliation_Script_Prod.sql"))
    rows = [
        ("active_vendor_ingestion_scripts", str(len(active_ingestion)), "Expected 8; Bitdefender uses royalty SQL."),
        (
            "bitdefender_legacy_ingestion_archived",
            str(bitdefender_archive.exists()),
            "Expected True; archive retained for lineage only.",
        ),
        ("vendor_recon_sql_scripts", str(len(recon_files)), "Expected 9 distinct recon pathways."),
        (
            "invoice_parser_exists",
            str((REPO / "Ingestion/Netsuite_Invoice_JSON_Ingestion_Prod.py").exists()),
            "Expected True.",
        ),
        (
            "bitdefender_royalty_sql_exists",
            str((REPO / "Reconciliation/00_bitdefender_vendor_usage_rebuild.sql").exists()),
            "Expected True.",
        ),
        (
            "app_path_exists",
            str((REPO / "app/combined_recon_app.py").exists()),
            "Expected True; lowercase app path is canonical.",
        ),
        (
            "uppercase_app_copy_exists",
            str((REPO / "App/combined_recon_app.py").exists()),
            "Review; uppercase App copy is not canonical per README.",
        ),
    ]
    return rows


def write_csv(path: Path, rows: list[tuple] | list[Finding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if rows and isinstance(rows[0], Finding):
            writer.writerow(["FILE", "LINE", "REFERENCE", "STATUS", "NOTE", "TEXT"])
            for row in rows:
                writer.writerow([row.file, row.line, row.reference, row.status, row.note, row.text])
        else:
            writer.writerow(["CHECK", "VALUE", "NOTE"])
            writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Static architecture dependency audit.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to output/architecture_dependency_audit_<timestamp>.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or REPO / "output" / f"architecture_dependency_audit_{timestamp}"
    all_findings: list[Finding] = []

    for rel_path in ACTIVE_FILES:
        path = REPO / rel_path
        if not path.exists():
            all_findings.append(
                Finding(str(rel_path), 0, "(missing file)", "FAIL", "Expected active architecture file is missing.", "")
            )
            continue
        all_findings.extend(find_pattern_matches(rel_path, path.read_text(encoding="utf-8", errors="replace")))

    write_csv(output_dir / "architecture_dependency_findings.csv", all_findings)
    write_csv(output_dir / "architecture_component_summary.csv", summarize_components())

    fail_count = sum(1 for finding in all_findings if finding.status == "FAIL")
    ok_count = sum(1 for finding in all_findings if finding.status == "OK")
    cleanup_count = sum(1 for finding in all_findings if finding.status == "CLEANUP")
    doc_count = sum(1 for finding in all_findings if finding.status == "DOC")
    print(f"Writing architecture dependency audit to {output_dir}")
    print(
        f"  architecture_dependency_findings.csv: {len(all_findings):,} rows "
        f"({fail_count:,} fail, {cleanup_count:,} cleanup, {doc_count:,} doc, {ok_count:,} ok)"
    )
    print("  architecture_component_summary.csv: component counts")
    if fail_count:
        print("FAIL: active code still contains blocked or deprecated references.")
        return 1
    print("PASS: no blocked or deprecated references found in active architecture files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
