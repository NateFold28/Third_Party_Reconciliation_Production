"""Populate THIRD_PARTY_RECON_VENDOR_INVOICES from NETSUITE.DBO.PARSED_VENDOR_DATA.

WHAT THIS TABLE IS AND HOW UNIT PRICES FLOW
============================================
THIRD_PARTY_RECON_VENDOR_INVOICES is the canonical rate table that drives dynamic
unit-price back-fill for all 9 vendor usage streams:

  VENDOR_INVOICES
      -> sql/00b_backfill_invoice_prices.sql   (runs every pipeline cycle)
      -> THIRD_PARTY_RECON_VENDOR_USAGE_PROD   (UNIT_PRICE + AMOUNT filled)

00b uses LAST_VALUE IGNORE NULLS over (PARTITION BY vendor, sku ORDER BY billing_month).
This means: if a vendor has no invoice for the current month, the most recent prior
month's rates are carried forward automatically.  The table only needs to be
refreshed when new invoice PDFs are parsed by Engineering.

SOURCE STRUCTURE
================
NETSUITE.DBO.PARSED_VENDOR_DATA columns:
  TRANSACTION_ID, VENDOR_NAME, FILE_PATH, FILE_NAME, PARSED_AT, PARSED_DOCUMENT

PARSED_DOCUMENT is a JSON object: {"pages": [{"content": "<raw PDF text>"}, ...]}
Each row = one invoice PDF file.  The raw text must be parsed per-vendor to extract
structured line items (partner, sku, quantity, unit_price, amount).

HOW TO ADD OR UPDATE A VENDOR PARSER
=====================================
1. Run this to inspect the raw PDF text for a vendor/month:
       python -u scripts/ingestion/Netsuite_Invoice_JSON_Ingestion_Prod.py --inspect --vendor Auvik --month 2026-05
2. Write a parse_<vendor>(text, file_path) -> list[dict] function below.
   Each dict must have: partner, sku, quantity, unit_price, amount.
3. Register the function in PER_VENDOR_PARSERS (vendor name key -> callable).
4. Test: python -u scripts/ingestion/Netsuite_Invoice_JSON_Ingestion_Prod.py --vendor Auvik --dry-run
5. Validate: python -u scripts/ingestion/Netsuite_Invoice_JSON_Ingestion_Prod.py --validate
6. Write: python -u scripts/ingestion/Netsuite_Invoice_JSON_Ingestion_Prod.py --vendor Auvik

TARGET TABLE
============
ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
  VENDOR             VARCHAR
  BILLING_MONTH      DATE       (first-of-month)
  PARTNER            VARCHAR
  VENDOR_PRODUCT_SKU VARCHAR
  QUANTITY           NUMBER(18,6)
  UNIT_PRICE         NUMBER(18,6)
  AMOUNT             NUMBER(18,6)
  SOURCE_FILE_PATH   VARCHAR    (audit trail)

Run:
    python -u scripts/ingestion/Netsuite_Invoice_JSON_Ingestion_Prod.py [options]

Options:
    --dry-run         Parse and summarise; do NOT write to Snowflake.
    --validate        Compare parsed output vs live VENDOR_INVOICES table; implies dry-run.
    --inspect         Print raw PDF text for matching rows (use with --vendor/--month).
    --vendor V        Filter PARSED_VENDOR_DATA: vendor_name ILIKE '%V%'  (default: all).
    --month  YYYY-MM  Filter file_path for a specific month token YYYY_MM  (default: all).
    --append          Append to existing table instead of replacing matching rows.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from typing import Callable

import pandas as pd

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

TARGET_TABLE = "ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES"

# Maps a vendor key (lower-case substring of VENDOR_NAME) -> canonical label
VENDOR_NAME_MAP: dict[str, str] = {
    "acronis":     "Acronis",
    "auvik":       "Auvik",
    "bitdefender": "Bitdefender",
    "eset":        "ESET",
    "exium":       "Exium",
    "keepit":      "KeepIT",
    "proofpoint":  "Proofpoint",
    "sentinelone": "SentinelOne",
    "webroot":     "Webroot",
}

# ── Per-vendor PDF text parsers ───────────────────────────────────────────────
# Each parser receives:
#   text      : str  - full concatenated text from all pages of one invoice PDF
#   file_path : str  - source file path (for debugging)
# Returns: list[dict] with keys: partner, sku, quantity, unit_price, amount
#
# Add one function per vendor and register in PER_VENDOR_PARSERS below.

def _parse_generic(text: str, file_path: str) -> list[dict]:
    """Placeholder — returns empty until a vendor-specific parser is implemented.
    Run with --inspect to view the raw text and write a real parser."""
    return []


# Register vendor parsers here.  Key = lower-case substring of VENDOR_NAME.
PER_VENDOR_PARSERS: dict[str, Callable[[str, str], list[dict]]] = {
    "acronis":     _parse_generic,
    "auvik":       _parse_generic,
    "bitdefender": _parse_generic,
    "eset":        _parse_generic,
    "exium":       _parse_generic,
    "keepit":      _parse_generic,
    "proofpoint":  _parse_generic,
    "sentinelone": _parse_generic,
    "webroot":     _parse_generic,
}


# ── Helper utilities ──────────────────────────────────────────────────────────

def _normalise_vendor(raw: str) -> str | None:
    if not raw:
        return None
    lc = raw.lower()
    for key, label in VENDOR_NAME_MAP.items():
        if key in lc:
            return label
    return raw.strip()


def _month_from_path(file_path: str) -> str | None:
    """Extract billing month YYYY-MM-01 from path token like 2026_05."""
    m = re.search(r"(\d{4})_(\d{2})", file_path or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-01"
    return None


def _to_float(val) -> float | None:
    try:
        return float(str(val).replace(",", "").strip())
    except Exception:
        return None


def _extract_text(parsed_doc) -> str:
    """Pull all page content text from a PARSED_DOCUMENT JSON object."""
    try:
        doc = json.loads(parsed_doc) if isinstance(parsed_doc, str) else parsed_doc
        if not isinstance(doc, dict):
            return ""
        pages = doc.get("pages", [])
        return "\n\n".join(p.get("content", "") for p in pages if isinstance(p, dict))
    except Exception:
        return ""


def _parser_for_vendor(vendor_label: str | None) -> Callable[[str, str], list[dict]]:
    if not vendor_label:
        return _parse_generic
    lc = vendor_label.lower()
    for key, fn in PER_VENDOR_PARSERS.items():
        if key in lc:
            return fn
    return _parse_generic


# ── Core ──────────────────────────────────────────────────────────────────────

def fetch_raw(cur, vendor_filter: str | None, month_filter: str | None) -> list[tuple]:
    clauses: list[str] = []
    if vendor_filter:
        s = vendor_filter.replace("'", "''")
        clauses.append(f"VENDOR_NAME ILIKE '%{s}%'")
    if month_filter:
        tok = month_filter.replace("-", "_")
        tok = tok.replace("'", "''")
        clauses.append(f"FILE_PATH ILIKE '%{tok}%'")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT VENDOR_NAME, FILE_PATH, PARSED_DOCUMENT
        FROM NETSUITE.DBO.PARSED_VENDOR_DATA {where}
        ORDER BY FILE_PATH
    """
    print(f"Fetching from PARSED_VENDOR_DATA ... ", end="", flush=True)
    cur.execute(sql)
    rows = cur.fetchall()
    print(f"{len(rows):,} invoice files.")
    return rows


