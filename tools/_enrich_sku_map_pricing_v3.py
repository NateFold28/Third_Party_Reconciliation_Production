"""SKU map price propagation + targeted garbage cleanup — v3 (2026-08-30).

Strategy
--------
PRICE PROPAGATION (within sku_match_key group)
  For every unpriced row, find priced rows in the same (vendor, sku_match_key)
  group and apply the median price. If no priced row exists, fall back to
  contract_cost_rate with estimated retail at cost / 0.6 (40% margin target).

  Uses UPDATE ... FROM (staging table) to handle duplicate (vendor, cw_sku)
  rows in RECON_SKU_MAP without triggering Snowflake's MERGE duplicate error.

GARBAGE CLEANUP (SKU-code vendors only)
  Remove rows where cw_sku is not present as an individual token in
  THIRD_PARTY_RECON_DETAIL_PROD.cw_skus for that vendor.
  EXCLUDED from cleanup (cw_skus field is not SKU-code format):
    - Bitdefender  (cw_skus = product description strings)
    - Webroot      (cw_skus = NULL/empty)

DUPLICATES
  The map has known duplicate (vendor, cw_sku) rows with different sku_match_keys.
  The UPDATE approach intentionally sets the same price on all matching rows.
  We do NOT deduplicate the rows themselves — that is a separate data-quality task.

OUTPUT
  Per-vendor coverage report (before/after), CSV dumps.
  Changes written to RECON_SKU_MAP and THIRD_PARTY_RECON_SKU_MAP_PROD.
"""
from __future__ import annotations
import sys
import pandas as pd

sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

SCHEMA = "ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION"

# Vendors where cw_skus in DETAIL_PROD is NOT a CSV of SKU codes
# → skip garbage cleanup for these
SKIP_CLEANUP_VENDORS = {"Bitdefender", "Webroot"}


# -------------------------------------------------------------------
def run_sql(conn, sql: str, label: str = "") -> int:
    try:
        cur = conn.cursor()
        cur.execute(sql)
        cnt = cur.rowcount or 0
        if label:
            print(f"  {label} ... OK ({cnt} rows affected)")
        return cnt
    except Exception as exc:
        print(f"  {label} FAILED: {exc}")
        return 0


def fetch(conn, sql: str) -> pd.DataFrame:
    df = fetch_dataframe(sql, conn=conn)
    df.columns = [c.upper() for c in df.columns]
    return df


