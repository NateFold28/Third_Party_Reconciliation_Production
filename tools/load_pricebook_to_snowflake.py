"""Load SKU_Information_PowerBI.xlsx into ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PRICEBOOK.

This is the authoritative CW pricebook source-of-truth for third-party reconciliation.
Each row in the Excel is a (product, billing-type, tier) triplet. We preserve every
tier so downstream logic can pick the right unit price for a given quantity.

Vendor is inferred from the NAME column (workbook has no explicit vendor column).

Usage:
    python tools/load_pricebook_to_snowflake.py
    python tools/load_pricebook_to_snowflake.py --dry-run
    python tools/load_pricebook_to_snowflake.py --source path/to/Snapshot.xlsx

Design notes:
  * We copy the OneDrive-locked file to logs/ first so pandas can read it even if
    Excel/OneDrive has an exclusive lock upstream. Snapshot path is returned so
    downstream inspection can re-use it.
  * We CREATE OR REPLACE the target table each run — pricebook is a full refresh.
  * Vendor inference is deterministic (regex on NAME). Unknown rows land in the
    OTHER bucket so we can eyeball what fell through.
  * We DO NOT modify THIRD_PARTY_RECON_SKU_MAP_PROD directly. The reference-maps
    SQL (Maps/sql/02_unified_reference_maps.sql) will LEFT JOIN this pricebook
    into RECON_SKU_MAP at build time.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

DEFAULT_SOURCE = Path(
    r"C:\Users\Nate.Fold\OneDrive - ConnectWise, Inc\THIRD_PARTY_RECONCILIATION"
    r"\SKU_Information_PowerBI.xlsx"
)
SNAPSHOT_DIR = Path(r"c:/Users/Nate.Fold/projects/logs")
TABLE_NAME = "RECON_PRICEBOOK"

# Vendor inference rules. Order matters — first match wins.
# Left side is the canonical vendor name used everywhere else in the pipeline.
# Patterns are pragmatic: the CW pricebook rarely uses the vendor name literally
# for OEM/private-label SKUs (KeepIT ships as "SwO SaaS Backup ...",
# Auvik ships as "ConnectWise RMM Networks ...", Acronis ships as
# "APP-Advanced Backup ...").
VENDOR_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("SentinelOne", re.compile(r"\bsentinelone\b|\bsentinel one\b|^s1[- ]", re.I)),
    ("Bitdefender", re.compile(r"\bbitdefender\b|\bgravityzone\b", re.I)),
    ("Webroot", re.compile(r"\bwebroot\b|secureanywhere", re.I)),
    (
        "Acronis",
        re.compile(
            r"\bacronis\b"
            r"|\bAPP[- ](Advanced|Managed Detection|Backup|Security|Disaster|File Sync|BC[- ]|SA[- ])",
            re.I,
        ),
    ),
    ("Proofpoint", re.compile(r"\bproofpoint\b|\bpp[- ]", re.I)),
    (
        "Auvik",
        re.compile(
            r"\bauvik\b"
            r"|\bConnectWise[- ]?RMM\b"
            r"|\bRMM[- ]?Advanced Network Monitoring\b",
            re.I,
        ),
    ),
    ("ESET", re.compile(r"\beset\b", re.I)),
    (
        "KeepIT",
        re.compile(
            r"\bkeepit\b|keep it"
            r"|\bSaaS Backup\b"
            r"|\bRecover SaaS\b"
            r"|\bRMM[- ]?SaaS Backup\b",
            re.I,
        ),
    ),
    ("Exium", re.compile(r"\bexium\b", re.I)),
]

# Columns we keep from the workbook.
COLS_KEEP = [
    "PRODUCTCODE",
    "LEGACY_CATEGORY",
    "VENDOR PART #",
    "STATUS",
    "NAME",
    "RTM",
    "BILLING TYPE",
    "UOM",
    "CUR",
    "TIERNUM",
    "LOWERBOUND",
    "UPPERBOUND",
    "Retail Price",
    "Cost",
    "PRODUCT_LINE",
    "Previous SKU",
    "SOURCE",
    "SKU_TYPE",
    "FAMILY",
    "CWS_MARKETPLACE_SKU__C",
    "NetSuite ID",
]


@dataclass
class LoadResult:
    total_rows: int
    per_vendor: pd.Series
    unknown_rows: int


def infer_vendor(name: str | float) -> str:
    """Return canonical vendor name, or 'OTHER' if no rule matches."""
    if not isinstance(name, str):
        return "OTHER"
    for vendor, pattern in VENDOR_RULES:
        if pattern.search(name):
            return vendor
    return "OTHER"


def apply_seed_vendor_fallback(df: pd.DataFrame) -> pd.DataFrame:
    """For rows still VENDOR='OTHER', try to look up an authoritative vendor
    from THIRD_PARTY_RECON_SKU_MAP_PROD via CW_SKU / VENDOR_SKU / PREVIOUS_SKU.

    We only override 'OTHER'; regex matches take precedence.
    """
    other_mask = df["VENDOR"] == "OTHER"
    if not other_mask.any():
        return df

    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT UPPER(TRIM(CW_SKU)) AS CW_SKU, VENDOR "
            "FROM THIRD_PARTY_RECON_SKU_MAP_PROD WHERE CW_SKU IS NOT NULL"
        )
        cw_map = {row[0]: row[1] for row in cur.fetchall() if row[0]}
        cur.execute(
            "SELECT DISTINCT UPPER(TRIM(VENDOR_SKU)) AS VENDOR_SKU, VENDOR "
            "FROM THIRD_PARTY_RECON_SKU_MAP_PROD WHERE VENDOR_SKU IS NOT NULL"
        )
        vs_map = {row[0]: row[1] for row in cur.fetchall() if row[0]}
    finally:
        conn.close()

    def _lookup(row: pd.Series) -> str:
        for col, m in (("CW_SKU", cw_map), ("VENDOR_SKU", vs_map), ("PREVIOUS_SKU", cw_map)):
            key = row.get(col)
            if isinstance(key, str) and key.strip():
                hit = m.get(key.strip().upper())
                if hit:
                    return hit
        return "OTHER"

    df.loc[other_mask, "VENDOR"] = df.loc[other_mask].apply(_lookup, axis=1)
    return df


def make_snapshot(source: Path) -> Path:
    """Copy the Excel workbook to logs/ so we bypass OneDrive file locks."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snap = SNAPSHOT_DIR / "SKU_Information_PowerBI_snapshot.xlsx"
    shutil.copy2(source, snap)
    return snap