def parse_all(rows: list[tuple]) -> pd.DataFrame:
    records: list[dict] = []
    for vendor_name, file_path, parsed_doc in rows:
        vendor = _normalise_vendor(vendor_name)
        billing_month = _month_from_path(file_path)
        if not billing_month:
            continue
        text = _extract_text(parsed_doc)
        parser = _parser_for_vendor(vendor)
        items = parser(text, file_path)
        for item in items:
            records.append({
                "VENDOR":             vendor,
                "BILLING_MONTH":      billing_month,
                "PARTNER":            item.get("partner") or "Unknown",
                "VENDOR_PRODUCT_SKU": item.get("sku") or "UNKNOWN",
                "QUANTITY":           _to_float(item.get("quantity")),
                "UNIT_PRICE":         _to_float(item.get("unit_price")),
                "AMOUNT":             _to_float(item.get("amount")),
                "SOURCE_FILE_PATH":   file_path,
            })
    if not records:
        return pd.DataFrame(columns=[
            "VENDOR", "BILLING_MONTH", "PARTNER", "VENDOR_PRODUCT_SKU",
            "QUANTITY", "UNIT_PRICE", "AMOUNT", "SOURCE_FILE_PATH",
        ])
    return pd.DataFrame(records)


def print_summary(df: pd.DataFrame) -> None:
    if df.empty:
        print("\nNo line items extracted. All vendor parsers may still be stubs.")
        print("Run with --inspect to view raw PDF text, then implement per-vendor parsers.")
        return
    by_vendor = (
        df.groupby("VENDOR")
        .agg(
            row_cnt=("VENDOR", "size"),
            months=("BILLING_MONTH", "nunique"),
            partners=("PARTNER", "nunique"),
            skus=("VENDOR_PRODUCT_SKU", "nunique"),
            total_amount=("AMOUNT", "sum"),
        )
        .reset_index()
    )
    print(f"\n{'VENDOR':<15} {'ROWS':>7} {'MONTHS':>7} {'PARTNERS':>9} {'SKUS':>7} {'TOTAL_AMOUNT':>14}")
    print("-" * 65)
    for _, r in by_vendor.iterrows():
        amt = f"${r.total_amount:>12,.2f}" if pd.notna(r.total_amount) else "           N/A"
        print(f"{r.VENDOR:<15} {int(r.row_cnt):>7,} {int(r.months):>7} {int(r.partners):>9,} {int(r.skus):>7,} {amt}")
    print(f"\nTotal rows: {len(df):,}")


