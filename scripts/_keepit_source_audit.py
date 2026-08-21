"""KeepIT source-file audit: per month, list files vs which the ingestion picks up.

Read-only. Answers 'why did earlier KeepIT months not populate STANDALONE?'
Does NOT modify any Snowflake data or ingestion scripts.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ingestion"))

from KeepIT_Vendor_Usage_Ingestion_Prod import (  # type: ignore
    DEFAULT_SOURCE_ROOT,
    is_main_summary_file,
    is_promo_summary_file,
    is_takeout_summary_file,
    is_takeout_invoice_file,
)

MONTH_RE_PART = r"^\d{2}_[A-Z]{3}_\d{4}$"
import re
MONTH_RE = re.compile(MONTH_RE_PART)


def classify(path: Path) -> str:
    if path.name.startswith("~$"):
        return "ignored:lockfile"
    ext = path.suffix.lower()
    parts: list[str] = []
    if is_main_summary_file(path):
        parts.append("MAIN")
    if is_promo_summary_file(path):
        parts.append("PROMO")
    if is_takeout_summary_file(path):
        parts.append("TAKEOUT_xlsx")
    if is_takeout_invoice_file(path):
        parts.append("TAKEOUT_pdf(candidate)")
    if not parts:
        return f"unmatched({ext})"
    return "+".join(parts)


def main() -> None:
    root = DEFAULT_SOURCE_ROOT
    print(f"Source root: {root}")
    print(f"Exists: {root.exists()}\n")
    if not root.exists():
        print("ERROR: source root does not exist; nothing to audit.")
        return

    month_dirs = sorted(
        p for p in root.iterdir() if p.is_dir() and MONTH_RE.match(p.name)
    )
    for month in month_dirs:
        print(f"=== {month.name} ===")
        files = sorted(p for p in month.iterdir() if p.is_file())
        if not files:
            print("  (empty)")
            continue
        matched_main = matched_promo = matched_takeout = 0
        for f in files:
            tag = classify(f)
            if "MAIN" in tag:
                matched_main += 1
            if "PROMO" in tag:
                matched_promo += 1
            if "TAKEOUT" in tag:
                matched_takeout += 1
            print(f"  [{tag:<25}] {f.name}")
        print(
            f"  --> match summary: MAIN={matched_main}, PROMO={matched_promo}, "
            f"TAKEOUT={matched_takeout}\n"
        )


if __name__ == "__main__":
    main()
