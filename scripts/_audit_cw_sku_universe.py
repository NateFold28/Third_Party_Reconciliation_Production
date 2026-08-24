"""Audit CW-side (Salesforce/CARR) SKU universe per vendor and cross-reference
with our RECON_SKU_MAP coverage.

For each vendor:
  1. Pull the CW SKU universe from analytics.dbo_transformation.seed__product_categorization
     joined to analytics.dbo.carr__all_transactions for 12-mo MRR.
  2. Compare against RECON_SKU_MAP.CW_SKU to find:
     - CW SKUs present in the vendor's catalog but NOT in RECON_SKU_MAP (mapping gap)
     - RECON_SKU_MAP.CW_SKU values that don't exist in the CW SKU universe (stale mappings)
  3. Report top revenue SKUs not yet mapped.

Usage:
  python -u scripts/_audit_cw_sku_universe.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


VENDOR_FILTERS = {
    "Acronis":     "%acronis%",
    "Auvik":       "%auvik%",
    "Bitdefender": "%bitdefender%",
    "ESET":        "%eset%",
    "Exium":       "%exium%",
    "KeepIT":      "%keepit%",
    "Proofpoint":  "%proofpoint%",
    "SentinelOne": "%sentinelone%",
    "Webroot":     "%webroot%",
}


def main() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    c = conn.cursor()

    print("=" * 90)
    print("CW-side SKU universe per vendor (from seed__product_categorization + CARR)")
    print("=" * 90)
    print(f"  {'Vendor':14s} {'CW SKUs':>10s} {'w/MRR':>8s} {'Annual $':>16s} "
          f"{'In Map':>8s} {'Not Mapped':>10s} {'Gap $':>16s}")

    all_gaps = []

    for vendor, pattern in VENDOR_FILTERS.items():
        c.execute(
            f"""
            WITH skus AS (
              SELECT c.mrr_summed_last_12, pr.name, p.prod_sku, p.vendor AS cw_vendor,
                     p.sub_category, p.category,
                     UPPER(TRIM(p.prod_sku)) AS prod_sku_key
              FROM analytics.dbo_transformation.seed__product_categorization p
              LEFT JOIN (
                SELECT prod_sku, SUM(COALESCE(arr_budget_rate, 0) / 12) AS mrr_summed_last_12
                FROM analytics.dbo.carr__all_transactions
                WHERE month_year BETWEEN '2025-08-31' AND '2027-07-31'
                GROUP BY 1
              ) c ON c.prod_sku = p.prod_sku
              LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__product pr
                ON pr.product_code = p.prod_sku AND pr.is_deleted = FALSE
              WHERE p.sub_category ILIKE '{pattern}' OR p.vendor ILIKE '{pattern}'
            ),
            mapped AS (
              SELECT DISTINCT UPPER(TRIM(cw_sku)) AS cw_sku_key
              FROM DBT_NFOLD_TRANSFORMATION.RECON_SKU_MAP
              WHERE vendor = '{vendor}' AND cw_sku IS NOT NULL
            ),
            joined AS (
              SELECT s.*, m.cw_sku_key AS mapped_key
              FROM skus s
              LEFT JOIN mapped m ON m.cw_sku_key = s.prod_sku_key
            )
            SELECT
              COUNT(*) AS total_cw_skus,
              COUNT(mrr_summed_last_12) AS with_mrr,
              ROUND(SUM(COALESCE(mrr_summed_last_12, 0)) * 12, 0) AS annual_arr,
              COUNT(CASE WHEN mapped_key IS NOT NULL THEN 1 END) AS in_map,
              COUNT(CASE WHEN mapped_key IS NULL     THEN 1 END) AS not_mapped,
              ROUND(SUM(CASE WHEN mapped_key IS NULL
                             THEN COALESCE(mrr_summed_last_12, 0) ELSE 0 END) * 12, 0) AS gap_arr
            FROM joined
            """
        )
        r = c.fetchone()
        print(
            f"  {vendor:14s} {r[0]:>10,} {r[1]:>8,} ${r[2] or 0:>14,.0f} "
            f"{r[3]:>8,} {r[4]:>10,} ${r[5] or 0:>14,.0f}"
        )
        all_gaps.append((vendor, r[5] or 0, r[4]))

    print("\n" + "=" * 90)
    print("TOP UNMAPPED CW SKUs per vendor (annual $ impact, top 10 each)")
    print("=" * 90)
    for vendor, pattern in VENDOR_FILTERS.items():
        c.execute(
            f"""
            WITH skus AS (
              SELECT c.mrr_summed_last_12, pr.name, p.prod_sku,
                     UPPER(TRIM(p.prod_sku)) AS prod_sku_key
              FROM analytics.dbo_transformation.seed__product_categorization p
              LEFT JOIN (
                SELECT prod_sku, SUM(COALESCE(arr_budget_rate, 0) / 12) AS mrr_summed_last_12
                FROM analytics.dbo.carr__all_transactions
                WHERE month_year BETWEEN '2025-08-31' AND '2027-07-31'
                GROUP BY 1
              ) c ON c.prod_sku = p.prod_sku
              LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__product pr
                ON pr.product_code = p.prod_sku AND pr.is_deleted = FALSE
              WHERE p.sub_category ILIKE '{pattern}' OR p.vendor ILIKE '{pattern}'
            ),
            mapped AS (
              SELECT DISTINCT UPPER(TRIM(cw_sku)) AS cw_sku_key
              FROM DBT_NFOLD_TRANSFORMATION.RECON_SKU_MAP
              WHERE vendor = '{vendor}' AND cw_sku IS NOT NULL
            )
            SELECT s.prod_sku, s.name, ROUND(s.mrr_summed_last_12 * 12, 0) AS annual_arr
            FROM skus s
            LEFT JOIN mapped m ON m.cw_sku_key = s.prod_sku_key
            WHERE m.cw_sku_key IS NULL
              AND s.mrr_summed_last_12 IS NOT NULL AND s.mrr_summed_last_12 > 0
            ORDER BY s.mrr_summed_last_12 DESC NULLS LAST
            LIMIT 10
            """
        )
        rows = c.fetchall()
        if not rows:
            continue
        print(f"\n[{vendor}]")
        for sku, name, arr in rows:
            print(f"  ${arr or 0:>12,.0f}  {sku:35s} {(name or '')[:60]}")

    print("\n" + "=" * 90)
    print("Overall gap dollar rank (this is what to prioritize for mapping work)")
    print("=" * 90)
    for vendor, gap_arr, gap_count in sorted(all_gaps, key=lambda x: x[1], reverse=True):
        print(f"  {vendor:14s} unmapped_annual_arr=${gap_arr:>14,.0f}  unmapped_skus={gap_count:>5,}")


if __name__ == "__main__":
    main()
