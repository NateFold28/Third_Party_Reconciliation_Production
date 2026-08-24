#!/usr/bin/env python3
"""
Ingest ESET regional license usage CSVs into Snowflake.

ESET delivers three regional CSV files each month: US, UK, and AU_NZ. All three
carry an identical 14-column schema, but differ in:
  - Date formats: US is MM/DD/YYYY; UK and AU_NZ are DD/MM/YYYY
  - Regional parent entity names (ConnectWise, LLC / ConnectWise UK, EMEA and ROW / ConnectWise AU and NZ)

Two filters produce the reconciliation population:
  1. Company.Type == 'Managed Msp' (the invoice-aligned partner/product rollup)
  2. License.Type == 'Full' (quarantine 'Trial' rows)

Trial rows are retained in a separate table for visibility when they convert to paid.

Target tables:
  ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ESET_USAGE
  ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ESET_TRIAL_QUARANTINE
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

ESET_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = ESET_ROOT.parents[2]
OUTPUT_DIR = ESET_ROOT / "output"

DEFAULT_SOURCE_ROOT = Path(
    r"C:\Users\Nate.Fold\OneDrive - ConnectWise, Inc"
    r"\THIRD_PARTY_RECONCILIATION\2026 - Vendor Files\ESET"
)

VENDOR_NAME = "ESET"
TABLE_NAME = "ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD"
TRIAL_TABLE_NAME = "ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ESET_TRIAL_QUARANTINE"

# Regional entity names and file prefix -> entity tag mapping
REGIONAL_CONFIG = {
    "US": {
        "file_pattern": "US_license_usage_report_summary.csv",
        "entity_tag": "US",
        "snapshot_date_format": "%m/%d/%Y",  # MM/DD/YYYY
        "parent_entity": "ConnectWise, LLC",
    },
    "UK": {
        "file_pattern": "UK_license_usage_report_summary.csv",
        "entity_tag": "UK",
        "snapshot_date_format": "%d/%m/%Y",  # DD/MM/YYYY
        "parent_entity": "ConnectWise UK, EMEA and ROW",
    },
    "AU_NZ": {
        "file_pattern": "AU_NZ_license_usage_report_summary.csv",
        "entity_tag": "AU-NZ",  # Note: manual workbook uses AU-NZ with hyphen
        "snapshot_date_format": "%d/%m/%Y",  # DD/MM/YYYY
        "parent_entity": "ConnectWise AU and NZ",
    },
}

REPORT_DATE_FORMAT = "%m/%d/%Y"  # All regions use M/D/YYYY

# All fourteen source columns in order
SOURCE_COLUMNS = [
    "Company.Id",
    "Company.Name",
    "Company.Type",
    "ParentCompany.Id",
    "ParentCompany.Name",
    "ParentCompany.Type",
    "Product.Code",
    "Product.Name",
    "Seat Days",
    "Seats",
    "Snapshot Date From",
    "Snapshot Date To",
    "License.Type",
    "ReportDate",
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

PRODUCT_LABEL_RULES = [
    (r"PROTECT ENTRY ON.?PREM", "MSP - PROTECT Entry On-Prem"),
    (r"PROTECT ENTRY", "MSP - PROTECT Entry"),
    (r"PROTECT ADVANCED", "MSP - PROTECT Advanced"),
    (r"PROTECT COMPLETE", "MSP - PROTECT Complete"),
    (r"PROTECT ENTERPRISE", "MSP - PROTECT Enterprise"),
    (r"PROTECT MAIL", "MSP - PROTECT Mail Plus"),
    (r"MAIL SECURITY", "MSP - Mail Security"),
    (r"INSPECT", "MSP - ESET Inspect"),
    (r"SERVER SECURITY|FILE SECURITY", "MSP - Server Security"),
    (r"ENDPOINT SECURITY", "MSP - Endpoint Security"),
    (r"ENDPOINT ANTIVIRUS", "MSP - Endpoint Antivirus"),
    (r"FULL DISK ENCRYPTION", "MSP - Full Disk Encryption"),
    (r"ENDPOINT ENCRYPTION", "MSP - Endpoint Encryption Pro"),
    (r"SECURE AUTHENTICATION", "MSP - Secure Authentication"),
]


@dataclass
class IngestStats:
    region: str
    entity_tag: str
    source_file: Path
    billing_month: datetime
    period_yyyymm: str
    raw_rows: int
    managed_msp_rows: int
    customer_rows: int
    full_license_rows: int
    trial_rows: int
    ingested_rows: int
    trial_ingested_rows: int
    total_seat_days: float


def repair_hyphen_corruption(value: str) -> str:
    """Repair quote-hyphen-quote corruption: '"-"' -> '-'"""
    if not value:
        return value
    return value.replace('"-"', "-")


def parse_snapshot_date(date_str: str, region: str) -> datetime | None:
    """Parse snapshot date using region-specific format."""
    if not date_str:
        return None
    config = REGIONAL_CONFIG[region]
    try:
        return datetime.strptime(date_str.strip(), config["snapshot_date_format"])
    except ValueError:
        return None


def parse_report_date(date_str: str) -> datetime | None:
    """Parse ReportDate using the standard M/D/YYYY format (all regions)."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), REPORT_DATE_FORMAT)
    except ValueError:
        return None


