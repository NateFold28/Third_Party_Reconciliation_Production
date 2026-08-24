#!/usr/bin/env python3
"""
Ingest Exium ConnectWise billing report CSVs into Snowflake.

Exium delivers a single monthly CSV file named Connectwise-Billing_Report_MONTH-YYYY.csv
containing roughly 600-620 rows per month.

CRITICAL: The billing measure is 'Agent Count', NOT 'Quantity'. These columns disagree
on ~37% of rows. Amount is computed as Agent Count Ã— Price and must be used for all
chargeable/non-chargeable filtering.

The service window (Start Date â†’ End Date) is a rolling ~28-day cycle that does not
align to calendar months. The reporting period is derived from folder and filename
(e.g., 2026-06), distinct from the actual service window in the data.

Filter rule: Keep all rows but flag chargeable = (Agent Count Ã— Price) â‰  0.
This matches the manual workbook exactly: 337 chargeable rows in June, matching
manual Vendor Usage sheet exactly.

Reconciliation grain: Partner Name Ã— ProductSKU (grouped aggregation)

All 15 source columns are ingested, including ones empty in current months, because
an empty column is a fact worth tracking. Three quantity columns are retained
deliberately to surface provisioning-vs-consumption mismatches.

Target table: ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from snowflake.connector.pandas_tools import write_pandas

EXIUM_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = EXIUM_ROOT.parents[2]
OUTPUT_DIR = EXIUM_ROOT / "output"

DEFAULT_SOURCE_ROOT = Path(
    r"C:\Users\Nate.Fold\OneDrive - ConnectWise, Inc"
    r"\THIRD_PARTY_RECONCILIATION\2026 - Vendor Files\Exium"
)

VENDOR_NAME = "Exium"
TABLE_NAME = "ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD"

# Month folder pattern: MM_MON_YYYY (e.g., 06_JUN_2026)
MONTH_FOLDER_RE = re.compile(r"^(\d{2})_([A-Z]{3})_(\d{4})$", re.IGNORECASE)

# Source CSV columns in expected order
SOURCE_COLUMNS = [
    "Partner ID",
    "Partner Name",
    "Client ID",
    "Site ID",
    "ProductSKU",
    "ChargeSKU",
    "Start Date",
    "End Date",
    "UOM",
    "Quantity",
    "Agent Count(Overall)",
    "Agent Count",
    "Currency",
    "Price",
    "Description",
]

TEMPLATE_COLUMNS = [
    "BILLING_MONTH",
    "VENDOR",
    "VENDOR_PARTNER_NAME",
    "VENDOR_PRODUCT_SKU",
    "MODIFIER",
    "QUANTITY",
    "UNIT_PRICE",
    "AMOUNT",
    "CURRENCY",
]

# SKU price reference (from spec)
SKU_REFERENCE_PRICES = {
    "EX-CGW": 22.50,
    "EX-CGW-NEW": 50.00,
    "EX-SIA": 5.50,
    "EX-SPA": 5.50,
    "EX-SASE-PRO": 8.75,
    "EX-SASE-ESSENTIALS": 5.50,
}


@dataclass
class IngestStats:
    billing_month: str
    service_window_start: datetime
    service_window_end: datetime
    source_file: Path
    raw_rows: int
    chargeable_rows: int
    non_chargeable_rows: int
    distinct_partners: int
    distinct_clients: int
    distinct_skus: int
    total_agent_count: float
    total_amount: float
    price_mismatches: Dict[str, Tuple[float, float]]  # SKU -> (file_price, reference_price)
    unknown_skus: set[str]


def to_numeric(value) -> float:
    """Convert value to float, returning 0.0 on failure."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def parse_date_dmy(date_str: str) -> datetime | None:
    """Parse DD-MM-YYYY date format (day first)."""
    if not date_str:
        return None
    try:
        return datetime.strptime(str(date_str).strip(), "%d-%m-%Y")
    except ValueError:
        return None


