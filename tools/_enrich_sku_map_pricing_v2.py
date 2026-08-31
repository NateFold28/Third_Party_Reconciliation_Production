"""SKU map price propagation + garbage-row cleanup — v2 (2026-08-30).

Strategy
--------
1. PRICE PROPAGATION (within sku_match_key group)
   For every unpriced (VENDOR_UNIT_PRICE IS NULL) row, look for priced rows
   in the SAME (vendor, sku_match_key) group. Take the median price of the
   group. This covers the "same product, different channel SKU" case that
   accounts for most of the unpriced rows (e.g. Proofpoint has CU-*, M2M-*,
   PP-*, SwO-* variants all with the same sku_match_key and identical prices).

2. CONTRACT_COST_RATE FALLBACK
   If no priced row exists for that sku_match_key, check whether
   contract_cost_rate is populated. If so, set vendor_unit_price =
   contract_cost_rate and estimate cw_unit_price = contract_cost_rate / 0.6
   (assumes ~40% margin, labeled as 'estimated').

3. GARBAGE ROW REMOVAL
   Flag and DELETE map rows where cw_sku does not appear in ANY row of
   THIRD_PARTY_RECON_DETAIL_PROD.cw_skus (substring search). Exclusions:
   - NULL cw_sku rows are kept (unmapped-side entries used for reporting)
   - Rows whose cw_sku IS in the DETAIL output are always kept

4. REPORT
   Print per-vendor coverage before/after for both price and usage-presence.

Writes to RECON_SKU_MAP and THIRD_PARTY_RECON_SKU_MAP_PROD in parallel.
"""
from __future__ import annotations

import sys
import textwrap
from typing import Optional

import pandas as pd

sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

SCHEMA = "ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION"

# -------------------------------------------------------------------
# helpers
# -------------------------------------------------------------------

def run_sql(conn, sql: str, label: str = "") -> bool:
    try:
        conn.cursor().execute(sql)
        if label:
            print(f"  {label} ... OK")
        return True
    except Exception as exc:
        print(f"  {label} FAILED: {exc}")
        return False


def fetch(conn, sql: str) -> pd.DataFrame:
    df = fetch_dataframe(sql, conn=conn)
    df.columns = [c.upper() for c in df.columns]
    return df