# -------------------------------------------------------------------
def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )

    # ------------------------------------------------------------------
    # 1. Load data
    # ------------------------------------------------------------------
    print("=== Loading RECON_SKU_MAP ===")
    sku_map = fetch(conn, f"""
        SELECT vendor, cw_sku, vendor_sku, sku_match_key, trt_match_key,
               contract_cost_rate, cw_retail_rate, vendor_unit_price, cw_unit_price,
               mapping_notes
        FROM {SCHEMA}.RECON_SKU_MAP
        ORDER BY vendor, sku_match_key, cw_sku
    """)
    print(f"  {len(sku_map)} rows, {sku_map['VENDOR'].nunique()} vendors")

    print("=== Loading DETAIL_PROD cw_skus (token expansion) ===")
    detail_rows = fetch(conn, f"""
        SELECT DISTINCT vendor, cw_skus
        FROM {SCHEMA}.THIRD_PARTY_RECON_DETAIL_PROD
        WHERE cw_skus IS NOT NULL AND TRIM(cw_skus) != ''
    """)
    # Build (vendor, individual_sku_token) set
    used_skus: set[tuple[str, str]] = set()
    for _, row in detail_rows.iterrows():
        vendor = str(row["VENDOR"]).strip()
        if vendor in SKIP_CLEANUP_VENDORS:
            continue
        for token in str(row["CW_SKUS"]).split(","):
            token = token.strip()
            if token:
                used_skus.add((vendor, token))
    print(f"  {len(used_skus)} individual (vendor, cw_sku) tokens found (SKU-code vendors only)")

    # ------------------------------------------------------------------
    # 2. Before-state report
    # ------------------------------------------------------------------
    print("\n=== BEFORE: pricing + usage coverage per vendor ===")
    before_rows: list[dict] = []
    for vendor, grp in sku_map.groupby("VENDOR"):
        total = len(grp)
        priced = int(grp["VENDOR_UNIT_PRICE"].notna().sum())
        in_detail = (
            sum(1 for _, r in grp.iterrows()
                if pd.notna(r["CW_SKU"]) and (vendor, str(r["CW_SKU"]).strip()) in used_skus)
            if vendor not in SKIP_CLEANUP_VENDORS
            else "n/a (product-name skus)"
        )
        before_rows.append({
            "vendor": vendor, "map_rows": total,
            "priced_before": priced, "in_detail": in_detail,
        })
    print(pd.DataFrame(before_rows).to_string(index=False))

    # ------------------------------------------------------------------
    # 3. Build price lookup: (vendor, sku_match_key) → (vendor_price, cw_price)
    # ------------------------------------------------------------------
    print("\n=== Building price lookup by sku_match_key group ===")
    priced_mask = sku_map["VENDOR_UNIT_PRICE"].notna()

    # PowerBI-sourced prices (highest priority)
    group_prices: dict[tuple, tuple] = {}
    for (vendor, smk), grp in sku_map[priced_mask].groupby(["VENDOR", "SKU_MATCH_KEY"]):
        v_prices = grp["VENDOR_UNIT_PRICE"].dropna()
        c_prices = grp["CW_UNIT_PRICE"].dropna()
        if len(v_prices):
            group_prices[(vendor, smk)] = (
                float(v_prices.median()),
                float(c_prices.median()) if len(c_prices) else float(v_prices.median()) / 0.60,
            )

    # Contract-cost-rate fallback for groups with no PowerBI price
    rate_mask = sku_map["VENDOR_UNIT_PRICE"].isna() & sku_map["CONTRACT_COST_RATE"].notna()
    for (vendor, smk), grp in sku_map[rate_mask].groupby(["VENDOR", "SKU_MATCH_KEY"]):
        if (vendor, smk) not in group_prices:
            rates = grp["CONTRACT_COST_RATE"].dropna()
            if len(rates):
                median_rate = float(rates.median())
                group_prices[(vendor, smk)] = (median_rate, round(median_rate / 0.60, 4))

    print(f"  {len(group_prices)} (vendor, sku_match_key) groups with a price source")

    # Build per-row update list
    unpriced = sku_map[sku_map["VENDOR_UNIT_PRICE"].isna() & sku_map["CW_SKU"].notna()].copy()
    updates_by_cw_sku: dict[tuple, tuple] = {}  # (vendor, cw_sku) → (vp, cp)
    no_price: list[str] = []
    for _, row in unpriced.iterrows():
        vendor = str(row["VENDOR"])
        smk = str(row["SKU_MATCH_KEY"]) if pd.notna(row["SKU_MATCH_KEY"]) else ""
        cw_sku = str(row["CW_SKU"]).strip()
        key = (vendor, smk)
        pair = (vendor, cw_sku)
        if pair in updates_by_cw_sku:
            continue  # already have a price for this cw_sku
        if key in group_prices:
            updates_by_cw_sku[pair] = group_prices[key]
        else:
            no_price.append(f"{vendor}/{cw_sku} (smk={smk})")

    print(f"  Rows to price (distinct vendor+cw_sku): {len(updates_by_cw_sku)}")
    print(f"  Rows with no price source:              {len(no_price)}")
    if no_price:
        print("  No-price sample:")
        for x in no_price[:20]:
            print(f"    {x}")

    # ------------------------------------------------------------------
    # 4. Identify garbage rows (SKU-code vendors only)
    # ------------------------------------------------------------------
    print("\n=== Identifying garbage rows ===")
    garbage_pairs: list[tuple[str, str]] = []
    for _, row in sku_map.iterrows():
        vendor = str(row["VENDOR"])
        cw_sku_raw = row["CW_SKU"]
        if pd.isna(cw_sku_raw) or str(cw_sku_raw).strip() in ("", "None"):
            continue
        cw_sku = str(cw_sku_raw).strip()
        if vendor in SKIP_CLEANUP_VENDORS:
            continue
        if (vendor, cw_sku) not in used_skus:
            garbage_pairs.append((vendor, cw_sku))

    # Deduplicate
    garbage_pairs = list(dict.fromkeys(garbage_pairs))
    print(f"  {len(garbage_pairs)} unique (vendor, cw_sku) garbage rows to delete")
    gdf = pd.DataFrame(garbage_pairs, columns=["vendor", "cw_sku"])
    if not gdf.empty:
        print(gdf.groupby("vendor").size().to_string())

    # ------------------------------------------------------------------
    # 5. Apply price propagation (UPDATE, not MERGE)
    # ------------------------------------------------------------------
    print("\n=== Applying changes ===")
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    run_sql(conn,
        f"CREATE TABLE {SCHEMA}.RECON_SKU_MAP_BAK_{ts} AS SELECT * FROM {SCHEMA}.RECON_SKU_MAP",
        f"backup RECON_SKU_MAP → RECON_SKU_MAP_BAK_{ts}")

    # Stage price updates into a temporary table
    if updates_by_cw_sku:
        value_rows = []
        for (vendor, cw_sku), (vp, cp) in updates_by_cw_sku.items():
            ve = vendor.replace("'", "''")
            se = cw_sku.replace("'", "''")
            value_rows.append(f"('{ve}', '{se}', {round(vp, 6)}, {round(cp, 6)})")

        # Create temp staging table
        run_sql(conn, "CREATE OR REPLACE TEMPORARY TABLE _sku_price_stage (vendor TEXT, cw_sku TEXT, vendor_unit_price FLOAT, cw_unit_price FLOAT)", "create temp stage")

        # Insert in batches of 1000
        batch_size = 1000
        staged = 0
        for i in range(0, len(value_rows), batch_size):
            batch = value_rows[i:i+batch_size]
            ins = f"INSERT INTO _sku_price_stage VALUES {','.join(batch)}"
            staged += run_sql(conn, ins)
        print(f"  Staged {staged} (may be 0 for multi-row inserts — OK)")

        # Bulk UPDATE using join
        n1 = run_sql(conn, f"""
            UPDATE {SCHEMA}.RECON_SKU_MAP t
            SET VENDOR_UNIT_PRICE = s.vendor_unit_price,
                CW_UNIT_PRICE = s.cw_unit_price
            FROM _sku_price_stage s
            WHERE t.VENDOR = s.vendor
              AND t.CW_SKU = s.cw_sku
              AND t.VENDOR_UNIT_PRICE IS NULL
        """, "UPDATE RECON_SKU_MAP prices")

        n2 = run_sql(conn, f"""
            UPDATE {SCHEMA}.THIRD_PARTY_RECON_SKU_MAP_PROD t
            SET VENDOR_UNIT_PRICE = s.vendor_unit_price,
                CW_UNIT_PRICE = s.cw_unit_price
            FROM _sku_price_stage s
            WHERE t.VENDOR = s.vendor
              AND t.CW_SKU = s.cw_sku
              AND t.VENDOR_UNIT_PRICE IS NULL
        """, "UPDATE THIRD_PARTY_RECON_SKU_MAP_PROD prices")
    else:
        print("  No price updates needed.")

    # ------------------------------------------------------------------
    # 6. Delete garbage rows
    # ------------------------------------------------------------------
    if garbage_pairs:
        batch_size = 200
        deleted_main = 0
        deleted_mirror = 0
        for i in range(0, len(garbage_pairs), batch_size):
            batch = garbage_pairs[i:i+batch_size]
            conds = " OR ".join(f"(VENDOR = '{v.replace(chr(39),chr(39)*2)}' AND CW_SKU = '{s.replace(chr(39),chr(39)*2)}')" for v, s in batch)
            deleted_main += run_sql(conn, f"DELETE FROM {SCHEMA}.RECON_SKU_MAP WHERE {conds}")
            deleted_mirror += run_sql(conn, f"DELETE FROM {SCHEMA}.THIRD_PARTY_RECON_SKU_MAP_PROD WHERE {conds}")
        print(f"  Deleted {deleted_main} garbage rows from RECON_SKU_MAP")
        print(f"  Deleted {deleted_mirror} garbage rows from THIRD_PARTY_RECON_SKU_MAP_PROD")

    conn.commit()

    # ------------------------------------------------------------------
    # 7. After-state report
    # ------------------------------------------------------------------
    print("\n=== AFTER: pricing + usage coverage per vendor ===")
    sku_map_after = fetch(conn, f"""
        SELECT vendor, cw_sku, sku_match_key, contract_cost_rate,
               vendor_unit_price, cw_unit_price
        FROM {SCHEMA}.RECON_SKU_MAP
        ORDER BY vendor, sku_match_key, cw_sku
    """)

    report: list[dict] = []
    for vendor, grp in sku_map_after.groupby("VENDOR"):
        total = len(grp)
        priced_v = int(grp["VENDOR_UNIT_PRICE"].notna().sum())
        priced_c = int(grp["CW_UNIT_PRICE"].notna().sum())
        in_detail = (
            sum(1 for _, r in grp.iterrows()
                if pd.notna(r["CW_SKU"]) and (vendor, str(r["CW_SKU"]).strip()) in used_skus)
            if vendor not in SKIP_CLEANUP_VENDORS else "skipped"
        )
        before_row = next((r for r in before_rows if r["vendor"] == vendor), {})
        report.append({
            "Vendor": vendor,
            "Rows before": before_row.get("map_rows", total),
            "Rows after": total,
            "Removed": before_row.get("map_rows", total) - total,
            "Priced before": before_row.get("priced_before", 0),
            "Priced after": priced_v,
            "New prices": priced_v - before_row.get("priced_before", 0),
            "% priced": f"{priced_v*100//total if total else 0}%",
            "Still unpriced": total - priced_v,
            "In DETAIL_PROD": in_detail,
        })

    print(pd.DataFrame(report).to_string(index=False))

    # Per-vendor sample of unpriced rows (for future manual pricing)
    print("\n=== Unpriced rows remaining (no price in PowerBI + no contract rate) ===")
    unpriced_after = sku_map_after[sku_map_after["VENDOR_UNIT_PRICE"].isna()]
    if unpriced_after.empty:
        print("  None — all rows priced!")
    else:
        for vendor, grp in unpriced_after.groupby("VENDOR"):
            print(f"\n  {vendor} ({len(grp)} unpriced):")
            for _, r in grp.head(8).iterrows():
                print(f"    cw_sku={r['CW_SKU']}  smk={r['SKU_MATCH_KEY']}  contract_rate={r['CONTRACT_COST_RATE']}")

    # Dump
    sku_map_after.to_csv(r"c:/Users/Nate.Fold/projects/logs/sku_map_after_v3.csv", index=False)
    gdf.to_csv(r"c:/Users/Nate.Fold/projects/logs/sku_map_garbage_v3.csv", index=False)
    print(f"\n  Full map → logs/sku_map_after_v3.csv")
    print(f"  Garbage removed → logs/sku_map_garbage_v3.csv")

    conn.close()
    print("\nDONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
