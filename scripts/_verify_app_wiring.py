"""Quick app-wiring verification: confirm OUTPUT_PROD + SUMMARY are current
and in sync, and print the exact per-vendor / per-EXCEPTION_TYPE numbers the
app will render."""
from __future__ import annotations

import sys

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

    print("=" * 90)
    print("APP WIRING CHECK — 2026-08-23")
    print("=" * 90)

    # 1) OUTPUT_PROD current shape
    c.execute("""
        SELECT COUNT(*) AS n_rows,
               COUNT(DISTINCT VENDOR) AS n_vendors,
               COUNT(DISTINCT EXCEPTION_TYPE) AS n_buckets,
               MIN(BILLING_MONTH) AS min_month,
               MAX(BILLING_MONTH) AS max_month
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
    """)
    r = c.fetchone()
    print(f"\nTHIRD_PARTY_RECON_OUTPUT_PROD:")
    print(f"  rows={r[0]:,}  vendors={r[1]}  buckets={r[2]}  months={r[3]}..{r[4]}")

    # 2) SUMMARY current shape
    c.execute("""
        SELECT COUNT(*) AS n_rows,
               COUNT(DISTINCT VENDOR) AS n_vendors,
               MIN(BILLING_MONTH) AS min_month,
               MAX(BILLING_MONTH) AS max_month
        FROM THIRD_PARTY_RECON_SUMMARY_PROD
    """)
    r = c.fetchone()
    print(f"\nTHIRD_PARTY_RECON_SUMMARY_PROD:")
    print(f"  rows={r[0]:,}  vendors={r[1]}  months={r[2]}..{r[3]}")

    # 3) Sanity: OUTPUT vs SUMMARY row-count agreement per vendor
    c.execute("""
        WITH o AS (
          SELECT VENDOR, COUNT(*) AS output_rows
          FROM THIRD_PARTY_RECON_OUTPUT_PROD GROUP BY 1
        ),
        s AS (
          SELECT VENDOR, SUM(TOTAL_ROWS) AS summary_rows
          FROM THIRD_PARTY_RECON_SUMMARY_PROD GROUP BY 1
        )
        SELECT o.VENDOR, o.output_rows, s.summary_rows,
               (o.output_rows - COALESCE(s.summary_rows, 0)) AS delta
        FROM o LEFT JOIN s USING (VENDOR)
        ORDER BY o.VENDOR
    """)
    print(f"\nPer-vendor OUTPUT vs SUMMARY row parity:")
    print(f"  {'VENDOR':14s} {'OUTPUT':>10s} {'SUMMARY':>10s} {'delta':>8s}")
    for v, o, s, d in c.fetchall():
        marker = "  " if d == 0 else " !"
        print(f"  {v:14s} {o:>10,} {(s or 0):>10,} {d:>8,}{marker}")

    # 4) The exact clear-rate + $ table the app will show
    c.execute("""
        SELECT VENDOR,
               COUNT(*) AS rows_tot,
               ROUND(100.0 * SUM(CASE WHEN EXCEPTION_TYPE = 'Clear' THEN 1 ELSE 0 END)
                     / NULLIF(COUNT(*), 0), 1) AS clear_pct,
               ROUND(SUM(CASE WHEN EXCEPTION_TYPE <> 'Clear'
                              THEN COALESCE(VENDOR_AMOUNT, 0) ELSE 0 END), 0) AS exception_dollars
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
        GROUP BY 1
        ORDER BY clear_pct DESC
    """)
    print(f"\nPer-vendor clear rate + exception $ (as app will render):")
    print(f"  {'VENDOR':14s} {'ROWS':>10s} {'CLEAR%':>8s} {'EXCEPTION_$':>16s}")
    for v, n, pct, dol in c.fetchall():
        print(f"  {v:14s} {n:>10,} {pct or 0:>7.1f}% ${dol or 0:>15,.0f}")

    # 5) EXCEPTION_TYPE bucket universe
    c.execute("""
        SELECT EXCEPTION_TYPE, COUNT(*) AS rows_tot,
               ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 0) AS dollars
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
        GROUP BY 1
        ORDER BY rows_tot DESC
    """)
    print(f"\nEXCEPTION_TYPE distribution:")
    print(f"  {'BUCKET':44s} {'ROWS':>10s} {'DOLLARS':>16s}")
    for b, n, d in c.fetchall():
        print(f"  {b:44s} {n:>10,} ${d or 0:>15,.0f}")

    # 6) SUMMARY has data_load_status column?
    c.execute("""
        SELECT DATA_LOAD_STATUS, COUNT(*) AS n
        FROM THIRD_PARTY_RECON_SUMMARY_PROD
        GROUP BY 1
        ORDER BY n DESC
    """)
    print(f"\nDATA_LOAD_STATUS distribution in SUMMARY:")
    for st, n in c.fetchall():
        print(f"  {st}: {n:,}")

    conn.close()


if __name__ == "__main__":
    main()