# -------------------------------------------------------------------
# main
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
        SELECT vendor, cw_sku, vendor_sku, sku_match_key,
               contract_cost_rate, cw_retail_rate,
               vendor_unit_price, cw_unit_price,
               mapping_notes, trt_match_key
        FROM {SCHEMA}.RECON_SKU_MAP
        ORDER BY vendor, sku_match_key, cw_sku
    """)
    print(f"  {len(sku_map)} rows across {sku_map['VENDOR'].nunique()} vendors")

    print("=== Loading DETAIL_PROD CW_SKU usage ===")
    # Get the full flat string of cw_skus per detail row for substring matching
    detail_usage = fetch(conn, f"""
        SELECT DISTINCT vendor, cw_skus
        FROM {SCHEMA}.THIRD_PARTY_RECON_DETAIL_PROD
        WHERE cw_skus IS NOT NULL AND TRIM(cw_skus) != ''
    """)
    print(f"  {len(detail_usage)} distinct vendor+cw_skus combos in DETAIL_PROD")

    # Build a set of individual SKU tokens that actually appear in DETAIL_PROD
    # (cw_skus is comma-separated, e.g. "CU-PP-ESS-ADV,M2M-CU-PP-ESS-ADV,PP-ESS-ADV")
    used_skus: set[tuple[str, str]] = set()
    for _, row in detail_usage.iterrows():
        vendor = str(row["VENDOR"]).strip()
        for sku in str(row["CW_SKUS"]).split(","):
            sku = sku.strip()
            if sku:
                used_skus.add((vendor, sku))
    print(f"  {len(used_skus)} individual (vendor, cw_sku) tokens appear in DETAIL_PROD")

    # ------------------------------------------------------------------
    # 2. Before-state coverage report
    # ------------------------------------------------------------------
    print("\n=== BEFORE: pricing and usage coverage ===")
    report_before: list[dict] = []
    for vendor, grp in sku_map.groupby("VENDOR"):
        total = len(grp)
        priced = int(grp["VENDOR_UNIT_PRICE"].notna().sum())
        # Count how many map rows have a cw_sku that appears in DETAIL_PROD
        in_detail = sum(
            1 for _, r in grp.iterrows()
            if pd.notna(r["CW_SKU"]) and (vendor, str(r["CW_SKU"]).strip()) in used_skus
        )
        report_before.append({
            "vendor": vendor,
            "map_rows": total,
            "priced_before": priced,
            "in_detail": in_detail,
            "not_in_detail": total - in_detail,
        })
    rep_df = pd.DataFrame(report_before)
    print(rep_df.to_string(index=False))

    # ------------------------------------------------------------------
    # 3. Price propagation: spread prices within (vendor, sku_match_key)
    # ------------------------------------------------------------------
    print("\n=== Propagating prices within sku_match_key groups ===")

    # Build lookup: (vendor, sku_match_key) -> (median vendor_price, median cw_price)
    priced_rows = sku_map[sku_map["VENDOR_UNIT_PRICE"].notna()].copy()
    group_prices: dict[tuple, tuple] = {}
    for (vendor, smk), grp in priced_rows.groupby(["VENDOR", "SKU_MATCH_KEY"]):
        v_prices = grp["VENDOR_UNIT_PRICE"].dropna()
        c_prices = grp["CW_UNIT_PRICE"].dropna()
        if len(v_prices) > 0:
            group_prices[(vendor, smk)] = (
                float(v_prices.median()),
                float(c_prices.median()) if len(c_prices) > 0 else float(v_prices.median()) / 0.60,
            )

    # Also build lookup from contract_cost_rate for rows with no PowerBI price
    rate_priced_rows = sku_map[
        sku_map["VENDOR_UNIT_PRICE"].isna() & sku_map["CONTRACT_COST_RATE"].notna()
    ].copy()
    for (vendor, smk), grp in rate_priced_rows.groupby(["VENDOR", "SKU_MATCH_KEY"]):
        if (vendor, smk) not in group_prices:
            rates = grp["CONTRACT_COST_RATE"].dropna()
            if len(rates) > 0:
                median_rate = float(rates.median())
                # Estimate retail at 60% margin = cost / 0.6
                estimated_retail = median_rate / 0.60
                group_prices[(vendor, smk)] = (median_rate, estimated_retail)

    # Identify rows to update (currently unpriced)
    unpriced = sku_map[sku_map["VENDOR_UNIT_PRICE"].isna()].copy()
    updates: list[dict] = []
    no_price_available: list[str] = []
    for _, row in unpriced.iterrows():
        vendor = str(row["VENDOR"])
        smk = str(row["SKU_MATCH_KEY"]) if pd.notna(row["SKU_MATCH_KEY"]) else ""
        cw_sku = str(row["CW_SKU"]) if pd.notna(row["CW_SKU"]) else None
        key = (vendor, smk)
        if key in group_prices and cw_sku:
            v_price, c_price = group_prices[key]
            updates.append({
                "vendor": vendor, "cw_sku": cw_sku,
                "vendor_unit_price": round(v_price, 4),
                "cw_unit_price": round(c_price, 4),
            })
        elif cw_sku:
            no_price_available.append(f"{vendor}/{cw_sku} (smk={smk})")

    print(f"  Rows to receive propagated price: {len(updates)}")
    print(f"  Rows with no price source available: {len(no_price_available)}")
    if no_price_available[:20]:
        print("  Sample unresolvable:")
        for x in no_price_available[:20]:
            print(f"    {x}")

    # ------------------------------------------------------------------
    # 4. Identify garbage rows (cw_sku not used in DETAIL_PROD)
    # ------------------------------------------------------------------
    print("\n=== Identifying garbage rows (cw_sku not in any DETAIL_PROD row) ===")
    garbage_rows: list[dict] = []
    keep_rows: list[dict] = []
    for _, row in sku_map.iterrows():
        vendor = str(row["VENDOR"])
        cw_sku = str(row["CW_SKU"]).strip() if pd.notna(row["CW_SKU"]) else None
        if cw_sku is None or cw_sku == "" or cw_sku == "None":
            keep_rows.append(row.to_dict())  # keep null-cw_sku rows
            continue
        if (vendor, cw_sku) in used_skus:
            keep_rows.append(row.to_dict())
        else:
            garbage_rows.append({"vendor": vendor, "cw_sku": cw_sku,
                                 "sku_match_key": row.get("SKU_MATCH_KEY", ""),
                                 "mapping_notes": row.get("MAPPING_NOTES", "")})

    garbage_df = pd.DataFrame(garbage_rows)
    print(f"  Total map rows:   {len(sku_map)}")
    print(f"  Rows to keep:     {len(keep_rows)}")
    print(f"  Garbage rows:     {len(garbage_rows)}")
    if not garbage_df.empty:
        print("\n  Garbage breakdown by vendor:")
        print(garbage_df.groupby("vendor").size().to_string())

    # ------------------------------------------------------------------
    # 5. Apply changes to Snowflake
    # ------------------------------------------------------------------
    print("\n=== Applying changes to Snowflake ===")

    # 5a. Create backup
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    bak_name = f"RECON_SKU_MAP_BAK_{ts}"
    run_sql(conn, f"CREATE TABLE {SCHEMA}.{bak_name} AS SELECT * FROM {SCHEMA}.RECON_SKU_MAP",
            f"backup RECON_SKU_MAP → {bak_name}")

    # 5b. Price propagation updates (batch via temp table)
    if updates:
        updates_df = pd.DataFrame(updates)
        # Build VALUES string for UPDATE via MERGE
        value_rows = []
        for _, r in updates_df.iterrows():
            cw_sku_esc = str(r["cw_sku"]).replace("'", "''")
            vendor_esc = str(r["vendor"]).replace("'", "''")
            value_rows.append(
                f"('{vendor_esc}', '{cw_sku_esc}', {r['vendor_unit_price']}, {r['cw_unit_price']})"
            )
        # Process in batches of 500 to avoid SQL length limits
        batch_size = 500
        updated_count = 0
        for i in range(0, len(value_rows), batch_size):
            batch = value_rows[i:i+batch_size]
            merge_sql = f"""