def to_numeric(value) -> float:
    """Convert value to float, returning 0.0 on failure."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def normalize_key(value: object) -> str:
    """Normalize vendor/partner text for invoice-rate lookups."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = repair_hyphen_corruption(str(value)).upper()
    text = text.replace("Â°", "").replace("*", "")
    text = text.replace("â€“", "-").replace("â€”", "-")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def canonical_vendor_product(product_name: object) -> str:
    """Map ESET raw Product.Name to the invoice-visible MSP product label."""
    normalized = normalize_key(product_name)
    for pattern, label in PRODUCT_LABEL_RULES:
        if re.search(pattern, normalized):
            return label
    return repair_hyphen_corruption(str(product_name or "")).strip()


def modifier_from_seats(value: object) -> str | None:
    """Keep the source seat count in MODIFIER without forcing a new numeric column."""
    seats = to_numeric(value)
    if seats == 0:
        return None
    return str(int(seats)) if float(seats).is_integer() else str(seats)


def product_suffix_from_invoice_sku(value: object) -> str:
    """Return the product suffix used in invoice partner descriptions."""
    text = repair_hyphen_corruption(str(value or "")).strip()
    text = text.replace("â€“", "-").replace("â€”", "-")
    text = re.sub(r"^MSP\s*-\s*", "", text, flags=re.IGNORECASE).strip()
    if text.upper() == "ESET INSPECT":
        return "Inspect"
    if text.upper() == "FULL DISK ENCRYPTION":
        return "Full Disk Encryption"
    return text


def invoice_partner_from_description(partner: object, invoice_sku: object) -> str:
    """Strip the trailing invoice product from partner/product descriptions."""
    text = repair_hyphen_corruption(str(partner or "")).strip()
    suffix = product_suffix_from_invoice_sku(invoice_sku)
    if not text or not suffix:
        return text
    for candidate in [suffix, suffix.replace("ESET ", "")]:
        marker = f", {candidate}"
        if text.upper().endswith(marker.upper()):
            return text[: -len(marker)].strip()
    return text


