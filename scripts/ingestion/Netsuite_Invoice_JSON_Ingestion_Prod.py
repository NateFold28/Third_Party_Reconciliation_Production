"""Build THIRD_PARTY_RECON_VENDOR_INVOICES from NETSUITE.DBO.PARSED_VENDOR_DATA.

Source : NETSUITE.DBO.PARSED_VENDOR_DATA
           Rows = one invoice line per vendor file parsed by the NetSuite
           JSON-extraction pipeline.  Key columns discovered at runtime (see
           _probe_schema() below); canonical names normalised into:
               vendor_name, file_path, billing_month, partner, sku,
               quantity, unit_price, amount

Target : ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
           VENDOR            VARCHAR
           BILLING_MONTH     DATE
           PARTNER           VARCHAR
           VENDOR_PRODUCT_SKU VARCHAR
           QUANTITY          NUMBER
           UNIT_PRICE        NUMBER(18,6)
           AMOUNT            NUMBER(18,6)
           SOURCE_FILE_PATH  VARCHAR   -- audit trail

Run:
    python -u scripts\_build_vendor_invoices.py [--dry-run] [--vendor ESET]

Options:
    --dry-run   Print row counts and schema; do NOT write to Snowflake.
    --vendor V  Filter PARSED_VENDOR_DATA to vendor_name ILIKE '%V%' (default: all).
    --month  M  Filter to a single billing month YYYY-MM (default: all).
"""
from __future__ import annotations

import sys
import argparse
import re
from datetime import datetime

import pandas as pd

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────

TARGET_TABLE = "ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES"

# Canonical vendor name normalisation: raw vendor_name value → clean label
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

# ── Schema discovery ──────────────────────────────────────────────────────────

# PARSED_VENDOR_DATA column aliases → canonical name
# Extend this list if the actual table uses different names.
_ALIAS_MAP: dict[str, str] = {
    # vendor
    "vendor_name":     "vendor_name",
    "vendor":          "vendor_name",
    "vendor_id":       "vendor_name",
    # file path
    "file_path":       "file_path",
    "source_file":     "file_path",
    "file_name":       "file_path",
    # billing month
    "billing_month":   "billing_month",
    "invoice_month":   "billing_month",
    "invoice_date":    "billing_month",
    "period":          "billing_month",
    "month":           "billing_month",
    # partner / customer
    "partner":         "partner",
    "partner_name":    "partner",
    "customer":        "partner",
    "customer_name":   "partner",
    "account_name":    "partner",
    # sku / product
    "sku":             "sku",
    "product_sku":     "sku",
    "product_code":    "sku",
    "item_code":       "sku",
    "item":            "sku",
    "product":         "sku",
    "description":     "sku",        # last-resort fallback
    # quantity
    "quantity":        "quantity",
    "qty":             "quantity",
    "units":           "quantity",
    "seats":           "quantity",
    # unit price
    "unit_price":      "unit_price",
    "price":           "unit_price",
    "rate":            "unit_price",
    "unit_cost":       "unit_price",
    # amount / line total
    "amount":          "amount",
    "line_amount":     "amount",
    "total":           "amount",
    "total_amount":    "amount",
    "extended_price":  "amount",
    "ext_price":       "amount",
}


def _probe_schema(cur) -> list[str]:
    """Return actual column names from PARSED_VENDOR_DATA."""
    cur.execute("""
        SELECT COLUMN_NAME
        FROM NETSUITE.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'DBO'
          AND TABLE_NAME   = 'PARSED_VENDOR_DATA'
        ORDER BY ORDINAL_POSITION
    """)
    return [r[0] for r in cur.fetchall()]


def _map_columns(raw_cols: list[str]) -> dict[str, str]:
    """Map raw column names to canonical names using _ALIAS_MAP.
    Returns {raw_col: canonical} for recognised columns only.
    """
    mapping: dict[str, str] = {}
    seen_canonical: set[str] = set()
    for col in raw_cols:
        canonical = _ALIAS_MAP.get(col.lower())
        if canonical and canonical not in seen_canonical:
            mapping[col] = canonical
            seen_canonical.add(canonical)
    return mapping


# ── Normalisation helpers ─────────────────────────────────────────────────────

def _normalise_vendor(raw: str) -> str | None:
    """Map a raw vendor_name value to the canonical pipeline label."""
    if not raw:
        return None
    lc = raw.lower()
    for key, label in VENDOR_NAME_MAP.items():
        if key in lc:
            return label
    return raw.strip()  # keep unknown as-is