def month_folder_for_period(source_root: Path, yyyymm: str) -> Path:
    """Locate the MM_MON_YYYY folder for a given YYYY-MM period."""
    target_dt = datetime.strptime(yyyymm, "%Y-%m")
    target_prefix = f"{target_dt:%m_%b_%Y}".upper()
    
    candidates = [
        p for p in source_root.iterdir()
        if p.is_dir() and MONTH_FOLDER_RE.match(p.name)
        and p.name.upper().startswith(target_prefix)
    ]
    if not candidates:
        raise FileNotFoundError(f"No month folder found for {yyyymm} in {source_root}")
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def locate_billing_csv(month_folder: Path) -> Path | None:
    """Find the vendor billing CSV, handling varying name patterns.

    Accepted patterns (case-insensitive substring match on 'billing' + 'report'
    or 'billing_report'):
      - Connectwise-Billing_Report_June-2026.csv
      - Billing Report March-2026-lat.csv
    Excludes 'Manual billing file ...' CSVs.
    """
    all_csvs = list(month_folder.glob("*.csv"))
    candidates = []
    for p in all_csvs:
        name_lower = p.name.lower()
        if "manual" in name_lower:
            continue
        if "billing" in name_lower and ("report" in name_lower or "billing_report" in name_lower):
            candidates.append(p)
    if candidates:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]
    return None


