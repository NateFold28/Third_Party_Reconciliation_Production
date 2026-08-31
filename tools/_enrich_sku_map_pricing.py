"""
Populate `RECON_SKU_PRICING` (per (VENDOR, CW_SKU, TIERNUM, BILLING_TYPE) grain)
and add VENDOR_UNIT_PRICE + CW_UNIT_PRICE columns to RECON_SKU_MAP and
THIRD_PARTY_RECON_SKU_MAP_PROD from SKU_Information_PowerBI.xlsx.

Data source of truth:
    C:\\Users\\Nate.Fold\\OneDrive - ConnectWise, Inc\\THIRD_PARTY_RECONCILIATION\\
    SKU_Information_PowerBI.xlsx  (sheet: Export)

Excel columns of interest:
    PRODUCTCODE   -> RECON_SKU_MAP.CW_SKU (join key)
    NAME          -> product display name
    BILLING TYPE  -> EVERGREEN | MONTHLY
    UOM, CUR
    TIERNUM, LOWERBOUND, UPPERBOUND -> volume tier bounds
    Retail Price  -> CW_UNIT_PRICE (what CW charges partner)
    Cost          -> VENDOR_UNIT_PRICE (what vendor charges CW)
    FAMILY, PRODUCT_LINE, SOURCE, SKU_TYPE, STATUS

Design:
- Vendor is inferred by joining PRODUCTCODE -> RECON_SKU_MAP.CW_SKU. Rows that
  do not map to any known CW_SKU in the map are still loaded into the pricing
  table but with VENDOR = NULL (informational only).
- RECON_SKU_MAP gets two flat columns populated from the "default tier" row:
  first choice = TIERNUM=0 EVERGREEN; fallback = lowest TIERNUM EVERGREEN;
  fallback = any row. Multi-tier lookups can join RECON_SKU_PRICING directly.
- Idempotent: CREATE OR REPLACE for both tables.
- Backup created before overwrite of RECON_SKU_MAP.
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

WORKSPACE = Path(r"c:/Users/Nate.Fold/projects")
sys.path.insert(0, str(WORKSPACE))

from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe  # noqa: E402

SOURCE_XLSX = Path(
    r"C:\Users\Nate.Fold\OneDrive - ConnectWise, Inc\THIRD_PARTY_RECONCILIATION\SKU_Information_PowerBI.xlsx"
)


def load_xlsx() -> pd.DataFrame:
    # OneDrive-hosted file may be locked by Excel; use PowerShell Copy-Item
    # (which honours share-locks) to a TEMP path.
    import subprocess
    dest = Path(os.environ["TEMP"]) / "SKU_Information_PowerBI__enrich.xlsx"
    try:
        shutil.copy(SOURCE_XLSX, dest)
    except PermissionError:
        cmd = ["powershell", "-Command",
               f"Copy-Item -LiteralPath '{SOURCE_XLSX}' -Destination '{dest}' -Force"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"could not copy locked xlsx: {result.stderr}")
    df = pd.read_excel(dest, sheet_name="Export")
    df.columns = [str(c).strip() for c in df.columns]
    print(f"  loaded {len(df):,} rows / {len(df.columns)} cols from {dest.name}")
    return df


def build_pricing_frame(xlsx: pd.DataFrame, vendor_by_cw_sku: dict[str, str]) -> pd.DataFrame:
    rename = {
        "PRODUCTCODE": "CW_SKU",
        "NAME": "PRODUCT_NAME",
        "BILLING TYPE": "BILLING_TYPE",
        "TIERNUM": "TIERNUM",
        "LOWERBOUND": "LOWERBOUND",
        "UPPERBOUND": "UPPERBOUND",
        "Retail Price": "CW_UNIT_PRICE",
        "Cost": "VENDOR_UNIT_PRICE",
        "CUR": "CURRENCY",
        "UOM": "UOM",
        "STATUS": "SKU_STATUS",
        "FAMILY": "FAMILY",
        "PRODUCT_LINE": "PRODUCT_LINE",
        "SKU_TYPE": "SKU_TYPE",
        "SOURCE": "SOURCE",
    }
    df = xlsx[list(rename.keys())].rename(columns=rename).copy()
    df["CW_SKU"] = df["CW_SKU"].astype(str).str.strip()
    df = df[df["CW_SKU"].str.len() > 0]
    df["VENDOR"] = df["CW_SKU"].map(vendor_by_cw_sku)
    df["TIERNUM"] = pd.to_numeric(df["TIERNUM"], errors="coerce").astype("Int64")
    df["LOWERBOUND"] = pd.to_numeric(df["LOWERBOUND"], errors="coerce").astype("Int64")
    df["UPPERBOUND"] = pd.to_numeric(df["UPPERBOUND"], errors="coerce").astype("Int64")
    df["CW_UNIT_PRICE"] = pd.to_numeric(df["CW_UNIT_PRICE"], errors="coerce")
    df["VENDOR_UNIT_PRICE"] = pd.to_numeric(df["VENDOR_UNIT_PRICE"], errors="coerce")
    return df


def build_default_price_per_cw_sku(pricing: pd.DataFrame) -> pd.DataFrame:
    """Pick one representative (vendor unit price, cw unit price) per CW_SKU.

    Order of preference:
      1) TIERNUM == 0 AND BILLING_TYPE = 'EVERGREEN'
      2) Lowest TIERNUM AND BILLING_TYPE = 'EVERGREEN'
      3) Lowest TIERNUM (any billing type)
    """
    def rank(row) -> tuple:
        bt = str(row.get("BILLING_TYPE") or "").upper()
        tier = row.get("TIERNUM")
        tier_val = int(tier) if pd.notna(tier) else 99
        return (
            0 if (bt == "EVERGREEN" and tier_val == 0) else 1,
            0 if bt == "EVERGREEN" else 1,
            tier_val,
        )
    pricing = pricing.copy()
    pricing["__rank"] = pricing.apply(rank, axis=1)
    pricing = pricing.sort_values(["CW_SKU", "__rank"])
    default_rows = pricing.groupby("CW_SKU", as_index=False).first()
    return default_rows[["CW_SKU", "VENDOR_UNIT_PRICE", "CW_UNIT_PRICE"]]


def main() -> int:
    print("=== SKU pricing enrichment ===")
    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    cur = conn.cursor()

    # Vendor lookup by CW_SKU (from existing map).
    map_df = fetch_dataframe(
        "SELECT VENDOR, CW_SKU FROM RECON_SKU_MAP WHERE CW_SKU IS NOT NULL",
        conn=conn,
    )
    vendor_by_cw_sku: dict[str, str] = {}
    for _, r in map_df.iterrows():
        key = str(r["CW_SKU"]).strip()
        if key and key not in vendor_by_cw_sku:
            vendor_by_cw_sku[key] = r["VENDOR"]
    print(f"  vendor lookup: {len(vendor_by_cw_sku):,} distinct CW_SKUs across {map_df['VENDOR'].nunique()} vendors")

    xlsx = load_xlsx()
    pricing = build_pricing_frame(xlsx, vendor_by_cw_sku)
    matched = pricing["VENDOR"].notna().sum()
    print(f"  pricing rows: {len(pricing):,} total; {matched:,} matched a known CW_SKU")

    # Coverage per vendor
    cov = (
        pricing[pricing["VENDOR"].notna()]
        .groupby("VENDOR")
        .agg(rows=("CW_SKU", "size"), skus=("CW_SKU", "nunique"))
        .sort_values("skus", ascending=False)
    )
    print("\n  Coverage from SKU_Information_PowerBI per vendor:")
    print(cov.to_string())

    default_prices = build_default_price_per_cw_sku(pricing[pricing["VENDOR"].notna()])
    print(f"\n  default price rows: {len(default_prices):,}")

    # ---- Backup RECON_SKU_MAP before adding columns ----
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"RECON_SKU_MAP_BAK_{stamp}"
    print(f"\n  creating backup table {backup_name} ...")
    cur.execute(f"CREATE TABLE {backup_name} CLONE RECON_SKU_MAP")

    # ---- Rebuild RECON_SKU_PRICING ----
    print("  rebuilding RECON_SKU_PRICING ...")
    cur.execute(
        """
        CREATE OR REPLACE TABLE RECON_SKU_PRICING (
            VENDOR VARCHAR,
            CW_SKU VARCHAR,
            PRODUCT_NAME VARCHAR,
            BILLING_TYPE VARCHAR,
            UOM VARCHAR,
            CURRENCY VARCHAR,
            TIERNUM NUMBER,
            LOWERBOUND NUMBER,
            UPPERBOUND NUMBER,
            VENDOR_UNIT_PRICE FLOAT,
            CW_UNIT_PRICE FLOAT,
            SKU_STATUS VARCHAR,
            FAMILY VARCHAR,
            PRODUCT_LINE VARCHAR,
            SKU_TYPE VARCHAR,
            SOURCE VARCHAR,
            LOAD_TS TIMESTAMP_NTZ
        )
        """
    )

    upload_cols = [
        "VENDOR", "CW_SKU", "PRODUCT_NAME", "BILLING_TYPE", "UOM", "CURRENCY",
        "TIERNUM", "LOWERBOUND", "UPPERBOUND", "VENDOR_UNIT_PRICE", "CW_UNIT_PRICE",
        "SKU_STATUS", "FAMILY", "PRODUCT_LINE", "SKU_TYPE", "SOURCE",
    ]
    upload_df = pricing[upload_cols].copy()
    # snowflake pandas write_pandas
    from snowflake.connector.pandas_tools import write_pandas
    for col in ("TIERNUM", "LOWERBOUND", "UPPERBOUND"):
        upload_df[col] = upload_df[col].astype("object").where(upload_df[col].notna(), None)
    upload_df["LOAD_TS"] = datetime.now()
    ok, nchunks, nrows, _ = write_pandas(
        conn, upload_df, "RECON_SKU_PRICING",
        auto_create_table=False, overwrite=False, quote_identifiers=False,
    )
    print(f"  RECON_SKU_PRICING: {nrows:,} rows loaded (ok={ok})")

    # ---- Add columns to RECON_SKU_MAP if missing ----
    cur.execute(
        """
        SELECT COLUMN_NAME FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION' AND TABLE_NAME='RECON_SKU_MAP'
        """
    )
    map_cols = {r[0] for r in cur.fetchall()}
    if "VENDOR_UNIT_PRICE" not in map_cols:
        print("  adding VENDOR_UNIT_PRICE column to RECON_SKU_MAP ...")
        cur.execute("ALTER TABLE RECON_SKU_MAP ADD COLUMN VENDOR_UNIT_PRICE FLOAT")
    if "CW_UNIT_PRICE" not in map_cols:
        print("  adding CW_UNIT_PRICE column to RECON_SKU_MAP ...")
        cur.execute("ALTER TABLE RECON_SKU_MAP ADD COLUMN CW_UNIT_PRICE FLOAT")

    # Populate default prices back into RECON_SKU_MAP
    print("  populating default prices back into RECON_SKU_MAP ...")
    stage_price = default_prices.copy()
    stage_price.columns = ["CW_SKU", "VENDOR_UNIT_PRICE", "CW_UNIT_PRICE"]
    cur.execute("CREATE OR REPLACE TEMPORARY TABLE __SKU_DEFAULT_PRICES (CW_SKU VARCHAR, VENDOR_UNIT_PRICE FLOAT, CW_UNIT_PRICE FLOAT)")
    ok, _, nrows_stage, _ = write_pandas(
        conn, stage_price, "__SKU_DEFAULT_PRICES",
        auto_create_table=False, overwrite=False, quote_identifiers=False,
    )
    print(f"    staged {nrows_stage:,} default price rows (ok={ok})")

    cur.execute(
        """
        UPDATE RECON_SKU_MAP m
        SET VENDOR_UNIT_PRICE = p.VENDOR_UNIT_PRICE,
            CW_UNIT_PRICE = p.CW_UNIT_PRICE
        FROM __SKU_DEFAULT_PRICES p
        WHERE m.CW_SKU = p.CW_SKU
        """
    )
    updated_map = cur.rowcount
    print(f"    RECON_SKU_MAP updated rows: {updated_map:,}")

    # ---- Add columns to THIRD_PARTY_RECON_SKU_MAP_PROD and sync ----
    cur.execute(
        """
        SELECT COLUMN_NAME FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION' AND TABLE_NAME='THIRD_PARTY_RECON_SKU_MAP_PROD'
        """
    )
    prod_cols = {r[0] for r in cur.fetchall()}
    if "VENDOR_UNIT_PRICE" not in prod_cols:
        cur.execute("ALTER TABLE THIRD_PARTY_RECON_SKU_MAP_PROD ADD COLUMN VENDOR_UNIT_PRICE FLOAT")
    if "CW_UNIT_PRICE" not in prod_cols:
        cur.execute("ALTER TABLE THIRD_PARTY_RECON_SKU_MAP_PROD ADD COLUMN CW_UNIT_PRICE FLOAT")

    cur.execute(
        """
        UPDATE THIRD_PARTY_RECON_SKU_MAP_PROD m
        SET VENDOR_UNIT_PRICE = p.VENDOR_UNIT_PRICE,
            CW_UNIT_PRICE = p.CW_UNIT_PRICE
        FROM __SKU_DEFAULT_PRICES p
        WHERE m.CW_SKU = p.CW_SKU
        """
    )
    updated_prod = cur.rowcount
    print(f"    THIRD_PARTY_RECON_SKU_MAP_PROD updated rows: {updated_prod:,}")

    # ---- Summary ----
    summary = fetch_dataframe(
        """
        SELECT VENDOR,
               COUNT(*) AS map_rows,
               COUNT(VENDOR_UNIT_PRICE) AS rows_with_vendor_price,
               COUNT(CW_UNIT_PRICE) AS rows_with_cw_price,
               ROUND(COUNT(VENDOR_UNIT_PRICE) * 100.0 / COUNT(*), 1) AS pct_vendor_priced
        FROM RECON_SKU_MAP GROUP BY 1 ORDER BY 1
        """,
        conn=conn,
    )
    print("\n=== Per-vendor pricing coverage in RECON_SKU_MAP after enrichment ===")
    print(summary.to_string(index=False))

    tier_summary = fetch_dataframe(
        """
        SELECT VENDOR,
               COUNT(*) AS pricing_rows,
               COUNT(DISTINCT CW_SKU) AS distinct_cw_skus,
               COUNT(DISTINCT TIERNUM) AS distinct_tiers,
               COUNT(DISTINCT BILLING_TYPE) AS distinct_billing_types
        FROM RECON_SKU_PRICING WHERE VENDOR IS NOT NULL GROUP BY 1 ORDER BY 1
        """,
        conn=conn,
    )
    print("\n=== RECON_SKU_PRICING per-vendor coverage ===")
    print(tier_summary.to_string(index=False))

    conn.close()
    print("\nDONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
