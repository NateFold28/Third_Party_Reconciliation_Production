from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
PROJECTS = Path(r"C:\Users\Nate.Fold\projects")
OUT_DIR = REPO / "outputs" / f"vendor_logic_probe_{datetime.now():%Y%m%d_%H%M%S}"

sys.path.insert(0, str(PROJECTS))
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


def query_df(conn, sql: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close()
    return pd.DataFrame(rows, columns=cols)


def write_df(df: pd.DataFrame, name: str) -> None:
    df.to_csv(OUT_DIR / name, index=False, quoting=csv.QUOTE_MINIMAL)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        conn.execute_string("""
            USE ROLE DEVELOPER;
            USE WAREHOUSE REPORTING_WH;
            USE DATABASE ANALYTICS_DEV;
            USE SCHEMA DBT_NFOLD_TRANSFORMATION;
        """)

        write_df(query_df(conn, """
            SELECT vendor_sku, cw_sku, sku_match_key, COUNT(*) AS row_count
            FROM RECON_SKU_MAP
            WHERE vendor = 'SentinelOne'
              AND (sku_match_key ILIKE 'S1_%' OR sku_match_key IN ('COMPLETE', 'CONTROL'))
            GROUP BY 1, 2, 3
            ORDER BY vendor_sku, cw_sku, sku_match_key
        """), "sentinelone_endpoint_sku_map.csv")

        write_df(query_df(conn, """
            SELECT
                billing_month::DATE AS billing_month,
                COALESCE(modifier, '<NULL>') AS modifier_stream,
                vendor_product_sku,
                COUNT(*) AS row_count,
                SUM(COALESCE(quantity, 0)) AS quantity,
                SUM(COALESCE(amount, 0)) AS amount
            FROM WEBROOT_USAGE
            GROUP BY 1, 2, 3
            ORDER BY 1, 2, 3
        """), "webroot_raw_usage_by_stream.csv")

        write_df(query_df(conn, """
            SELECT
                billing_month,
                recon_stream,
                sku_match_group,
                billing_source_mix,
                outcome_flag,
                COUNT(*) AS row_count,
                SUM(COALESCE(vendor_quantity, 0)) AS vendor_quantity,
                SUM(COALESCE(zuora_quantity, 0)) AS zuora_quantity,
                SUM(COALESCE(marketplace_quantity, 0)) AS marketplace_quantity,
                SUM(COALESCE(total_billing_quantity, 0)) AS total_billing_quantity,
                SUM(ABS(COALESCE(qty_delta, 0))) AS abs_qty_delta
            FROM WEBROOT_RECON_DETAIL
            GROUP BY 1, 2, 3, 4, 5
            ORDER BY 1, 2, 3, 6 DESC
        """), "webroot_detail_by_stream.csv")

        write_df(query_df(conn, """
            SELECT
                billing_month,
                source_family,
                vendor_product AS sku_match_group,
                billing_source_mix,
                outcome_flag,
                COUNT(*) AS row_count,
                SUM(COALESCE(vendor_quantity, 0)) AS vendor_quantity,
                SUM(COALESCE(zuora_quantity, 0)) AS zuora_quantity,
                SUM(COALESCE(marketplace_quantity, 0)) AS carr_quantity,
                SUM(COALESCE(total_billing_quantity, 0)) AS total_billing_quantity,
                SUM(ABS(COALESCE(qty_delta, 0))) AS abs_qty_delta
            FROM KEEPIT_RECON_DETAIL
            GROUP BY 1, 2, 3, 4, 5
            ORDER BY 1, 2, 3, 6 DESC
        """), "keepit_detail_by_source_family.csv")

        write_df(query_df(conn, """
            SELECT
                billing_month::DATE AS billing_month,
                source_family,
                sku_match_group,
                COUNT(*) AS row_count,
                SUM(COALESCE(quantity, 0)) AS quantity,
                SUM(COALESCE(recon_amount, amount, 0)) AS amount,
                COUNT_IF(sf_id IS NULL) AS unmapped_rows,
                SUM(IFF(sf_id IS NULL, COALESCE(quantity, 0), 0)) AS unmapped_quantity
            FROM KEEPIT_VENDOR_USAGE_MASTER
            GROUP BY 1, 2, 3
            ORDER BY 1, 2, 3
        """), "keepit_raw_by_source_family.csv")

        print(f"Probe written to: {OUT_DIR}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