def load_invoice_rates(conn) -> Tuple[Dict[Tuple[str, str, str], float], Dict[Tuple[str, str, str], float]]:
    """Load ESET unit prices from THIRD_PARTY_RECON_VENDOR_INVOICES.

    ESET invoices have no per-partner pricing (PARTNER is NULL) - rates are SKU-level only.
    Strategy: 1. Exact (billing_month, sku) match.  2. Carry-forward most recent prior month.
    Wildcards partner key '*' so assign_invoice_rate finds it for any partner.
    Returns empty dicts gracefully if the table does not exist yet.
    """
    query = """
    SELECT BILLING_MONTH, VENDOR_PRODUCT_SKU, AVG(UNIT_PRICE) AS UNIT_PRICE
    FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
    WHERE VENDOR ILIKE '%eset%'
      AND UNIT_PRICE IS NOT NULL AND UNIT_PRICE > 0
    GROUP BY 1, 2
    ORDER BY 1, 2
    """
    invoice_df = pd.DataFrame()
    try:
        invoice_df = pd.read_sql(query, conn)
    except Exception as e:
        print(f"WARNING: could not load ESET invoice rates ({type(e).__name__}: {e}). Unit prices will be NULL.", flush=True)
    partner_rates: Dict[Tuple[str, str, str], float] = {}
    parent_rates: Dict[Tuple[str, str, str], float] = {}
    if invoice_df.empty:
        return partner_rates, parent_rates

    invoice_df["BILLING_MONTH"] = pd.to_datetime(invoice_df["BILLING_MONTH"])
    invoice_df["VENDOR_PRODUCT_SKU"] = invoice_df["VENDOR_PRODUCT_SKU"].astype(str).str.strip()
    invoice_df["UNIT_PRICE"] = pd.to_numeric(invoice_df["UNIT_PRICE"], errors="coerce")
    invoice_df = invoice_df.dropna(subset=["UNIT_PRICE"])
    sku_latest: dict = {}
    for _, row in invoice_df.iterrows():
        sku = repair_hyphen_corruption(str(row["VENDOR_PRODUCT_SKU"])).replace("\u00e2\u0080\u0093", "-").strip()
        price = float(row["UNIT_PRICE"])
        month_str = row["BILLING_MONTH"].strftime("%Y-%m-%d")
        sku_key = normalize_key(sku)
        sku_latest[sku_key] = price
        key = (month_str, "*", sku_key)
        partner_rates[key] = price
        parent_rates[key] = price
    for sku_key, price in sku_latest.items():
        partner_rates[("latest", "*", sku_key)] = price
        parent_rates[("latest", "*", sku_key)] = price
    print(f"Loaded {len(invoice_df)} ESET invoice rates "
          f"({invoice_df['BILLING_MONTH'].nunique()} months, {invoice_df['VENDOR_PRODUCT_SKU'].nunique()} SKUs).", flush=True)
    return partner_rates, parent_rates


def assign_invoice_rate(row: pd.Series, partner_rates: Dict[Tuple[str, str, str], float], parent_rates: Dict[Tuple[str, str, str], float]) -> "float | None":
    """Assign ESET unit price: exact month match, carry-forward, then latest fallback."""
    billing_month = pd.to_datetime(row["BILLING_MONTH"]).strftime("%Y-%m-%d")
    product_key = normalize_key(repair_hyphen_corruption(str(row["VENDOR_PRODUCT_SKU"])).replace("\u00e2\u0080\u0093", "-").strip())
    exact_key = (billing_month, "*", product_key)
    if exact_key in partner_rates:
        return partner_rates[exact_key]
    target_dt = pd.to_datetime(billing_month)
    best_price = None
    best_dt = None
    for (m, p, s), price in partner_rates.items():
        if m == "latest" or p != "*" or s != product_key:
            continue
        try:
            m_dt = pd.to_datetime(m)
        except Exception:
            continue
        if m_dt <= target_dt and (best_dt is None or m_dt > best_dt):
            best_price = price
            best_dt = m_dt
    if best_price is not None:
        return best_price
    return partner_rates.get(("latest", "*", product_key))

