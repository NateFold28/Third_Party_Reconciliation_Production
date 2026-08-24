"""Inspect manual recon xlsx files to understand what mapping data
is available for Exium / SentinelOne / Webroot.

Prints for each workbook: sheet names, column headers, sample values.
Writes to log file as it goes so we can watch progress.
"""
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(r"C:\Users\Nate.Fold\OneDrive - ConnectWise, Inc\THIRD_PARTY_RECONCILIATION\Manual Recon Files 2026")

TARGETS = [
    ROOT / "MASTER_PARTNER_MAPPING_SEED.xlsx",
    ROOT / "Exium" / "07_JUL_2026" / "Exium Recon JUL 2026.xlsx",
    ROOT / "SentinelOne" / "06_JUN_2026" / "SentinelOne reconciliation June'26 (1).xlsx",
    ROOT / "Webroot CW" / "07_JUL_2026" / "Webroot CW Recon July'26.xlsx",
]


def emit(msg: str) -> None:
    print(msg, flush=True)


for p in TARGETS:
    emit("\n" + "=" * 100)
    emit(f"FILE: {p}")
    emit("=" * 100)
    if not p.exists():
        emit("  DOES NOT EXIST"); continue
    size_mb = p.stat().st_size / 1024 / 1024
    emit(f"  size: {size_mb:.2f} MB")
    try:
        xl = pd.ExcelFile(p)
    except Exception as e:
        emit(f"  cannot open: {e}"); continue
    emit(f"  sheets: {xl.sheet_names}")
    for sheet in xl.sheet_names:
        try:
            df_head = pd.read_excel(p, sheet_name=sheet, nrows=5)
        except Exception as e:
            emit(f"  [{sheet}] head read error: {e}")
            continue
        try:
            row_count = len(pd.read_excel(p, sheet_name=sheet, usecols=[0]))
        except Exception:
            row_count = "?"
        emit(f"\n  -- sheet: '{sheet}' ({df_head.shape[1]} cols, {row_count} rows) --")
        for c in df_head.columns:
            sample_vals = df_head[c].dropna().astype(str).head(2).tolist()
            emit(f"     {str(c)[:45]:<45} | {sample_vals}")