# Column name aliases: some months use compact names (no spaces, different casing)
COLUMN_ALIASES: dict[str, str] = {
    "partnerid": "Partner ID",
    "partner name": "Partner Name",
    "clientid": "Client ID",
    "client id": "Client ID",
    "siteid": "Site ID",
    "site id": "Site ID",
    "productsku": "ProductSKU",
    "chargesku": "ChargeSKU",
    "startdate": "Start Date",
    "start date": "Start Date",
    "enddate": "End Date",
    "end date": "End Date",
    "agentcount": "Agent Count",
    "agent count": "Agent Count",
    "agent count(overall)": "Agent Count(Overall)",
}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to canonical form regardless of spacing/casing."""
    rename_map = {}
    for col in df.columns:
        canonical = COLUMN_ALIASES.get(col.strip().lower())
        if canonical and col != canonical:
            rename_map[col] = canonical
    return df.rename(columns=rename_map)


def validate_period_consistency(df: pd.DataFrame) -> Tuple[datetime, datetime]:
    """Assert exactly one Start Date and End Date pair in the data."""
    start_dates = df["Start Date"].unique()
    end_dates = df["End Date"].unique()
    
    if len(start_dates) != 1 or len(end_dates) != 1:
        raise ValueError(
            f"Expected single Start Date/End Date pair; "
            f"found {len(start_dates)} start dates and {len(end_dates)} end dates"
        )
    
    start_dt = parse_date_dmy(start_dates[0])
    end_dt = parse_date_dmy(end_dates[0])
    
    if start_dt is None or end_dt is None:
        raise ValueError(f"Failed to parse Start Date or End Date")
    
    if start_dt > end_dt:
        raise ValueError(f"Start Date {start_dt} after End Date {end_dt}")
    
    return start_dt, end_dt


def process_billing_csv(file_path: Path, billing_month: str) -> Tuple[pd.DataFrame, IngestStats]:
    """Process a single Exium billing CSV file."""
    # Read CSV
    df = pd.read_csv(file_path, dtype=str)
    df.columns = [col.strip() for col in df.columns]
    # Normalize column name variations (compact vs spaced, different casing)
    df = normalize_columns(df)

    raw_count = len(df)
    
    # Validate period consistency
    service_start, service_end = validate_period_consistency(df)
    
    # Coerce numeric columns
    df["Quantity"] = df["Quantity"].apply(to_numeric)
    df["Agent Count(Overall)"] = df["Agent Count(Overall)"].apply(to_numeric)
    df["Agent Count"] = df["Agent Count"].apply(to_numeric)
    df["Price"] = df["Price"].apply(to_numeric)
    
    # Compute derived Amount (Agent Count Ã— Price)
    df["Amount"] = df["Agent Count"] * df["Price"]
    
    # Chargeable flag
    df["Chargeable"] = df["Amount"] != 0
    
    # Trim text fields
    for col in ["Partner Name", "Client ID"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    
    # Compute statistics
    chargeable_count = df[df["Chargeable"]].shape[0]
    non_chargeable_count = raw_count - chargeable_count
    distinct_partners = df["Partner Name"].nunique()
    distinct_clients = df["Client ID"].nunique()
    distinct_skus = df["ProductSKU"].nunique()
    total_agent_count = df["Agent Count"].sum()
    total_amount = df["Amount"].sum()
    
    # Price validation
    price_mismatches: Dict[str, Tuple[float, float]] = {}
    for sku, ref_price in SKU_REFERENCE_PRICES.items():
        sku_rows = df[df["ProductSKU"] == sku]
        if not sku_rows.empty:
            file_prices = sku_rows["Price"].unique()
            for file_price in file_prices:
                if abs(file_price - ref_price) > 0.01:  # tolerance for float comparison
                    price_mismatches[sku] = (file_price, ref_price)
    
    # Unknown SKU detection
    unknown_skus = set(df["ProductSKU"].unique()) - set(SKU_REFERENCE_PRICES.keys())
    
    # Build production usage frame at the shared vendor usage grain.
    billing_date = datetime.strptime(billing_month, "%Y-%m").date()

    result_df = pd.DataFrame(
        {
            "BILLING_MONTH": billing_date,
            "VENDOR": VENDOR_NAME,
            "VENDOR_PARTNER_NAME": df["Partner Name"].astype(str).str.strip(),
            "VENDOR_PRODUCT_SKU": df["ProductSKU"].astype(str).str.strip(),
            "MODIFIER": pd.Series([pd.NA] * len(df), dtype="string"),
            "QUANTITY": df["Agent Count"],
            "UNIT_PRICE": df["Price"],
            "AMOUNT": df["Amount"],
            "CURRENCY": df["Currency"].astype(str).str.strip(),
        }
    )
    result_df = result_df[result_df["AMOUNT"] != 0].copy()
    result = (
        result_df[TEMPLATE_COLUMNS]
        .groupby(
            [
                "BILLING_MONTH",
                "VENDOR",
                "VENDOR_PARTNER_NAME",
                "VENDOR_PRODUCT_SKU",
                "MODIFIER",
                "UNIT_PRICE",
                "CURRENCY",
            ],
            dropna=False,
            as_index=False,
        )
        .agg({"QUANTITY": "sum", "AMOUNT": "sum"})
    )
    result = result[TEMPLATE_COLUMNS].reset_index(drop=True)
    
    stats = IngestStats(
        billing_month=billing_month,
        service_window_start=service_start,
        service_window_end=service_end,
        source_file=file_path,
        raw_rows=raw_count,
        chargeable_rows=chargeable_count,
        non_chargeable_rows=non_chargeable_count,
        distinct_partners=distinct_partners,
        distinct_clients=distinct_clients,
        distinct_skus=distinct_skus,
        total_agent_count=total_agent_count,
        total_amount=total_amount,
        price_mismatches=price_mismatches,
        unknown_skus=unknown_skus,
    )
    
    return result, stats


def ensure_table_schema(conn, reset: bool) -> None:
    """Create or validate schema for THIRD_PARTY_RECON_VENDOR_USAGE_PROD."""
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
      BILLING_MONTH DATE,
      VENDOR VARCHAR,
      VENDOR_PARTNER_NAME VARCHAR,
      VENDOR_PRODUCT_SKU VARCHAR,
      MODIFIER VARCHAR,
      QUANTITY NUMBER(18,4),
      UNIT_PRICE NUMBER(18,6),
      AMOUNT NUMBER(18,6),
      CURRENCY VARCHAR
    )
    """
    
    with conn.cursor() as cursor:
        if reset:
            cursor.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
        cursor.execute(create_sql)


