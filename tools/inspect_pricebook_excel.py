"""Inspect the SKU_Information_PowerBI.xlsx pricebook workbook.

Reports every sheet, its columns, row count, and the first 3 sample rows so
we can decide how to ingest VENDOR_UNIT_PRICE / CW_UNIT_PRICE / CONTRACT_COST_RATE
and TIERNUM into THIRD_PARTY_RECON_SKU_MAP_PROD.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

XLSX = Path(
    r"c:/Users/Nate.Fold/projects/logs/SKU_Information_PowerBI_snapshot_20260830.xlsx"
)


def main() -> int:
    if not XLSX.exists():
        print(f"ERROR: pricebook not found at {XLSX}")
        return 1

    print(f"File: {XLSX}")
    print(f"Size: {XLSX.stat().st_size:,} bytes")
    print(f"Modified: {pd.Timestamp(XLSX.stat().st_mtime, unit='s')}")

    xl = pd.ExcelFile(XLSX)
    print(f"\nSheets ({len(xl.sheet_names)}): {xl.sheet_names}")

    for sheet in xl.sheet_names:
        df = pd.read_excel(XLSX, sheet_name=sheet)
        print(f"\n=== SHEET: {sheet!r} ===")
        print(f"  shape: {df.shape[0]:,} rows x {df.shape[1]} cols")
        print(f"  columns: {list(df.columns)}")
        if not df.empty:
            print("  first 3 rows:")
            with pd.option_context(
                "display.max_columns", None, "display.width", 240
            ):
                print(df.head(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