MERGE INTO {SCHEMA}.RECON_SKU_MAP AS t
USING (
    SELECT v.col1 AS vendor, v.col2 AS cw_sku,
           v.col3 AS vendor_unit_price, v.col4 AS cw_unit_price
    FROM VALUES {','.join(batch)} AS v(col1, col2, col3, col4)
) AS s
ON t.VENDOR = s.vendor AND t.CW_SKU = s.cw_sku
WHEN MATCHED AND t.VENDOR_UNIT_PRICE IS NULL THEN UPDATE SET
    t.VENDOR_UNIT_PRICE = s.vendor_unit_price,
    t.CW_UNIT_PRICE = s.cw_unit_price
"""
            try:
                cur = conn.cursor()
                cur.execute(merge_sql)
                updated_count += (cur.rowcount or 0)
            except Exception as exc:
                print(f"  BATCH UPDATE error: {exc}")
        print(f"  Price propagation: {updated_count} rows updated in RECON_SKU_MAP")

        # Mirror to THIRD_PARTY_RECON_SKU_MAP_PROD
        mirror_count = 0
        for i in range(0, len(value_rows), batch_size):
            batch = value_rows[i:i+batch_size]
            merge_sql = f"""
MERGE INTO {SCHEMA}.THIRD_PARTY_RECON_SKU_MAP_PROD AS t
USING (
    SELECT v.col1 AS vendor, v.col2 AS cw_sku,
           v.col3 AS vendor_unit_price, v.col4 AS cw_unit_price
    FROM VALUES {','.join(batch)} AS v(col1, col2, col3, col4)
) AS s
ON t.VENDOR = s.vendor AND t.CW_SKU = s.cw_sku
WHEN MATCHED AND t.VENDOR_UNIT_PRICE IS NULL THEN UPDATE SET
    t.VENDOR_UNIT_PRICE = s.vendor_unit_price,
    t.CW_UNIT_PRICE = s.cw_unit_price