def period_exists(conn, table_name: str, billing_month: str) -> bool:
    """Check if period already exists in table."""
    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT COUNT(*) FROM {table_name} "
            "WHERE BILLING_MONTH = %s AND UPPER(COALESCE(VENDOR, '')) = UPPER(%s)",
            (f"{billing_month}-01", VENDOR_NAME),
        )
        return int(cursor.fetchone()[0]) > 0


def delete_period(conn, table_name: str, billing_month: str) -> None:
    """Delete period from table."""
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {table_name} "
            "WHERE BILLING_MONTH = %s AND UPPER(COALESCE(VENDOR, '')) = UPPER(%s)",
            (f"{billing_month}-01", VENDOR_NAME),
        )


def load_to_snowflake(
    conn,
    df: pd.DataFrame,
    billing_month: str,
    table_name: str,
    replace_month: bool,
) -> int:
    """Load DataFrame to Snowflake table. Returns row count loaded."""
    if df.empty:
        return 0
    
    if replace_month:
        delete_period(conn, table_name, billing_month)
    elif period_exists(conn, table_name, billing_month):
        raise RuntimeError(
            f"Data already exists for {billing_month} in {table_name}; "
            f"use --replace-month to overwrite"
        )
    
    df = _fill_missing_prices(df, VENDOR_NAME, conn=conn)
    ok, chunks, rows, _ = write_pandas(
        conn,
        df,
        table_name=table_name.split(".")[-1],
        schema=table_name.split(".")[1],
        database=table_name.split(".")[0],
        quote_identifiers=False,
    )
    if not ok:
        raise RuntimeError("write_pandas reported failure")
    
    return rows


def write_audit_report(script_dir: Path, stats: IngestStats) -> Path:
    """Write ingestion audit report."""
    output_dir = script_dir.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"exium_ingest_audit_{stamp}.txt"
    
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write("Exium Ingestion Audit\n")
        handle.write("=" * 100 + "\n\n")
        
        handle.write(f"Source file: {stats.source_file.name}\n")
        handle.write(f"Reporting period: {stats.billing_month}\n")
        handle.write(f"Service window: {stats.service_window_start} â†’ {stats.service_window_end}\n")
        handle.write(f"  (Duration: {(stats.service_window_end - stats.service_window_start).days} days)\n\n")
        
        handle.write(f"Row counts:\n")
        handle.write(f"  Raw rows: {stats.raw_rows:,}\n")
        handle.write(f"  Chargeable (Amount â‰  0): {stats.chargeable_rows:,}\n")
        handle.write(f"  Non-chargeable (Amount = 0): {stats.non_chargeable_rows:,}\n\n")
        
        handle.write(f"Dimensions:\n")
        handle.write(f"  Distinct partners: {stats.distinct_partners:,}\n")
        handle.write(f"  Distinct clients: {stats.distinct_clients:,}\n")
        handle.write(f"  Distinct SKUs: {stats.distinct_skus:,}\n\n")
        
        handle.write(f"Aggregates:\n")
        handle.write(f"  Total Agent Count: {stats.total_agent_count:,.0f}\n")
        handle.write(f"  Total Amount: ${stats.total_amount:,.2f}\n\n")
        
        if stats.price_mismatches:
            handle.write(f"âš ï¸ PRICE MISMATCHES vs Reference Pack:\n")
            for sku, (file_price, ref_price) in sorted(stats.price_mismatches.items()):
                handle.write(f"  {sku}: file=${file_price:.2f} vs reference=${ref_price:.2f}\n")
            handle.write("\n")
        
        if stats.unknown_skus:
            handle.write(f"âš ï¸ UNKNOWN SKUS (not in reference):\n")
            for sku in sorted(stats.unknown_skus):
                handle.write(f"  {sku}\n")
            handle.write("\n")
        
        handle.write(f"Ingested at: {datetime.now().isoformat()}\n")
    
    return output_path


