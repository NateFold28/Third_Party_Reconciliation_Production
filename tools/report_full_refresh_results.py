"""Report post-refresh health metrics for the third-party recon pipeline."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import snowflake.connector

import sys

PROJECT_ROOT = Path(r"C:\Users\Nate.Fold\projects")
REPO = PROJECT_ROOT / "PROJECTS" / "Third_Party_Reconciliation" / "Combined_Recon_Prod_Pipeline"
sys.path.insert(0, str(PROJECT_ROOT))

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

DB = "ANALYTICS_DEV"
SCHEMA = "DBT_NFOLD_TRANSFORMATION"


def fetch_dicts(cur: snowflake.connector.DictCursor, sql: str, params: tuple = ()) -> list[dict]:
    cur.execute(sql, params)
    return [dict(row) for row in cur.fetchall()]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-suffix", required=True, help="Suffix after __SNAPSHOT_, e.g. 20260901_211248.")
    args = parser.parse_args()

    out_dir = REPO / "output" / f"full_refresh_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database=DB,
        schema=SCHEMA,
    )
    try:
        with conn.cursor(snowflake.connector.DictCursor) as cur:
            clear_rates = fetch_dicts(
                cur,
                """
                WITH loaded_months AS (
                    SELECT DISTINCT vendor, billing_month
                    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
                    WHERE billing_month BETWEEN '2026-01-01' AND '2026-08-01'
                )
                SELECT
                    o.vendor,
                    COUNT(DISTINCT o.billing_month) AS loaded_months,
                    COUNT(*) AS total_rows,
                    COUNT_IF(o.exception_type = 'Clear') AS clear_rows,
                    ROUND(100 * COUNT_IF(o.exception_type = 'Clear') / NULLIF(COUNT(*), 0), 1) AS clear_pct,
                    COUNT_IF(o.exception_type = 'Vendor Billing, No CW Billing') AS vendor_no_cw_rows,
                    COUNT_IF(o.exception_type = 'CW Billing, No Vendor Billing') AS cw_no_vendor_rows,
                    COUNT_IF(o.exception_type = 'Vendor Billing, Insufficient CW Billing') AS vendor_insuff_cw_rows,
                    COUNT_IF(o.exception_type = 'Unmapped Partner') AS unmapped_partner_rows,
                    COUNT_IF(o.exception_type = 'Other Issue') AS other_issue_rows
                FROM THIRD_PARTY_RECON_OUTPUT_PROD o
                JOIN loaded_months lm
                  ON lm.vendor = o.vendor
                 AND lm.billing_month = o.billing_month
                GROUP BY o.vendor
                ORDER BY clear_pct DESC, vendor
                """,
            )
            month_rates = fetch_dicts(
                cur,
                """
                WITH loaded_months AS (
                    SELECT DISTINCT vendor, billing_month
                    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
                    WHERE billing_month BETWEEN '2026-01-01' AND '2026-08-01'
                )
                SELECT
                    o.vendor,
                    TO_VARCHAR(o.billing_month, 'YYYY-MM') AS billing_month,
                    'LOADED' AS data_load_status,
                    COUNT(*) AS total_rows,
                    COUNT_IF(o.exception_type = 'Clear') AS clear_rows,
                    ROUND(100 * COUNT_IF(o.exception_type = 'Clear') / NULLIF(COUNT(*), 0), 1) AS clear_pct
                FROM THIRD_PARTY_RECON_OUTPUT_PROD o
                JOIN loaded_months lm
                  ON lm.vendor = o.vendor
                 AND lm.billing_month = o.billing_month
                GROUP BY o.vendor, o.billing_month
                ORDER BY o.vendor, o.billing_month
                """,
            )
            app_counts = fetch_dicts(
                cur,
                """
                SELECT 'THIRD_PARTY_RECON_VENDOR_USAGE_PROD' AS object_name, COUNT(*) AS row_count, COUNT(DISTINCT vendor) AS vendors FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
                UNION ALL
                SELECT 'THIRD_PARTY_RECON_VENDOR_INVOICES', COUNT(*), COUNT(DISTINCT vendor) FROM THIRD_PARTY_RECON_VENDOR_INVOICES
                UNION ALL
                SELECT 'THIRD_PARTY_RECON_DETAIL_PROD', COUNT(*), COUNT(DISTINCT vendor) FROM THIRD_PARTY_RECON_DETAIL_PROD
                UNION ALL
                SELECT 'THIRD_PARTY_RECON_OUTPUT_PROD', COUNT(*), COUNT(DISTINCT vendor) FROM THIRD_PARTY_RECON_OUTPUT_PROD
                UNION ALL
                SELECT 'THIRD_PARTY_RECON_SUMMARY_PROD', COUNT(*), COUNT(DISTINCT vendor) FROM THIRD_PARTY_RECON_SUMMARY_PROD
                UNION ALL
                SELECT 'THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD', COUNT(*), COUNT(DISTINCT vendor) FROM THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
                """,
            )
            coverage = fetch_dicts(
                cur,
                """
                WITH vendors AS (
                    SELECT column1 AS vendor
                    FROM VALUES
                        ('Acronis'), ('Auvik'), ('Bitdefender'), ('ESET'), ('Exium'),
                        ('KeepIT'), ('Proofpoint'), ('SentinelOne'), ('Webroot')
                ),
                usage_months AS (
                    SELECT vendor, COUNT(DISTINCT billing_month) AS usage_months,
                           MIN(billing_month) AS first_usage_month, MAX(billing_month) AS last_usage_month,
                           COUNT(*) AS usage_rows
                    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
                    WHERE billing_month BETWEEN '2026-01-01' AND '2026-08-01'
                    GROUP BY vendor
                ),
                invoice_months AS (
                    SELECT vendor, COUNT(DISTINCT billing_month) AS invoice_months,
                           MIN(billing_month) AS first_invoice_month, MAX(billing_month) AS last_invoice_month,
                           COUNT(*) AS invoice_rows
                    FROM THIRD_PARTY_RECON_VENDOR_INVOICES
                    WHERE billing_month BETWEEN '2026-01-01' AND '2026-08-01'
                    GROUP BY vendor
                )
                SELECT
                    v.vendor,
                    COALESCE(u.usage_months, 0) AS usage_months,
                    u.first_usage_month,
                    u.last_usage_month,
                    COALESCE(u.usage_rows, 0) AS usage_rows,
                    COALESCE(i.invoice_months, 0) AS invoice_months,
                    i.first_invoice_month,
                    i.last_invoice_month,
                    COALESCE(i.invoice_rows, 0) AS invoice_rows
                FROM vendors v
                LEFT JOIN usage_months u ON u.vendor = v.vendor
                LEFT JOIN invoice_months i ON i.vendor = v.vendor
                ORDER BY v.vendor
                """,
            )
            exception_mix = fetch_dicts(
                cur,
                """
                SELECT
                    vendor,
                    exception_type,
                    COUNT(*) AS row_count,
                    ROUND(SUM(est_dollar_impact), 2) AS est_dollar_impact
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                WHERE billing_month BETWEEN '2026-01-01' AND '2026-08-01'
                GROUP BY vendor, exception_type
                ORDER BY vendor, row_count DESC
                """,
            )
            map_integrity = []
            for name in ["THIRD_PARTY_RECON_PARTNER_MAP_PROD", "THIRD_PARTY_RECON_SKU_MAP_PROD"]:
                snap = f"{name}__SNAPSHOT_{args.snapshot_suffix}"
                cur.execute(f"SELECT COUNT(*) AS ROW_COUNT, HASH_AGG(*) AS HASH_VALUE FROM {name}")
                current = dict(cur.fetchone())
                cur.execute(f'SELECT COUNT(*) AS ROW_COUNT, HASH_AGG(*) AS HASH_VALUE FROM "{snap}"')
                before = dict(cur.fetchone())
                map_integrity.append(
                    {
                        "object_name": name,
                        "current_rows": current["ROW_COUNT"],
                        "snapshot_rows": before["ROW_COUNT"],
                        "row_delta": current["ROW_COUNT"] - before["ROW_COUNT"],
                        "hash_matches_snapshot": current["HASH_VALUE"] == before["HASH_VALUE"],
                    }
                )

    finally:
        conn.close()

    write_csv(out_dir / "clear_rates_jan_aug_2026.csv", clear_rates)
    write_csv(out_dir / "month_rates_jan_aug_2026.csv", month_rates)
    write_csv(out_dir / "app_table_counts.csv", app_counts)
    write_csv(out_dir / "usage_invoice_coverage_jan_aug_2026.csv", coverage)
    write_csv(out_dir / "exception_mix_jan_aug_2026.csv", exception_mix)
    write_csv(out_dir / "governed_map_integrity.csv", map_integrity)
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "snapshot_suffix": args.snapshot_suffix,
                "report_dir": str(out_dir),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Report written to {out_dir}")
    print("\nWeighted clear rates, Jan-Aug 2026 loaded months:")
    for row in clear_rates:
        print(
            f"  {row['VENDOR']:<13} {row['CLEAR_PCT']:>5}% "
            f"({row['CLEAR_ROWS']:,}/{row['TOTAL_ROWS']:,}, months={row['LOADED_MONTHS']})"
        )
    print("\nGoverned map integrity:")
    for row in map_integrity:
        print(
            f"  {row['object_name']}: rows {row['snapshot_rows']:,} -> {row['current_rows']:,}; "
            f"hash_matches_snapshot={row['hash_matches_snapshot']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
