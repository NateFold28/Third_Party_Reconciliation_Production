from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
PROJECTS = Path(r"C:\Users\Nate.Fold\projects")
OUT_DIR = REPO / "outputs" / f"quantity_logic_audit_{datetime.now():%Y%m%d_%H%M%S}"

sys.path.insert(0, str(PROJECTS))
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

USE = """
USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;
"""

VENDORS = ("SentinelOne", "ESET", "Webroot", "KeepIT")
VENDOR_IN = ", ".join(f"'{v}'" for v in VENDORS)


def query_df(conn, sql: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close()
    return pd.DataFrame(rows, columns=cols)


def write_df(df: pd.DataFrame, name: str) -> None:
    path = OUT_DIR / name
    df.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        conn.execute_string(USE)

        columns = query_df(conn, f"""
            SELECT table_name, ordinal_position, column_name, data_type
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE table_schema = 'DBT_NFOLD_TRANSFORMATION'
              AND (
                    table_name IN (
                        'THIRD_PARTY_RECON_VENDOR_USAGE_PROD',
                        'THIRD_PARTY_RECON_SOURCE_ZUORA_PROD',
                        'THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD',
                        'THIRD_PARTY_RECON_SOURCE_TRT_PROD',
                        'THIRD_PARTY_RECON_DETAIL_PROD',
                        'THIRD_PARTY_RECON_OUTPUT_PROD'
                    )
                    OR table_name IN (
                        'SENTINELONE_RECON_DETAIL',
                        'ESET_RECON_DETAIL',
                        'WEBROOT_RECON_DETAIL',
                        'KEEPIT_RECON_DETAIL'
                    )
              )
            ORDER BY table_name, ordinal_position
        """)
        write_df(columns, "table_columns.csv")

        usage_profile = query_df(conn, f"""
            SELECT
                vendor,
                billing_month::DATE AS billing_month,
                COUNT(*) AS row_count,
                SUM(COALESCE(quantity, 0)) AS sum_quantity,
                SUM(COALESCE(TRY_TO_NUMBER(modifier), 0)) AS sum_modifier,
                SUM(COALESCE(amount, 0)) AS sum_amount,
                COUNT_IF(COALESCE(quantity, 0) <> COALESCE(TRY_TO_NUMBER(modifier), 0)) AS quantity_modifier_diff_rows,
                MIN(quantity) AS min_quantity,
                MAX(quantity) AS max_quantity,
                MIN(TRY_TO_NUMBER(modifier)) AS min_modifier,
                MAX(TRY_TO_NUMBER(modifier)) AS max_modifier
            FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
            WHERE vendor IN ({VENDOR_IN})
            GROUP BY 1, 2
            ORDER BY 1, 2
        """)
        write_df(usage_profile, "vendor_usage_quantity_profile.csv")

        billing_profile = query_df(conn, f"""
            WITH zuora AS (
                SELECT vendor, billing_month::DATE AS billing_month,
                       COUNT(*) AS zuora_rows,
                       SUM(COALESCE(qty, 0)) AS zuora_quantity,
                       SUM(COALESCE(charge_amount_usd, 0)) AS zuora_amount
                FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
                WHERE vendor IN ({VENDOR_IN})
                GROUP BY 1, 2
            ),
            mp AS (
                SELECT vendor, billing_month::DATE AS billing_month,
                       COUNT(*) AS marketplace_rows,
                       SUM(COALESCE(qty, 0)) AS marketplace_quantity,
                       SUM(COALESCE(amount, 0)) AS marketplace_amount
                FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
                WHERE vendor IN ({VENDOR_IN})
                GROUP BY 1, 2
            ),
            trt AS (
                SELECT vendor, billing_month::DATE AS billing_month,
                       COUNT(*) AS trt_rows,
                       SUM(COALESCE(trt_quantity, 0)) AS trt_quantity,
                       SUM(COALESCE(avg_api_quantity, 0)) AS avg_api_quantity
                FROM THIRD_PARTY_RECON_SOURCE_TRT_PROD
                WHERE vendor IN ({VENDOR_IN})
                GROUP BY 1, 2
            ),
            grid AS (
                SELECT vendor, billing_month FROM zuora
                UNION SELECT vendor, billing_month FROM mp
                UNION SELECT vendor, billing_month FROM trt
            )
            SELECT
                g.vendor,
                g.billing_month,
                COALESCE(z.zuora_rows, 0) AS zuora_rows,
                COALESCE(z.zuora_quantity, 0) AS zuora_quantity,
                COALESCE(z.zuora_amount, 0) AS zuora_amount,
                COALESCE(m.marketplace_rows, 0) AS marketplace_rows,
                COALESCE(m.marketplace_quantity, 0) AS marketplace_quantity,
                COALESCE(m.marketplace_amount, 0) AS marketplace_amount,
                COALESCE(t.trt_rows, 0) AS trt_rows,
                COALESCE(t.trt_quantity, 0) AS trt_quantity,
                COALESCE(t.avg_api_quantity, 0) AS avg_api_quantity
            FROM grid g
            LEFT JOIN zuora z ON z.vendor = g.vendor AND z.billing_month = g.billing_month
            LEFT JOIN mp m ON m.vendor = g.vendor AND m.billing_month = g.billing_month
            LEFT JOIN trt t ON t.vendor = g.vendor AND t.billing_month = g.billing_month
            ORDER BY 1, 2
        """)
        write_df(billing_profile, "billing_source_quantity_profile.csv")

        output_profile = query_df(conn, f"""
            SELECT
                vendor,
                billing_month,
                COUNT(*) AS row_count,
                COUNT_IF(exception_type = 'Clear') AS clear_rows,
                ROUND(COUNT_IF(exception_type = 'Clear') * 100.0 / NULLIF(COUNT(*), 0), 1) AS clear_pct,
                SUM(COALESCE(vendor_quantity, 0)) AS vendor_quantity,
                SUM(COALESCE(total_billing_quantity, 0)) AS total_billing_quantity,
                ROUND(SUM(COALESCE(total_billing_quantity, 0)) / NULLIF(SUM(COALESCE(vendor_quantity, 0)), 0), 3) AS billing_to_vendor_qty_ratio,
                SUM(ABS(COALESCE(qty_delta, 0))) AS abs_qty_delta,
                SUM(COALESCE(vendor_amount, 0)) AS vendor_amount,
                SUM(COALESCE(total_billing_amount, 0)) AS billing_amount
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            WHERE vendor IN ({VENDOR_IN})
            GROUP BY 1, 2
            ORDER BY 1, 2
        """)
        write_df(output_profile, "output_quantity_profile.csv")

        detail_profile = query_df(conn, f"""
            SELECT
                vendor,
                billing_month,
                billing_source_mix,
                outcome_flag,
                COUNT(*) AS row_count,
                SUM(COALESCE(vendor_quantity, 0)) AS vendor_quantity,
                SUM(COALESCE(zuora_quantity, 0)) AS zuora_quantity,
                SUM(COALESCE(marketplace_quantity, 0)) AS marketplace_quantity,
                SUM(COALESCE(total_billing_quantity, 0)) AS total_billing_quantity,
                SUM(ABS(COALESCE(qty_delta, 0))) AS abs_qty_delta,
                SUM(COALESCE(vendor_amount, 0)) AS vendor_amount,
                SUM(COALESCE(total_billing_amount, 0)) AS billing_amount
            FROM THIRD_PARTY_RECON_DETAIL_PROD
            WHERE vendor IN ({VENDOR_IN})
            GROUP BY 1, 2, 3, 4
            ORDER BY 1, 2, 5 DESC
        """)
        write_df(detail_profile, "detail_outcome_quantity_profile.csv")

        product_profile = query_df(conn, f"""
            SELECT *
            FROM (
                SELECT
                    vendor,
                    billing_month,
                    vendor_product,
                    sku_match_group,
                    exception_type,
                    COUNT(*) AS row_count,
                    SUM(COALESCE(vendor_quantity, 0)) AS vendor_quantity,
                    SUM(COALESCE(total_billing_quantity, 0)) AS total_billing_quantity,
                    ROUND(SUM(COALESCE(total_billing_quantity, 0)) / NULLIF(SUM(COALESCE(vendor_quantity, 0)), 0), 3) AS billing_to_vendor_qty_ratio,
                    SUM(ABS(COALESCE(qty_delta, 0))) AS abs_qty_delta,
                    ROW_NUMBER() OVER (
                        PARTITION BY vendor
                        ORDER BY SUM(ABS(COALESCE(qty_delta, 0))) DESC
                    ) AS rn
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                WHERE vendor IN ({VENDOR_IN})
                GROUP BY 1, 2, 3, 4, 5
            )
            WHERE rn <= 80
            ORDER BY vendor, rn
        """)
        write_df(product_profile, "top_product_quantity_gaps.csv")

        key_dup_profile = query_df(conn, f"""
            SELECT *
            FROM (
                SELECT
                    vendor,
                    billing_month,
                    sf_id,
                    vendor_product,
                    sku_match_group,
                    COUNT(*) AS row_count,
                    COUNT(DISTINCT billing_source_mix) AS source_mix_count,
                    SUM(COALESCE(vendor_quantity, 0)) AS vendor_quantity,
                    SUM(COALESCE(total_billing_quantity, 0)) AS total_billing_quantity,
                    SUM(ABS(COALESCE(qty_delta, 0))) AS abs_qty_delta,
                    ROW_NUMBER() OVER (
                        PARTITION BY vendor
                        ORDER BY COUNT(*) DESC, SUM(ABS(COALESCE(qty_delta, 0))) DESC
                    ) AS rn
                FROM THIRD_PARTY_RECON_DETAIL_PROD
                WHERE vendor IN ({VENDOR_IN})
                GROUP BY 1, 2, 3, 4, 5
                HAVING COUNT(*) > 1
            )
            WHERE rn <= 100
            ORDER BY vendor, rn
        """)
        write_df(key_dup_profile, "duplicate_recon_key_profile.csv")

        duplicate_detail_rows = query_df(conn, """
            WITH dup_keys AS (
                SELECT vendor, billing_month, sf_id, vendor_product
                FROM THIRD_PARTY_RECON_DETAIL_PROD
                WHERE vendor IN ('SentinelOne', 'KeepIT', 'Webroot')
                GROUP BY 1, 2, 3, 4
                HAVING COUNT(*) > 1
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY vendor
                    ORDER BY SUM(ABS(COALESCE(qty_delta, 0))) DESC, COUNT(*) DESC
                ) <= 25
            )
            SELECT
                d.vendor,
                d.billing_month,
                d.sf_id,
                d.vendor_partner_name,
                d.vendor_product,
                d.sku_match_group,
                d.billing_source_mix,
                d.outcome_flag,
                d.vendor_quantity,
                d.zuora_quantity,
                d.marketplace_quantity,
                d.total_billing_quantity,
                d.qty_delta,
                d.vendor_amount,
                d.total_billing_amount
            FROM THIRD_PARTY_RECON_DETAIL_PROD d
            JOIN dup_keys k
              ON k.vendor = d.vendor
             AND k.billing_month = d.billing_month
             AND COALESCE(k.sf_id, '') = COALESCE(d.sf_id, '')
             AND COALESCE(k.vendor_product, '') = COALESCE(d.vendor_product, '')
            ORDER BY d.vendor, d.billing_month, d.sf_id, d.vendor_product, d.billing_source_mix
        """)
        write_df(duplicate_detail_rows, "duplicate_detail_rows.csv")

        sentinelone_source_dupes = query_df(conn, """
            WITH dup_keys AS (
                SELECT billing_month, sf_id, sku_match_group
                FROM SENTINELONE_RECON_DETAIL
                GROUP BY 1, 2, 3
                HAVING COUNT(*) > 1
                QUALIFY ROW_NUMBER() OVER (
                    ORDER BY SUM(ABS(COALESCE(qty_delta, 0))) DESC, COUNT(*) DESC
                ) <= 50
            )
            SELECT
                d.billing_month,
                d.sf_id,
                d.vendor_partner_name,
                d.vendor_product,
                d.sku_match_group,
                d.billing_source_mix,
                d.outcome_flag,
                d.vendor_quantity,
                d.zuora_quantity,
                d.marketplace_quantity,
                d.total_billing_quantity,
                d.qty_delta,
                d.vendor_amount,
                d.total_billing_amount
            FROM SENTINELONE_RECON_DETAIL d
            JOIN dup_keys k
              ON k.billing_month = d.billing_month
             AND COALESCE(k.sf_id, '') = COALESCE(d.sf_id, '')
             AND COALESCE(k.sku_match_group, '') = COALESCE(d.sku_match_group, '')
            ORDER BY d.billing_month, d.sf_id, d.sku_match_group, d.billing_source_mix
        """)
        write_df(sentinelone_source_dupes, "sentinelone_source_duplicate_rows.csv")

    finally:
        conn.close()

    with (OUT_DIR / "README.md").open("w", encoding="utf-8", newline="\n") as f:
        f.write("# Quantity Logic Audit\n\n")
        f.write(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        for path in sorted(OUT_DIR.glob("*.csv")):
            f.write(f"- `{path.name}`\n")
    print(f"Audit written to: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