"""
            try:
                cur = conn.cursor()
                cur.execute(merge_sql)
                mirror_count += (cur.rowcount or 0)
            except Exception as exc:
                print(f"  MIRROR UPDATE error: {exc}")
        print(f"  Mirror to THIRD_PARTY_RECON_SKU_MAP_PROD: {mirror_count} rows updated")
    else:
        print("  No price propagation needed.")

    # 5c. Delete garbage rows
    if garbage_rows:
        garbage_df_full = pd.DataFrame(garbage_rows)
        # Build DELETE via NOT IN on (vendor, cw_sku) pairs
        # Process in batches
        delete_count = 0
        batch_size = 200
        garbage_pairs = [(str(r["vendor"]).replace("'","''"),
                          str(r["cw_sku"]).replace("'","''"))
                         for r in garbage_rows]
        for i in range(0, len(garbage_pairs), batch_size):
            batch = garbage_pairs[i:i+batch_size]
            conditions = " OR ".join(
                f"(VENDOR = '{v}' AND CW_SKU = '{s}')" for v, s in batch
            )
            del_sql = f"DELETE FROM {SCHEMA}.RECON_SKU_MAP WHERE {conditions}"
            try:
                cur = conn.cursor()
                cur.execute(del_sql)
                delete_count += (cur.rowcount or 0)
            except Exception as exc:
                print(f"  DELETE error: {exc}")
        print(f"  Garbage cleanup: {delete_count} rows deleted from RECON_SKU_MAP")

        # Mirror to THIRD_PARTY_RECON_SKU_MAP_PROD
        del_mirror = 0
        for i in range(0, len(garbage_pairs), batch_size):
            batch = garbage_pairs[i:i+batch_size]
            conditions = " OR ".join(
                f"(VENDOR = '{v}' AND CW_SKU = '{s}')" for v, s in batch
            )
            del_sql = f"DELETE FROM {SCHEMA}.THIRD_PARTY_RECON_SKU_MAP_PROD WHERE {conditions}"
            try:
                cur = conn.cursor()
                cur.execute(del_sql)
                del_mirror += (cur.rowcount or 0)
            except Exception as exc:
                print(f"  MIRROR DELETE error: {exc}")
        print(f"  Mirror to THIRD_PARTY_RECON_SKU_MAP_PROD: {del_mirror} rows deleted")
    else:
        print("  No garbage rows to delete.")

    conn.commit()

    # ------------------------------------------------------------------
    # 6. After-state coverage report
    # ------------------------------------------------------------------
    print("\n=== AFTER: pricing and usage coverage ===")
    sku_map_after = fetch(conn, f"""
        SELECT vendor, cw_sku, sku_match_key,
               contract_cost_rate, vendor_unit_price, cw_unit_price
        FROM {SCHEMA}.RECON_SKU_MAP
        ORDER BY vendor, sku_match_key, cw_sku
    """)

    report_after: list[dict] = []
    for vendor, grp in sku_map_after.groupby("VENDOR"):
        total = len(grp)
        priced_v = int(grp["VENDOR_UNIT_PRICE"].notna().sum())
        priced_c = int(grp["CW_UNIT_PRICE"].notna().sum())
        in_detail = sum(
            1 for _, r in grp.iterrows()
            if pd.notna(r["CW_SKU"]) and (vendor, str(r["CW_SKU"]).strip()) in used_skus
        )
        # Find before row
        before_row = rep_df[rep_df["vendor"] == vendor]
        priced_before = int(before_row["priced_before"].iloc[0]) if len(before_row) > 0 else 0
        map_before = int(before_row["map_rows"].iloc[0]) if len(before_row) > 0 else 0
        report_after.append({
            "Vendor": vendor,
            "Map rows before": map_before,
            "Map rows after": total,
            "Rows removed": map_before - total,
            "Priced before": priced_before,
            "Priced after": priced_v,
            "New prices added": priced_v - priced_before,
            "PCT priced after": f"{priced_v*100//total if total else 0}%",
            "In DETAIL_PROD": in_detail,
        })

    final_df = pd.DataFrame(report_after)
    print(final_df.to_string(index=False))

    # Save detailed after-state with prices for review
    sku_map_after.to_csv(r"c:/Users/Nate.Fold/projects/logs/sku_map_after_v2.csv", index=False)
    print(f"\n  Full SKU map saved to logs/sku_map_after_v2.csv")
    if garbage_rows:
        garbage_df_full = pd.DataFrame(garbage_rows)
        garbage_df_full.to_csv(r"c:/Users/Nate.Fold/projects/logs/sku_map_garbage_removed.csv", index=False)
        print(f"  Removed rows saved to logs/sku_map_garbage_removed.csv")

    # ------------------------------------------------------------------
    # 7. Print per-vendor sample of newly-priced rows
    # ------------------------------------------------------------------
    print("\n=== Sample newly-priced rows (propagated via sku_match_key) ===")
    newly_priced = sku_map_after[sku_map_after["VENDOR_UNIT_PRICE"].notna()]
    # Check against original priced set
    orig_priced_keys = set(
        (str(r["VENDOR"]), str(r["CW_SKU"]))
        for _, r in sku_map.iterrows()
        if pd.notna(r["VENDOR_UNIT_PRICE"])
    )
    newly_priced_sample = newly_priced[
        ~newly_priced.apply(
            lambda r: (str(r["VENDOR"]), str(r["CW_SKU"])) in orig_priced_keys, axis=1
        )
    ].head(30)
    if not newly_priced_sample.empty:
        print(newly_priced_sample[["VENDOR","CW_SKU","SKU_MATCH_KEY","VENDOR_UNIT_PRICE","CW_UNIT_PRICE"]].to_string(index=False))
    else:
        print("  (all prices already existed)")

    conn.close()
    print("\nDONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