def locate_regional_file(month_folder: Path, region: str) -> Path | None:
    """Find the regional CSV file in the month folder.
    
    Files may have OneDrive duplicates like " (2)" or " (3)" in the name,
    so we search for base pattern and return newest.
    """
    base_pattern = REGIONAL_CONFIG[region]["file_pattern"].replace(".csv", "")
    candidates = sorted(
        [
            p for p in month_folder.glob(f"{base_pattern}*.csv")
            if p.suffix == ".csv"
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def month_folder_for_period(source_root: Path, target_yyyymm: str) -> Path:
    """Locate the MM_MON_YYYY folder for a given YYYY-MM period."""
    target_dt = datetime.strptime(target_yyyymm, "%Y-%m")
    target_prefix = f"{target_dt:%m_%b_%Y}".upper()
    
    candidates = [
        p for p in source_root.iterdir()
        if p.is_dir() and p.name.upper().startswith(target_prefix)
    ]
    if not candidates:
        raise FileNotFoundError(f"No month folder found for {target_yyyymm} in {source_root}")
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def load_regional_csv(file_path: Path) -> pd.DataFrame:
    """Load and normalize regional CSV."""
    df = pd.read_csv(file_path, dtype=str)
    df.columns = [col.strip() for col in df.columns]
    return df


def validate_period_consistency(df: pd.DataFrame, region: str, month_folder: Path) -> Tuple[int, str, datetime]:
    """Validate that parsed snapshot dates yield consistent period and match folder.
    
    Returns (period_month, period_yyyymm, billing_date).
    Raises ValueError if inconsistent or mismatched.
    """
    if "Snapshot Date From" not in df.columns or "Snapshot Date To" not in df.columns:
        raise ValueError(f"Missing snapshot date columns in {region} file")
    
    dates_from = df["Snapshot Date From"].dropna().unique()
    dates_to = df["Snapshot Date To"].dropna().unique()
    
    if len(dates_from) != 1 or len(dates_to) != 1:
        raise ValueError(
            f"{region}: Expected single snapshot window, found "
            f"{len(dates_from)} 'From' dates and {len(dates_to)} 'To' dates"
        )
    
    parsed_from = parse_snapshot_date(dates_from[0], region)
    parsed_to = parse_snapshot_date(dates_to[0], region)
    
    if parsed_from is None or parsed_to is None:
        raise ValueError(f"{region}: Failed to parse snapshot dates: {dates_from[0]} to {dates_to[0]}")
    
    # Verify first day of month and last day of month
    if parsed_from.day != 1:
        raise ValueError(f"{region}: Snapshot 'From' is not first day of month: {parsed_from}")
    
    # Check if 'To' is last day of its month
    next_month = parsed_to.replace(day=28) + pd.Timedelta(days=4)
    last_day = (next_month - pd.Timedelta(days=next_month.day)).day
    if parsed_to.day != last_day:
        raise ValueError(f"{region}: Snapshot 'To' is not last day of month: {parsed_to}")
    
    # Both should be same month
    if parsed_from.year != parsed_to.year or parsed_from.month != parsed_to.month:
        raise ValueError(
            f"{region}: Snapshot From/To span different months: {parsed_from} to {parsed_to}"
        )
    
    period_yyyymm = f"{parsed_from.year:04d}-{parsed_from.month:02d}"
    folder_month = month_folder.name.upper()
    expected_prefix = f"{parsed_from:%m_%b_%Y}".upper()
    
    if not folder_month.startswith(expected_prefix):
        raise ValueError(
            f"{region}: Snapshot period {period_yyyymm} does not match folder {month_folder.name}"
        )
    
    return parsed_from.month, period_yyyymm, parsed_from


def process_regional_file(
    file_path: Path,
    region: str,
    month_folder: Path,
    partner_rates: Dict[Tuple[str, str, str], float],
    parent_rates: Dict[Tuple[str, str, str], float],
) -> Tuple[pd.DataFrame, pd.DataFrame, IngestStats]:
    """
    Process a single regional ESET CSV.
    
    Returns (ingested_df, trial_df, stats).
    """
    raw_df = load_regional_csv(file_path)
    
    # Validate and extract period
    period_month, period_yyyymm, billing_date = validate_period_consistency(raw_df, region, month_folder)
    
    # Repair text corruption in text columns
    for col in ["Company.Name", "ParentCompany.Name", "Product.Name"]:
        if col in raw_df.columns:
            raw_df[col] = raw_df[col].fillna("").astype(str).apply(repair_hyphen_corruption)
    
    raw_count = len(raw_df)
    
    # Split by Company.Type for tier validation
    company_type_key = raw_df["Company.Type"].fillna("").astype(str).str.strip().str.upper()
    managed_msp_df = raw_df[company_type_key == "MANAGED MSP"]
    customer_df = raw_df[company_type_key == "CUSTOMER"]
    
    managed_msp_count = len(managed_msp_df)
    customer_count = len(customer_df)
    
    # Validate tiers match (sum of Seat Days should be equal)
    if managed_msp_count > 0 and customer_count > 0:
        managed_seat_days = managed_msp_df["Seat Days"].apply(to_numeric).sum()
        customer_seat_days = customer_df["Seat Days"].apply(to_numeric).sum()
        if abs(managed_seat_days - customer_seat_days) > 0.01:
            raise ValueError(
                f"{region}: Managed Msp tier ({managed_seat_days:,.0f}) does not match "
                f"Customer tier ({customer_seat_days:,.0f})"
            )
    
    # Keep invoice-aligned Managed MSP tier only.
    working_df = managed_msp_df.copy()
    
    # Split Full vs Trial
    full_df = working_df[working_df["License.Type"].str.strip() == "Full"].copy()
    trial_df = working_df[working_df["License.Type"].str.strip() == "Trial"].copy()
    
    full_count = len(full_df)
    trial_count = len(trial_df)
    
    # Process full licenses (ingested rows)
    full_df = full_df.reset_index(drop=True)
    full_df["BILLING_MONTH"] = billing_date.date()
    full_df["VENDOR"] = VENDOR_NAME
    full_df["VENDOR_PARTNER_NAME"] = full_df["Company.Name"]
    full_df["PARENT_COMPANY_NAME"] = full_df["ParentCompany.Name"]
    full_df["VENDOR_PRODUCT_SKU"] = full_df["Product.Name"].apply(canonical_vendor_product)
    full_df["MODIFIER"] = full_df["Seats"].apply(modifier_from_seats)
    full_df["QUANTITY"] = full_df["Seat Days"].apply(to_numeric)
    full_df["UNIT_PRICE"] = full_df.apply(assign_invoice_rate, axis=1, partner_rates=partner_rates, parent_rates=parent_rates)
    full_df["AMOUNT"] = full_df.apply(
        lambda row: None if row["UNIT_PRICE"] is None or pd.isna(row["UNIT_PRICE"]) else round(to_numeric(row["QUANTITY"]) * to_numeric(row["UNIT_PRICE"]), 6),
        axis=1,
    )
    full_df["CURRENCY"] = "USD"

    result_full = (
        full_df[TEMPLATE_COLUMNS]
        .groupby(["BILLING_MONTH", "VENDOR", "VENDOR_PARTNER_NAME", "VENDOR_PRODUCT_SKU", "MODIFIER", "UNIT_PRICE", "CURRENCY"], dropna=False, as_index=False)
        .agg({"QUANTITY": "sum", "AMOUNT": "sum"})
    )
    result_full.loc[result_full["UNIT_PRICE"].isna(), "AMOUNT"] = None
    result_full = result_full[TEMPLATE_COLUMNS].copy()
    
    # Process trial rows (quarantine)
    trial_df = trial_df.reset_index(drop=True)
    trial_df["BILLING_MONTH"] = billing_date.date()
    trial_df["VENDOR"] = VENDOR_NAME
    trial_df["VENDOR_PARTNER_NAME"] = trial_df["Company.Name"]
    trial_df["VENDOR_PRODUCT_SKU"] = trial_df["Product.Name"].apply(canonical_vendor_product)
    trial_df["MODIFIER"] = trial_df["Seats"].apply(modifier_from_seats)
    trial_df["QUANTITY"] = trial_df["Seat Days"].apply(to_numeric)
    trial_df["UNIT_PRICE"] = None
    trial_df["AMOUNT"] = None
    trial_df["CURRENCY"] = "USD"

    result_trial = trial_df[TEMPLATE_COLUMNS].copy()
    
    total_seat_days = float(result_full["QUANTITY"].sum())
    
    stats = IngestStats(
        region=region,
        entity_tag=REGIONAL_CONFIG[region]["entity_tag"],
        source_file=file_path,
        billing_month=billing_date,
        period_yyyymm=period_yyyymm,
        raw_rows=raw_count,
        managed_msp_rows=managed_msp_count,
        customer_rows=customer_count,
        full_license_rows=full_count,
        trial_rows=trial_count,
        ingested_rows=len(result_full),
        trial_ingested_rows=len(result_trial),
        total_seat_days=total_seat_days,
    )
    
    return result_full, result_trial, stats


def ensure_table_schema(conn, reset: bool) -> None:
    """Create or validate schema for ESET_USAGE table."""
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


def ensure_trial_table_schema(conn, reset: bool = False) -> None:
    """Create or validate schema for ESET trial quarantine table."""
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {TRIAL_TABLE_NAME} (
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
            cursor.execute(f"DROP TABLE IF EXISTS {TRIAL_TABLE_NAME}")
        cursor.execute(create_sql)


def period_exists(conn, table_name: str, billing_month: datetime) -> bool:
    """Check if period already exists in table."""
    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT COUNT(*) FROM {table_name} "
            "WHERE BILLING_MONTH = %s AND UPPER(COALESCE(VENDOR, '')) = UPPER(%s)",
            (billing_month.strftime("%Y-%m-%d"), VENDOR_NAME),
        )
        return int(cursor.fetchone()[0]) > 0


def delete_period(conn, table_name: str, billing_month: datetime) -> None:
    """Delete period from table."""
    with conn.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {table_name} "
            "WHERE BILLING_MONTH = %s AND UPPER(COALESCE(VENDOR, '')) = UPPER(%s)",
            (billing_month.strftime("%Y-%m-%d"), VENDOR_NAME),
        )


def load_to_snowflake(
    conn,
    df: pd.DataFrame,
    billing_month: datetime,
    table_name: str,
    reset: bool,
    replace_month: bool,
) -> int:
    """Load DataFrame to Snowflake table. Returns row count loaded."""
    if df.empty:
        return 0
    
    if replace_month:
        delete_period(conn, table_name, billing_month)
    elif period_exists(conn, table_name, billing_month):
        raise RuntimeError(
            f"Data already exists for {billing_month.strftime('%Y-%m')} in {table_name}; "
            f"use --replace-month to overwrite"
        )
    
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


def write_audit_report(script_dir: Path, stats_rows: List[IngestStats]) -> Path:
    """Write ingestion audit report."""
    output_dir = script_dir.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"eset_ingest_audit_{stamp}.txt"
    
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write("ESET Ingestion Audit\n")
        handle.write("=" * 100 + "\n\n")
        
        for stats in stats_rows:
            handle.write(f"Region: {stats.region} ({stats.entity_tag})\n")
            handle.write(f"Month: {stats.period_yyyymm}\n")
            handle.write(f"Source file: {stats.source_file.name}\n")
            handle.write(f"Raw rows: {stats.raw_rows:,}\n")
            handle.write(f"  Managed Msp (kept): {stats.managed_msp_rows:,}\n")
            handle.write(f"  Customer (excluded/validation): {stats.customer_rows:,}\n")
            handle.write(f"Full license rows: {stats.full_license_rows:,}\n")
            handle.write(f"Trial rows (quarantined): {stats.trial_rows:,}\n")
            handle.write(f"Ingested rows: {stats.ingested_rows:,}\n")
            handle.write(f"Trial ingested rows: {stats.trial_ingested_rows:,}\n")
            handle.write(f"Total seat days: {stats.total_seat_days:,.2f}\n")
            handle.write("-" * 100 + "\n")
        
        # Summary
        handle.write("\nSUMMARY (All Regions)\n")
        total_raw = sum(s.raw_rows for s in stats_rows)
        total_managed = sum(s.managed_msp_rows for s in stats_rows)
        total_customer = sum(s.customer_rows for s in stats_rows)
        total_full = sum(s.full_license_rows for s in stats_rows)
        total_trial = sum(s.trial_rows for s in stats_rows)
        total_ingested = sum(s.ingested_rows for s in stats_rows)
        total_trial_ingested = sum(s.trial_ingested_rows for s in stats_rows)
        total_seat_days = sum(s.total_seat_days for s in stats_rows)
        
        handle.write(f"Raw rows: {total_raw:,}\n")
        handle.write(f"  Managed Msp (kept): {total_managed:,}\n")
        handle.write(f"  Customer (excluded/validation): {total_customer:,}\n")
        handle.write(f"Full license rows: {total_full:,}\n")
        handle.write(f"Trial rows (quarantined): {total_trial:,}\n")
        handle.write(f"Ingested rows: {total_ingested:,}\n")
        handle.write(f"Trial ingested rows: {total_trial_ingested:,}\n")
        handle.write(f"Total seat days: {total_seat_days:,.2f}\n")
    
    return output_path


def get_connection():
    """Get Snowflake connection."""
    sys.path.insert(0, str(WORKSPACE_ROOT))
    from TEMPLATES.Python.connection import get_snowflake_connection
    
    return get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest ESET regional license usage CSVs")
    parser.add_argument(
        "--source-root",
        default=str(DEFAULT_SOURCE_ROOT),
        help="Root folder containing month folders like 06_JUN_2026",
    )
    
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--month", help="Month in YYYY-MM format")
    scope.add_argument("--all-months", action="store_true", help="Process all month folders")
    
    parser.add_argument("--dry-run", action="store_true", help="Do not write to Snowflake")
    parser.add_argument(
        "--replace-month",
        action="store_true",
        help="Delete and reload existing rows for each processed month",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate ESET_USAGE before loading (ignored for --dry-run)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    
    source_root = Path(args.source_root)
    if not source_root.exists():
        raise FileNotFoundError(f"Source root not found: {source_root}")
    
    script_dir = Path(__file__).resolve().parent
    
    # Discover months
    if args.month:
        month_folders = [month_folder_for_period(source_root, args.month)]
        target_months = [args.month]
    else:
        # Discover all MM_MON_YYYY folders
        month_folders = sorted([
            p for p in source_root.iterdir()
            if p.is_dir() and re.match(r"^\d{2}_[A-Z]{3}_\d{4}$", p.name, re.IGNORECASE)
        ], key=lambda p: p.name)
        target_months = [
            f"{p.name.split('_')[2]}-{p.name.split('_')[0]}"
            for p in month_folders
        ]
    
    if not month_folders:
        raise RuntimeError(f"No month folders found in {source_root}")
    
    all_ingested_frames: List[pd.DataFrame] = []
    all_trial_frames: List[pd.DataFrame] = []
    all_stats: List[IngestStats] = []

    invoice_conn = get_connection()
    try:
        partner_rates, parent_rates = load_invoice_rates(invoice_conn)
    finally:
        invoice_conn.close()
    print(f"Loaded {len(partner_rates):,} ESET invoice partner rates and {len(parent_rates):,} parent/entity fallback rates.")
    
    for month_folder, target_month in zip(month_folders, target_months):
        print(f"\nProcessing {target_month} from {month_folder.name}...")
        
        for region_key in ["US", "UK", "AU_NZ"]:
            regional_file = locate_regional_file(month_folder, region_key)
            if regional_file is None:
                print(f"  SKIP {region_key}: file not found")
                continue
            
            ingested_df, trial_df, stats = process_regional_file(
                regional_file,
                region_key,
                month_folder,
                partner_rates,
                parent_rates,
            )
            
            print(
                f"  {stats.region}: raw={stats.raw_rows:,}, "
                f"managed_msp={stats.managed_msp_rows:,}, "
                f"customer={stats.customer_rows:,}, "
                f"full={stats.full_license_rows:,}, "
                f"trial={stats.trial_rows:,}, "
                f"ingested={stats.ingested_rows:,}, "
                f"seat_days={stats.total_seat_days:,.2f}"
            )
            
            all_ingested_frames.append(ingested_df)
            if not trial_df.empty:
                all_trial_frames.append(trial_df)
            all_stats.append(stats)
    
    # Combine all frames
    combined_ingested = (
        pd.concat(all_ingested_frames, ignore_index=True)
        if all_ingested_frames
        else pd.DataFrame(columns=TEMPLATE_COLUMNS)
    )
    combined_trial = (
        pd.concat(all_trial_frames, ignore_index=True)
        if all_trial_frames
        else pd.DataFrame(columns=TEMPLATE_COLUMNS)
    )
    
    # Write audit
    audit_path = write_audit_report(script_dir, all_stats)
    print(f"\nAudit report written to: {audit_path}")
    
    if args.dry_run:
        total_rows = len(combined_ingested)
        total_trial = len(combined_trial)
        total_seat_days = combined_ingested["QUANTITY"].sum() if not combined_ingested.empty else 0.0
        print(f"Dry run complete. rows={total_rows:,}, trial_rows={total_trial:,}, seat_days={total_seat_days:,.2f}")
        return
    
    conn = get_connection()
    try:
        ensure_table_schema(conn, reset=args.reset)
        ensure_trial_table_schema(conn, reset=args.reset)
        
        # Load ingested (full license) rows
        if not combined_ingested.empty:
            unique_months = combined_ingested["BILLING_MONTH"].unique()
            for month in sorted(unique_months):
                billing_month = pd.Timestamp(month).to_pydatetime()
                month_df = combined_ingested[combined_ingested["BILLING_MONTH"] == month]
                rows_loaded = load_to_snowflake(
                    conn,
                    month_df,
                    billing_month,
                    TABLE_NAME,
                    reset=args.reset,
                    replace_month=args.replace_month or args.reset,
                )
                print(f"Loaded {rows_loaded:,} rows for {billing_month.strftime('%Y-%m')} into ESET_USAGE")
        
        # Load trial rows (append only, no duplicates check)
        if not combined_trial.empty:
            ok, chunks, rows, _ = write_pandas(
                conn,
                combined_trial,
                table_name="ESET_TRIAL_QUARANTINE",
                schema="DBT_NFOLD_TRANSFORMATION",
                database="ANALYTICS_DEV",
                quote_identifiers=False,
            )
            if ok:
                print(f"Loaded {rows:,} trial rows into ESET_TRIAL_QUARANTINE")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