def inspect_raw(rows: list[tuple]) -> None:
    """Print raw PDF text for each invoice file (used with --inspect flag)."""
    for vendor_name, file_path, parsed_doc in rows[:5]:
        print(f"\n{'='*70}")
        print(f"VENDOR: {vendor_name}")
        print(f"FILE:   {file_path}")
        print(f"{'='*70}")
        text = _extract_text(parsed_doc)
        print(text[:3000] if text else "(empty text)")
    if len(rows) > 5:
        print(f"\n... {len(rows)-5} more files not shown (use --vendor + --month to narrow down).")


def validate_against_live(cur, new_df: pd.DataFrame) -> None:
    """Compare new_df vs live THIRD_PARTY_RECON_VENDOR_INVOICES. Does not write."""
    print("\n" + "=" * 70)
    print("VALIDATION: parsed output vs live THIRD_PARTY_RECON_VENDOR_INVOICES")
    print("=" * 70)
    try:
        cur.execute("""
            SELECT
                VENDOR,
                COUNT(*)                          AS row_cnt,
                COUNT(DISTINCT VENDOR_PRODUCT_SKU) AS sku_cnt,
                COUNT(DISTINCT BILLING_MONTH)      AS month_cnt,
                SUM(AMOUNT)                        AS total_amount
            FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
            GROUP BY VENDOR
            ORDER BY VENDOR
        """)
        live = {r[0]: {"rows": int(r[1]), "skus": int(r[2]), "months": int(r[3]), "amount": float(r[4] or 0)}
                for r in cur.fetchall()}
    except Exception as e:
        print(f"  Could not query live table: {e}")
        return

    if not live:
        print("  Live table is EMPTY. Run without --validate to write first.")
        return

    new_map: dict = {}
    for vendor, grp in new_df.groupby("VENDOR"):
        new_map[vendor] = {
            "rows":   len(grp),
            "skus":   grp["VENDOR_PRODUCT_SKU"].nunique(),
            "months": grp["BILLING_MONTH"].nunique(),
            "amount": float(grp["AMOUNT"].sum()),
        }

    all_vendors = sorted(set(list(live.keys()) + list(new_map.keys())))
    print(f"\n{'VENDOR':<15} {'LIVE rows':>10} {'NEW rows':>10} {'ROW diff':>10} "
          f"{'LIVE $':>12} {'NEW $':>12} {'$ diff':>12}")
    print("-" * 85)
    ok = True
    for v in all_vendors:
        l = live.get(v, {})
        n = new_map.get(v, {})
        lrows = l.get("rows", 0)
        nrows = n.get("rows", 0)
        lamt  = l.get("amount", 0.0)
        namt  = n.get("amount", 0.0)
        rdiff = nrows - lrows
        adiff = namt - lamt
        flag = "  " if abs(rdiff) == 0 and abs(adiff) < 1.0 else "* "
        if flag == "* ":
            ok = False
        print(f"{flag}{v:<13} {lrows:>10,} {nrows:>10,} {rdiff:>+10,} "
              f"${lamt:>11,.0f} ${namt:>11,.0f} ${adiff:>+11,.0f}")
    print()
    if ok:
        print("  All vendors match. New parser output is consistent with live table.")
    else:
        print("  * = differences detected. Review above before writing to Snowflake.")
    print("=" * 70)

    # Summary of live table for reference
    print("\nLive THIRD_PARTY_RECON_VENDOR_INVOICES at a glance:")
    cur.execute("""
        SELECT VENDOR, COUNT(*) AS row_cnt,
               MIN(BILLING_MONTH) AS first_month, MAX(BILLING_MONTH) AS last_month,
               COUNT(DISTINCT VENDOR_PRODUCT_SKU) AS skus, SUM(AMOUNT) AS total_amount
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
        GROUP BY VENDOR ORDER BY VENDOR
    """)
    print(f"{'VENDOR':<15} {'ROWS':>7} {'FIRST':>10} {'LAST':>10} {'SKUS':>6} {'TOTAL_AMOUNT':>14}")
    print("-" * 68)
    for r in cur.fetchall():
        amt = f"${r[5]:>12,.0f}" if r[5] is not None else "           N/A"
        print(f"{r[0]:<15} {r[1]:>7,}  {str(r[2])[:10]:>10}  {str(r[3])[:10]:>10}  {r[4]:>6,} {amt}")