def _normalise_month(val) -> str | None:
    """Return 'YYYY-MM-DD' first-of-month string, or None."""
    if pd.isna(val) or val is None:
        return None
    s = str(val).strip()
    # Already ISO date
    m = re.match(r"^(\d{4})-(\d{1,2})(?:-\d{1,2})?$", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-01"
    # YYYY_MM from file path token
    m = re.search(r"(\d{4})_(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-01"
    # Try pandas parse
    try:
        dt = pd.to_datetime(s)
        return dt.strftime("%Y-%m-01")
    except Exception:
        return None


def _to_float(val) -> float | None:
    try:
        f = float(str(val).replace(",", "").strip())
        return f if pd.notna(f) else None
    except Exception:
        return None


# ── Core logic ────────────────────────────────────────────────────────────────

def fetch_raw(cur, vendor_filter: str | None, month_filter: str | None) -> pd.DataFrame:
    """Pull all rows from PARSED_VENDOR_DATA, optionally filtered."""
    where_clauses: list[str] = []
    if vendor_filter:
        safe = vendor_filter.replace("'", "''")
        where_clauses.append(f"vendor_name ILIKE '%{safe}%'")
    if month_filter:
        safe = month_filter.replace("'", "''")
        where_clauses.append(f"file_path ILIKE '%{safe}%'")
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    sql = f"SELECT * FROM NETSUITE.DBO.PARSED_VENDOR_DATA {where_sql}"
    print(f"\nFetching: {sql[:200]}")
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    print(f"  {len(rows):,} raw rows returned.")
    return pd.DataFrame(rows, columns=cols)


def transform(df: pd.DataFrame, col_map: dict[str, str]) -> pd.DataFrame:
    """Rename and normalise columns; return target-shaped DataFrame."""
    # Rename to canonical
    df = df.rename(columns=col_map)

    # Derive billing_month from file_path if column not directly available
    if "billing_month" not in df.columns and "file_path" in df.columns:
        df["billing_month"] = df["file_path"].apply(_normalise_month)
    elif "billing_month" in df.columns:
        df["billing_month"] = df["billing_month"].apply(_normalise_month)

    # Normalise vendor
    if "vendor_name" in df.columns:
        df["VENDOR"] = df["vendor_name"].apply(_normalise_vendor)
    else:
        df["VENDOR"] = None

    df["BILLING_MONTH"]      = df.get("billing_month")
    df["PARTNER"]            = df.get("partner", pd.Series(dtype=str)).fillna("Unknown")
    df["VENDOR_PRODUCT_SKU"] = df.get("sku",    pd.Series(dtype=str)).fillna("UNKNOWN")
    df["QUANTITY"]           = df.get("quantity", pd.Series(dtype=float)).apply(_to_float)
    df["UNIT_PRICE"]         = df.get("unit_price", pd.Series(dtype=float)).apply(_to_float)
    df["AMOUNT"]             = df.get("amount", pd.Series(dtype=float)).apply(_to_float)
    df["SOURCE_FILE_PATH"]   = df.get("file_path", pd.Series(dtype=str))

    # Derive AMOUNT from QUANTITY * UNIT_PRICE where missing
    mask_no_amount = df["AMOUNT"].isna() & df["QUANTITY"].notna() & df["UNIT_PRICE"].notna()
    df.loc[mask_no_amount, "AMOUNT"] = (
        df.loc[mask_no_amount, "QUANTITY"] * df.loc[mask_no_amount, "UNIT_PRICE"]
    ).round(6)

    # Derive UNIT_PRICE from AMOUNT / QUANTITY where missing
    mask_no_price = (
        df["UNIT_PRICE"].isna()
        & df["AMOUNT"].notna()
        & df["QUANTITY"].notna()
        & (df["QUANTITY"] != 0)
    )
    df.loc[mask_no_price, "UNIT_PRICE"] = (
        df.loc[mask_no_price, "AMOUNT"] / df.loc[mask_no_price, "QUANTITY"]
    ).round(6)

    target_cols = [
        "VENDOR", "BILLING_MONTH", "PARTNER",
        "VENDOR_PRODUCT_SKU", "QUANTITY", "UNIT_PRICE", "AMOUNT",
        "SOURCE_FILE_PATH",
    ]
    out = df[target_cols].copy()
    out = out[out["VENDOR"].notna() & out["BILLING_MONTH"].notna()]
    return out.reset_index(drop=True)


def write_to_snowflake(conn, cur, df: pd.DataFrame) -> None:
    """Recreate THIRD_PARTY_RECON_VENDOR_INVOICES and bulk-load the DataFrame."""
    print(f"\nCreating {TARGET_TABLE} ({len(df):,} rows)...")

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

    # Write via pandas → Snowflake write_pandas (fast path)
    from snowflake.connector.pandas_tools import write_pandas

    success, n_chunks, n_rows, _ = write_pandas(
        conn,
        df,
        table_name="THIRD_PARTY_RECON_VENDOR_INVOICES",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
        auto_create_table=False,
        overwrite=False,
    )
    if success:
        print(f"  Loaded {n_rows:,} rows in {n_chunks} chunk(s). Done.")
    else:
        raise RuntimeError("write_pandas reported failure — check Snowflake logs.")


def validate_against_live(cur, new_df: pd.DataFrame) -> None:
    """Compare new_df against the live THIRD_PARTY_RECON_VENDOR_INVOICES table.
    Prints per-vendor row count, total amount, and unique SKU deltas.
    Does not fail the script; only reports.
    """
    print("\n" + "=" * 70)
    print("VALIDATION: new output vs live THIRD_PARTY_RECON_VENDOR_INVOICES")
    print("=" * 70)
    try:
        cur.execute("""
            SELECT
                VENDOR,
                COUNT(*)          AS rows,
                COUNT(DISTINCT VENDOR_PRODUCT_SKU) AS skus,
                COUNT(DISTINCT BILLING_MONTH)      AS months,
                SUM(AMOUNT)       AS total_amount
            FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
            GROUP BY VENDOR
            ORDER BY VENDOR
        """)
        live_rows = cur.fetchall()
    except Exception as e:
        print(f"  Could not query live table: {e}")
        print("  (Table may not exist yet — run without --validate first.)")
        return

    if not live_rows:
        print("  Live table is EMPTY.")
        return

    live_map = {r[0]: {"rows": r[1], "skus": r[2], "months": r[3], "amount": r[4]} for r in live_rows}

    # Build same summary from new_df
    new_map: dict = {}
    for vendor, grp in new_df.groupby("VENDOR"):
        new_map[vendor] = {
            "rows":   len(grp),
            "skus":   grp["VENDOR_PRODUCT_SKU"].nunique(),
            "months": grp["BILLING_MONTH"].nunique(),
            "amount": grp["AMOUNT"].sum(),
        }

    all_vendors = sorted(set(list(live_map.keys()) + list(new_map.keys())))
    print(f"\n{'VENDOR':<15} {'LIVE rows':>10} {'NEW rows':>10} {'ROW diff':>10} "
          f"{'LIVE $':>12} {'NEW $':>12} {'$ diff':>12}")
    print("-" * 85)
    ok = True
    for v in all_vendors:
        l = live_map.get(v, {})
        n = new_map.get(v, {})
        lrows = l.get("rows", 0)
        nrows = n.get("rows", 0)
        lamt  = l.get("amount") or 0.0
        namt  = n.get("amount") or 0.0
        rdiff = nrows - lrows
        adiff = namt - lamt
        flag  = "  " if abs(rdiff) == 0 and abs(adiff) < 1.0 else "* "
        if flag == "* ":
            ok = False
        print(f"{flag}{v:<13} {lrows:>10,} {nrows:>10,} {rdiff:>+10,} "
              f"${lamt:>11,.0f} ${namt:>11,.0f} ${adiff:>+11,.0f}")
    print()
    if ok:
        print("  All vendors match within tolerance. New script output is consistent.")
    else:
        print("  * = differences detected. Review above before writing.")
    print("=" * 70)



        df.groupby("VENDOR")
        .agg(
            rows=("VENDOR", "size"),
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
        amt = f"${r.total_amount:>12,.2f}" if pd.notna(r.total_amount) else "         N/A"
        print(f"{r.VENDOR:<15} {int(r.rows):>7,} {int(r.months):>7} {int(r.partners):>9,} {int(r.skus):>7,} {amt}")
    print(f"\nTotal rows: {len(df):,}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print summary; do not write.")
    parser.add_argument("--validate", action="store_true", help="Compare output vs live table; implies dry-run.")
    parser.add_argument("--vendor",  default=None, help="Filter vendor_name ILIKE '%%V%%'.")
    parser.add_argument("--month",   default=None, help="Filter file_path ILIKE '%%YYYY_MM%%'.")
    args = parser.parse_args()

    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    cur = conn.cursor()

    # Discover source schema
    print("Probing NETSUITE.DBO.PARSED_VENDOR_DATA schema...")
    raw_cols = _probe_schema(cur)
    print(f"  Columns found: {raw_cols}")
    col_map = _map_columns(raw_cols)
    missing = {c for c in ["vendor_name", "sku", "billing_month", "partner"]
               if c not in col_map.values()}
    if missing:
        print(f"\nWARNING: could not map these canonical columns: {missing}")
        print("  Check _ALIAS_MAP in this script and add the actual column names.")

    # Fetch + transform
    raw_df  = fetch_raw(cur, args.vendor, args.month)
    out_df  = transform(raw_df, col_map)
    print_summary(out_df)

    if args.validate:
        validate_against_live(cur, out_df)
        print("\n[VALIDATE] No rows written.")
    elif args.dry_run:
        print("\n[DRY RUN] No rows written.")
    else:
        write_to_snowflake(conn, cur, out_df)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
