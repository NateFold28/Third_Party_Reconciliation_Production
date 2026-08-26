from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
PROJECTS = Path(r"C:\Users\Nate.Fold\projects")
sys.path.insert(0, str(PROJECTS))

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

USE_SQL = """
USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;
"""


BASELINE = {
    "row_count": 90462,
    "clear_rows": 56321,
    "clear_pct": 62.26,
    "abs_qty_delta": 43553242.0,
    "abs_amount_delta": 45523105.56,
    "outcomes": {
        "Unmapped Partner": 394,
        "Vendor Billing, No CW Billing": 7304,
        "CW Billing, No Vendor Billing": 2857,
        "Vendor Billing, Insufficient CW Billing": 2025,
        "API Usage, Insufficient CW Billing": 5997,
        "Vendor SKU, No CW SKU": 1799,
    },
}


def query_df(conn, sql: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close()
    return pd.DataFrame(rows, columns=cols)


def main() -> int:
    out_dir = REPO / "outputs" / f"post_finding_validation_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        conn.execute_string(USE_SQL)
        overall = query_df(
            conn,
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT vendor) AS vendor_count,
                COUNT_IF(exception_type = 'Clear') AS clear_rows,
                ROUND(COUNT_IF(exception_type = 'Clear') * 100.0 / NULLIF(COUNT(*), 0), 2) AS clear_pct,
                ROUND(SUM(ABS(COALESCE(qty_delta, 0))), 0) AS abs_qty_delta,
                ROUND(SUM(ABS(COALESCE(amount_delta, 0))), 2) AS abs_amount_delta
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            """,
        )
        outcomes = query_df(
            conn,
            """
            SELECT
                exception_type,
                COUNT(*) AS row_count,
                ROUND(SUM(ABS(COALESCE(qty_delta, 0))), 0) AS abs_qty_delta,
                ROUND(SUM(ABS(COALESCE(amount_delta, 0))), 2) AS abs_amount_delta
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            GROUP BY exception_type
            ORDER BY row_count DESC
            """,
        )
        vendor_metrics = query_df(
            conn,
            """
            WITH loaded_months AS (
                SELECT vendor, billing_month
                FROM THIRD_PARTY_RECON_SUMMARY_PROD
                WHERE data_load_status = 'LOADED'
            )
            SELECT
                o.vendor,
                COUNT_IF(l.billing_month IS NOT NULL) AS row_count_loaded,
                COUNT_IF(l.billing_month IS NOT NULL AND o.exception_type = 'Clear') AS clear_rows_loaded,
                ROUND(COUNT_IF(l.billing_month IS NOT NULL AND o.exception_type = 'Clear') * 100.0
                    / NULLIF(COUNT_IF(l.billing_month IS NOT NULL), 0), 2) AS clear_pct_loaded,
                ROUND(SUM(IFF(l.billing_month IS NOT NULL, ABS(COALESCE(o.qty_delta, 0)), 0)), 0) AS abs_qty_delta_loaded
            FROM THIRD_PARTY_RECON_OUTPUT_PROD o
            LEFT JOIN loaded_months l
              ON l.vendor = o.vendor
             AND l.billing_month = o.billing_month
            GROUP BY o.vendor
            ORDER BY clear_pct_loaded DESC NULLS LAST
            """,
        )
        map_counts = query_df(
            conn,
            """
            SELECT 'THIRD_PARTY_RECON_PARTNER_MAP_PROD' AS table_name, COUNT(*) AS row_count FROM THIRD_PARTY_RECON_PARTNER_MAP_PROD
            UNION ALL SELECT 'RECON_PARTNER_MAP', COUNT(*) FROM RECON_PARTNER_MAP
            UNION ALL SELECT 'THIRD_PARTY_RECON_SKU_MAP_PROD', COUNT(*) FROM THIRD_PARTY_RECON_SKU_MAP_PROD
            UNION ALL SELECT 'RECON_SKU_MAP', COUNT(*) FROM RECON_SKU_MAP
            ORDER BY table_name
            """,
        )
        keepit_unmapped = query_df(
            conn,
            """
            SELECT
                vendor_partner_name,
                COUNT(*) AS row_count,
                ROUND(SUM(COALESCE(vendor_quantity, 0)), 0) AS vendor_quantity,
                ROUND(SUM(COALESCE(vendor_amount, 0)), 2) AS vendor_amount
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            WHERE vendor = 'KeepIT'
              AND exception_type = 'Unmapped Partner'
            GROUP BY vendor_partner_name
            ORDER BY vendor_amount DESC, vendor_quantity DESC
            """,
        )
        proofpoint_pipe_unmapped = query_df(
            conn,
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT vendor_partner_name) AS partner_groups,
                ROUND(SUM(COALESCE(vendor_amount, 0)), 2) AS vendor_amount
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            WHERE vendor = 'Proofpoint'
              AND exception_type = 'Unmapped Partner'
              AND vendor_partner_name LIKE '%|%'
            """,
        )
        s1_addons = query_df(
            conn,
            """
            SELECT
                vendor_product,
                sku_match_group,
                cw_skus,
                exception_type,
                COUNT(*) AS row_count,
                COUNT(DISTINCT sf_id) AS account_count,
                ROUND(SUM(COALESCE(vendor_quantity, 0)), 0) AS vendor_quantity,
                ROUND(SUM(ABS(COALESCE(qty_delta, 0))), 0) AS abs_qty_delta
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            WHERE vendor = 'SentinelOne'
              AND (
                  UPPER(COALESCE(vendor_product, '')) LIKE '%PURPLE%'
               OR UPPER(COALESCE(vendor_product, '')) LIKE '%RANGER INSIGHT%'
               OR UPPER(COALESCE(vendor_product, '')) LIKE '%RANGER AD%'
               OR UPPER(COALESCE(vendor_product, '')) LIKE '%WATCHTOWER%'
              )
            GROUP BY vendor_product, sku_match_group, cw_skus, exception_type
            ORDER BY exception_type, vendor_product
            """,
        )
        invoice_usage = query_df(
            conn,
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT vendor) AS vendor_count,
                MIN(billing_month) AS min_month,
                MAX(billing_month) AS max_month,
                ROUND(SUM(ABS(COALESCE(delta_seats, 0))), 0) AS abs_delta_seats,
                ROUND(SUM(ABS(COALESCE(delta_amount, 0))), 2) AS abs_delta_amount
            FROM THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
            """,
        )

        current = overall.iloc[0].to_dict()
        outcome_lookup = {r["EXCEPTION_TYPE"]: int(r["ROW_COUNT"]) for r in outcomes.to_dict("records")}
        delta = {
            "row_count_delta": int(current["ROW_COUNT"]) - BASELINE["row_count"],
            "clear_rows_delta": int(current["CLEAR_ROWS"]) - BASELINE["clear_rows"],
            "clear_pct_delta_points": round(float(current["CLEAR_PCT"]) - BASELINE["clear_pct"], 2),
            "abs_qty_delta_change": float(current["ABS_QTY_DELTA"]) - BASELINE["abs_qty_delta"],
            "abs_amount_delta_change": float(current["ABS_AMOUNT_DELTA"]) - BASELINE["abs_amount_delta"],
            "outcome_row_deltas": {
                k: outcome_lookup.get(k, 0) - v for k, v in BASELINE["outcomes"].items()
            },
        }

        frames = {
            "overall.csv": overall,
            "outcomes.csv": outcomes,
            "vendor_metrics.csv": vendor_metrics,
            "map_counts.csv": map_counts,
            "keepit_unmapped.csv": keepit_unmapped,
            "proofpoint_pipe_unmapped.csv": proofpoint_pipe_unmapped,
            "s1_addons.csv": s1_addons,
            "invoice_usage.csv": invoice_usage,
        }
        for name, frame in frames.items():
            frame.to_csv(out_dir / name, index=False)

        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "baseline": BASELINE,
            "current": current,
            "delta": delta,
            "map_counts": map_counts.to_dict("records"),
            "vendor_metrics": vendor_metrics.to_dict("records"),
            "outcomes": outcomes.to_dict("records"),
            "keepit_unmapped": keepit_unmapped.to_dict("records"),
            "proofpoint_pipe_unmapped": proofpoint_pipe_unmapped.to_dict("records"),
            "s1_addons": s1_addons.to_dict("records"),
            "invoice_usage": invoice_usage.to_dict("records"),
        }
        (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(out_dir)
        print(json.dumps({"current": current, "delta": delta}, indent=2, default=str))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