def get_connection():
    sys.path.insert(0, str(WORKSPACE_ROOT))
    from TEMPLATES.Python.connection import get_snowflake_connection

    return get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest Exium billing data into Snowflake")
    parser.add_argument(
        "--source-root",
        default=str(DEFAULT_SOURCE_ROOT),
        help="Root path for month folders",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--month", help="Billing month in YYYY-MM format")
    group.add_argument("--all-months", action="store_true", help="Process all available months")
    parser.add_argument("--dry-run", action="store_true", help="Parse and audit; skip Snowflake load")
    parser.add_argument("--replace-month", action="store_true", help="Overwrite existing month")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate table")
    return parser.parse_args()



# ---------------------------------------------------------------------------
# Dynamic invoice rate fill (universal safety net)
# ---------------------------------------------------------------------------
def _fill_missing_prices(df, vendor_name, conn=None):
    """Fill NULL UNIT_PRICE/AMOUNT rows from THIRD_PARTY_RECON_VENDOR_INVOICES.

    Only fires on rows where both UNIT_PRICE and AMOUNT are NULL/0.
    Rows already populated by the source file are never overwritten.
    Exact (billing_month, sku) match first, then carry-forward to most
    recent prior month for same SKU.
    """
    import sys as _sys, pandas as _pd
    _ws = Path(__file__).resolve()
    for _ in range(8):
        _ws = _ws.parent
        if (_ws / "TEMPLATES").exists():
            break
    _sys.path.insert(0, str(_ws))
    try:
        _owned = conn is None
        if _owned:
            from TEMPLATES.Python.connection import get_snowflake_connection as _gc
            _c = _gc(role="DEVELOPER", warehouse="REPORTING_WH",
                     database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION")
        else:
            _c = conn
        _rows = _c.cursor().execute(
            "SELECT BILLING_MONTH, VENDOR_PRODUCT_SKU, AVG(UNIT_PRICE) AS UNIT_PRICE "
            "FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES "
            "WHERE VENDOR ILIKE %s AND UNIT_PRICE IS NOT NULL AND UNIT_PRICE > 0 "
            "GROUP BY 1, 2 ORDER BY 1 DESC, 2",
            (f"%{vendor_name}%",),
        ).fetchall()
        if _owned:
            _c.close()
    except Exception as _e:
        print(f"[WARN] _fill_missing_prices: {_e}. Prices unchanged.", flush=True)
        return df
    if not _rows:
        return df
    inv = _pd.DataFrame(_rows, columns=["BILLING_MONTH", "VENDOR_PRODUCT_SKU", "UNIT_PRICE"])
    inv["BILLING_MONTH"] = _pd.to_datetime(inv["BILLING_MONTH"]).dt.normalize()
    inv["VENDOR_PRODUCT_SKU"] = inv["VENDOR_PRODUCT_SKU"].astype(str).str.strip()
    inv["UNIT_PRICE"] = _pd.to_numeric(inv["UNIT_PRICE"], errors="coerce")
    inv = inv.dropna(subset=["UNIT_PRICE"])
    exact = {(r.VENDOR_PRODUCT_SKU, r.BILLING_MONTH): float(r.UNIT_PRICE) for r in inv.itertuples(index=False)}
    latest: dict = {}
    for r in inv.itertuples(index=False):
        if r.VENDOR_PRODUCT_SKU not in latest:
            latest[r.VENDOR_PRODUCT_SKU] = float(r.UNIT_PRICE)
    df = df.copy()
    for col in ("UNIT_PRICE", "AMOUNT"):
        if col not in df.columns:
            df[col] = None
    # Normalise for comparison only - do NOT reassign BILLING_MONTH (would break write_pandas DATE cast)
    _billing_norm = _pd.to_datetime(df["BILLING_MONTH"]).dt.normalize()
    df["VENDOR_PRODUCT_SKU"] = df["VENDOR_PRODUCT_SKU"].astype(str).str.strip()
    mask = ((df["UNIT_PRICE"].isna() | (df["UNIT_PRICE"] == 0)) &
            (df["AMOUNT"].isna() | (df["AMOUNT"] == 0)))
    filled = 0
    for _i, idx in enumerate(df[mask].index):
        sku = df.at[idx, "VENDOR_PRODUCT_SKU"]
        mo  = _billing_norm.iloc[df.index.get_loc(idx)]
        price = exact.get((sku, mo))
        if price is None:
            bm, bp = None, None
            for (s, m), p in exact.items():
                if s == sku and m <= mo and (bm is None or m > bm):
                    bm, bp = m, p
            price = bp or latest.get(sku)
        if price:
            df.at[idx, "UNIT_PRICE"] = price
            qty = df.at[idx, "QUANTITY"] if "QUANTITY" in df.columns else None
            if qty is not None and not _pd.isna(qty):
                df.at[idx, "AMOUNT"] = float(qty) * price
            filled += 1
    if filled:
        print(f"[INFO] _fill_missing_prices: filled {filled:,} rows for {vendor_name}.", flush=True)
    return df

def main() -> None:
    args = parse_args()
    
    source_root = Path(args.source_root)
    if not source_root.exists():
        raise FileNotFoundError(f"Source root does not exist: {source_root}")
    
    # Discover available months
    available_months = {}
    for folder in sorted(source_root.iterdir()):
        if folder.is_dir():
            m = MONTH_FOLDER_RE.match(folder.name)
            if m:
                mm, mon, yyyy = m.groups()
                available_months[f"{yyyy}-{mm}"] = folder
    
    if not available_months:
        raise RuntimeError(f"No month folders (MM_MON_YYYY) found in {source_root}")
    
    months = sorted(available_months.keys()) if args.all_months else [args.month]
    
    if args.month and args.month not in available_months:
        raise FileNotFoundError(
            f"Month {args.month} not found. Available: {sorted(available_months.keys())}"
        )
    
    # Process each month
    all_frames = []
    all_stats = []
    
    for month in months:
        print(f"Processing {month}...")
        month_folder = available_months[month]
        
        csv_path = locate_billing_csv(month_folder)
        if csv_path is None:
            print(f"  SKIP: No billing CSV found in {month_folder.name}")
            continue
        
        try:
            frame, stats = process_billing_csv(csv_path, month)
            if frame.empty:
                print(f"  SKIP: No rows produced from {csv_path.name}")
                continue
            
            print(
                f"  {stats.source_file.name}: "
                f"raw={stats.raw_rows:,}, chargeable={stats.chargeable_rows:,}, "
                f"non_chargeable={stats.non_chargeable_rows:,}, "
                f"partners={stats.distinct_partners:,}, amount=${stats.total_amount:,.2f}"
            )
            
            all_frames.append(frame)
            all_stats.append(stats)
        
        except Exception as e:
            print(f"  ERROR: {e}")
            raise
    
    if not all_frames:
        print("No data to load.")
        return
    
    # Combine all frames
    combined = pd.concat(all_frames, ignore_index=True)
    
    # Write audit report for each month
    script_dir = Path(__file__).parent
    for stats in all_stats:
        write_audit_report(script_dir, stats)
    
    # Dry run?
    if args.dry_run:
        total_rows = len(combined)
        amount = combined["AMOUNT"].sum()
        print(f"\nDry run complete. total_rows={total_rows:,}, amount=${amount:,.2f}")
        return
    
    # Load to Snowflake
    conn = get_connection()
    try:
        ensure_table_schema(conn, reset=args.reset)
        
        # Load for each month
        from datetime import date as date_type
        for month in months:
            billing_date_obj = datetime.strptime(month, "%Y-%m").date()
            month_data = combined[combined["BILLING_MONTH"] == billing_date_obj]
            if month_data.empty:
                continue
            
            rows_loaded = load_to_snowflake(
                conn,
                month_data,
                month,
                TABLE_NAME,
                replace_month=args.replace_month or args.reset,
            )
            print(f"Loaded {rows_loaded:,} rows for {month} into THIRD_PARTY_RECON_VENDOR_USAGE_PROD")
    
    finally:
        conn.close()


if __name__ == "__main__":
    main()