def write_to_snowflake(conn, cur, df: pd.DataFrame, append: bool = False) -> None:
    from snowflake.connector.pandas_tools import write_pandas

    if not append:
        print(f"\nRecreating {TARGET_TABLE} ...")
        cur.execute(f"""
            CREATE OR REPLACE TABLE {TARGET_TABLE} (
                VENDOR             VARCHAR,
                BILLING_MONTH      DATE,
                PARTNER            VARCHAR,
                VENDOR_PRODUCT_SKU VARCHAR,
                QUANTITY           NUMBER(18,6),
                UNIT_PRICE         NUMBER(18,6),
                AMOUNT             NUMBER(18,6),
                SOURCE_FILE_PATH   VARCHAR
            )
        """)
    else:
        print(f"\nAppending {len(df):,} rows to {TARGET_TABLE} ...")

    success, n_chunks, n_rows, _ = write_pandas(
        conn, df,
        table_name="THIRD_PARTY_RECON_VENDOR_INVOICES",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
        auto_create_table=False,
        overwrite=False,
    )
    if success:
        print(f"  Loaded {n_rows:,} rows in {n_chunks} chunk(s).")
    else:
        raise RuntimeError("write_pandas reported failure.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run",  action="store_true", help="Parse + summarise; do not write.")
    parser.add_argument("--validate", action="store_true", help="Compare vs live table; implies dry-run.")
    parser.add_argument("--inspect",  action="store_true", help="Print raw PDF text for matching rows.")
    parser.add_argument("--append",   action="store_true", help="Append rather than replace.")
    parser.add_argument("--vendor",   default=None, help="Filter vendor_name ILIKE '%%V%%'.")
    parser.add_argument("--month",    default=None, help="Filter file_path for month YYYY-MM.")
    args = parser.parse_args()

    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    cur = conn.cursor()

    rows = fetch_raw(cur, args.vendor, args.month)

    if args.inspect:
        inspect_raw(rows)
        conn.close()
        return

    df = parse_all(rows)
    print_summary(df)

    if args.validate:
        validate_against_live(cur, df)
        print("\n[VALIDATE] No rows written.")
    elif args.dry_run:
        print("\n[DRY RUN] No rows written.")
    else:
        if df.empty:
            print("\nNo rows parsed — nothing to write. Implement vendor parsers first.")
        else:
            write_to_snowflake(conn, cur, df, append=args.append)
            print("Done.")

    conn.close()


if __name__ == "__main__":
    main()
