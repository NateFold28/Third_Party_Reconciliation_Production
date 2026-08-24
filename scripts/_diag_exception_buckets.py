"""Diagnostic: top exception buckets per vendor + dollar impact.

Skeleton is complete (all 9 vendors LIVE). This script drives the fine-tuning
prioritization decision by showing where the exception dollars are concentrated
per vendor, not just where the row-count is highest.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


def main() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    c = conn.cursor()

    print("=== Total $ impact per vendor (non-Clear only) ===")
    c.execute(
        """
        SELECT VENDOR,
               ROUND(SUM(COALESCE(EST_DOLLAR_IMPACT, 0)), 0) AS impact,
               COUNT(*) AS n_exceptions,
               ROUND(100.0 * SUM(CASE WHEN IS_LEAKAGE THEN 1 ELSE 0 END) / COUNT(*), 1) AS leak_pct
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE EXCEPTION_TYPE <> 'Clear'
        GROUP BY 1
        ORDER BY impact DESC
        """
    )
    for vendor, impact, n_ex, leak_pct in c.fetchall():
        print(f"  {vendor:14s} ${impact:>14,.0f}  ({n_ex:>6,} exceptions, leakage={leak_pct}%)")

    print("\n=== Top 4 exception buckets per vendor ===")
    c.execute(
        """
        WITH ranked AS (
          SELECT VENDOR, EXCEPTION_TYPE,
                 COUNT(*) AS n_rows,
                 ROUND(SUM(COALESCE(EST_DOLLAR_IMPACT, 0)), 0) AS impact,
                 ROW_NUMBER() OVER (PARTITION BY VENDOR ORDER BY COUNT(*) DESC) AS rn
          FROM THIRD_PARTY_RECON_OUTPUT_PROD
          WHERE EXCEPTION_TYPE <> 'Clear'
          GROUP BY 1, 2
        )
        SELECT VENDOR, EXCEPTION_TYPE, n_rows, impact
        FROM ranked WHERE rn <= 4
        ORDER BY VENDOR, n_rows DESC
        """
    )
    current = None
    for vendor, exc_type, n_rows, impact in c.fetchall():
        if vendor != current:
            print(f"\n[{vendor}]")
            current = vendor
        print(f"  {exc_type:42s} rows={n_rows:>6,}  ${impact:>12,.0f}")

    print("\n=== Queue distribution per vendor ===")
    c.execute(
        """
        SELECT VENDOR,
               SUM(CASE WHEN IS_CLEAR THEN 1 ELSE 0 END) AS n_clear,
               SUM(CASE WHEN IS_LEAKAGE THEN 1 ELSE 0 END) AS n_leak,
               SUM(CASE WHEN IS_FINANCE_QUEUE THEN 1 ELSE 0 END) AS n_fin,
               SUM(CASE WHEN IS_OPS_QUEUE THEN 1 ELSE 0 END) AS n_ops,
               SUM(CASE WHEN IS_TIMING_QUEUE THEN 1 ELSE 0 END) AS n_timing
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
        GROUP BY 1
        ORDER BY 1
        """
    )
    print(f"  {'VENDOR':14s} {'CLEAR':>7s} {'LEAK':>7s} {'FINANCE':>8s} {'OPS':>7s} {'TIMING':>7s}")
    for vendor, n_clear, n_leak, n_fin, n_ops, n_timing in c.fetchall():
        print(f"  {vendor:14s} {n_clear:>7,} {n_leak:>7,} {n_fin:>8,} {n_ops:>7,} {n_timing:>7,}")


if __name__ == "__main__":
    main()