def load_workbook(path: Path) -> pd.DataFrame:
    """Read the Export sheet and normalize columns."""
    df = pd.read_excel(path, sheet_name="Export")
    missing = [c for c in COLS_KEEP if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"Pricebook missing expected columns: {missing}. Available: {list(df.columns)}"
        )
    df = df[COLS_KEEP].copy()

    df["VENDOR"] = df["NAME"].apply(infer_vendor)

    # Normalize numeric columns — Excel may leave blanks as NaN.
    for col in ("TIERNUM", "LOWERBOUND", "UPPERBOUND", "Retail Price", "Cost"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Rename to Snowflake-friendly identifiers.
    df = df.rename(
        columns={
            "PRODUCTCODE": "CW_SKU",
            "VENDOR PART #": "VENDOR_SKU",
            "NAME": "PRODUCT_NAME",
            "BILLING TYPE": "BILLING_TYPE",
            "Retail Price": "CW_UNIT_PRICE",
            "Cost": "VENDOR_UNIT_PRICE",
            "Previous SKU": "PREVIOUS_SKU",
            "CWS_MARKETPLACE_SKU__C": "CWS_MARKETPLACE_SKU",
            "NetSuite ID": "NETSUITE_ID",
        }
    )

    # Trim string cols.
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).where(df[col].notna(), None)
        df[col] = df[col].apply(lambda v: v.strip() if isinstance(v, str) else v)
    return df


