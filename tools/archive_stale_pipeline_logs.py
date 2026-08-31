"""Archive stale scratch/log files from logs/ to logs/_archive_20260830/.

Idempotent: files are MOVED, so a second run is a no-op. To reverse, move
files back from the archive folder.

Kept in place (curated):
  * pipeline_no_step_0a_20260830.txt         (winning production run)
  * unmapped_partner_months_for_seed_triage_20260830.csv  (Partner Ops backlog)
  * All existing curated .md audit summaries
"""
from __future__ import annotations

import shutil
from pathlib import Path

LOGS = Path(__file__).resolve().parent.parent / "logs"
ARCHIVE = LOGS / "_archive_20260830"
ARCHIVE.mkdir(exist_ok=True)

PATTERNS = [
    # Superseded intermediate pipeline iteration logs
    "pipeline_after_*_20260830.txt",
    "pipeline_full_after_*_20260830.txt",
    "skeleton_after_*_20260830.txt",
    "pipeline_full_after_*_20260829*.txt",
    "pipeline_full_webroot_api_universe_20260829.txt",
    "skeleton_rebuild_20260827_*.txt",
    "skeleton_run_proofpoint_trt_20260828.txt",
    "run_reports_*_2026_08_21*.txt",
    "s1_exium_*_20260828.txt",
    "s1_exium_*_20260829.txt",
    "proofpoint_rebuild_20260828*.txt",
    "proofpoint_applied_*_20260827.txt",
    "proofpoint_target_validation_20260827.txt",
    "rebuild_maps_20260828.txt",
    "pricebook_*_20260830.txt",
    "prune_sku_maps_20260830.txt",
    "behavior_audit_4vendors_20260829.txt",
    "trt_vendor_audit_*_20260829.txt",
    "output_prod_after_trt_drop_20260829.txt",
    "webroot_rewire_run_20260830.txt",
    # Scratch CSVs from 08-26 partner-map investigation
    "pipeline_before_after_delta_20260829.csv",
    "pipeline_metrics_*_20260829.csv",
    "null_partner_rows_by_vendor_exception_20260826.csv",
    "partner_name_multi_sf_candidates_20260826.csv",
    "pipe_partner_top_values_20260826.csv",
    "remaining_partner_multi_sf_after_fix_20260826.csv",
    "vendor_fix_priority_from_conflicts_20260826.csv",
    # Scratch Python probe scripts
    "tmp_*.py",
]


def main() -> int:
    moved = 0
    total_bytes = 0
    for pattern in PATTERNS:
        for f in LOGS.glob(pattern):
            if not f.is_file():
                continue
            dest = ARCHIVE / f.name
            if dest.exists():
                continue
            total_bytes += f.stat().st_size
            shutil.move(str(f), str(dest))
            moved += 1
    print(f"Moved {moved} stale files to {ARCHIVE.relative_to(LOGS.parent)}")
    print(f"Reclaimed roughly {total_bytes/1024:.1f} KB from logs/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