def write_to_snowflake(df: pd.DataFrame, dry_run: bool) -> LoadResult:
    """CREATE OR REPLACE the pricebook table and load all rows.

    Uses `snowflake.connector.pandas_tools.write_pandas` for efficient bulk load.
    """
    per_vendor = df["VENDOR"].value_counts().sort_index()
    unknown = int((df["VENDOR"] == "OTHER").sum())

    if dry_run:
        print("[DRY RUN] would write to Snowflake — skipping.")
        return LoadResult(len(df), per_vendor, unknown)

    from snowflake.connector.pandas_tools import write_pandas  # local import

    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        cur = conn.cursor()
        ddl = f"""
        CREATE OR REPLACE TABLE {TABLE_NAME} (
            VENDOR              VARCHAR,
            CW_SKU              VARCHAR,
            VENDOR_SKU          VARCHAR,
            PRODUCT_NAME        VARCHAR,
            STATUS              VARCHAR,
            LEGACY_CATEGORY     VARCHAR,
            RTM                 VARCHAR,
            BILLING_TYPE        VARCHAR,
            UOM                 VARCHAR,
            CUR                 VARCHAR,
            TIERNUM             NUMBER(10,0),
            LOWERBOUND          NUMBER(18,4),
            UPPERBOUND          NUMBER(18,4),
            VENDOR_UNIT_PRICE   NUMBER(18,6),
            CW_UNIT_PRICE       NUMBER(18,6),
            PRODUCT_LINE        VARCHAR,
            PREVIOUS_SKU        VARCHAR,
            SOURCE              VARCHAR,
            SKU_TYPE            VARCHAR,
            FAMILY              VARCHAR,
            CWS_MARKETPLACE_SKU VARCHAR,
            NETSUITE_ID         VARCHAR,
            LOAD_TS             TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
        )
        """
        cur.execute(ddl)

        # Cast NETSUITE_ID to string so mixed float/int values load cleanly.
        df_out = df.copy()
        df_out["NETSUITE_ID"] = df_out["NETSUITE_ID"].apply(
            lambda v: None if pd.isna(v) else (
                str(int(v)) if isinstance(v, float) and float(v).is_integer() else str(v)
            )
        )
        # Ensure column order matches DDL.
        column_order = [
            "VENDOR", "CW_SKU", "VENDOR_SKU", "PRODUCT_NAME", "STATUS",
            "LEGACY_CATEGORY", "RTM", "BILLING_TYPE", "UOM", "CUR",
            "TIERNUM", "LOWERBOUND", "UPPERBOUND", "VENDOR_UNIT_PRICE", "CW_UNIT_PRICE",
            "PRODUCT_LINE", "PREVIOUS_SKU", "SOURCE", "SKU_TYPE", "FAMILY",
            "CWS_MARKETPLACE_SKU", "NETSUITE_ID",
        ]
        df_out = df_out[column_order]

        success, num_chunks, num_rows, _ = write_pandas(
            conn, df_out, TABLE_NAME, auto_create_table=False, overwrite=False
        )
        if not success:
            raise RuntimeError("write_pandas returned success=False")
        print(f"Loaded {num_rows} rows in {num_chunks} chunks into {TABLE_NAME}.")
    finally:
        conn.close()

    return LoadResult(len(df), per_vendor, unknown)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    if not args.source.exists():
        print(f"ERROR: pricebook not found at {args.source}")
        return 1

    print(f"Source: {args.source}")
    snap = make_snapshot(args.source)
    print(f"Snapshot: {snap}")

    df = load_workbook(snap)
    print(f"Loaded {len(df):,} rows from workbook.")
    print("Vendor distribution (regex only):")
    print(df["VENDOR"].value_counts().sort_index().to_string())

    df = apply_seed_vendor_fallback(df)
    print("\nVendor distribution (after seed fallback):")
    print(df["VENDOR"].value_counts().sort_index().to_string())

    unknown = df[df["VENDOR"] == "OTHER"]
    if not unknown.empty:
        print(f"\nWARN: {len(unknown)} rows with no vendor match.")
        print("Sample:")
        with pd.option_context("display.max_colwidth", 100):
            print(unknown["PRODUCT_NAME"].head(20).to_string(index=False))

    result = write_to_snowflake(df, args.dry_run)
    print(f"\nDone. total={result.total_rows} unknown={result.unknown_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
