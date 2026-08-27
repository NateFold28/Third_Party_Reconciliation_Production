from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from TEMPLATES.Python.connection import get_snowflake_connection


def show(title: str, df: pd.DataFrame, max_rows: int = 80) -> None:
    print(f"\n## {title}")
    if df.empty:
        print("(empty)")
        return
    with pd.option_context("display.max_rows", max_rows, "display.max_columns", 60, "display.width", 300):
        print(df.head(max_rows).to_string(index=False))


def query(cur, title: str, sql: str, max_rows: int = 80) -> pd.DataFrame:
    cur.execute(sql)
    df = cur.fetch_pandas_all()
    show(title, df, max_rows=max_rows)
    return df


def main() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        cur = conn.cursor()
        month_filter = "billing_month IN ('2026-02-01'::DATE, '2026-04-01'::DATE)"
        query(
            cur,
            "A. Raw Vendor Usage Loaded",
            f"""
            SELECT
                billing_month,
                COALESCE(modifier, 'MAIN') AS modifier,
                vendor_product_sku,
                COUNT(*) AS row_count,
                SUM(quantity) AS qty,
                SUM(amount) AS amount
            FROM third_party_recon_vendor_usage_prod
            WHERE vendor = 'KeepIT'
              AND {month_filter}
            GROUP BY 1,2,3
            ORDER BY 1,2,3
            """,
            120,
        )
        query(
            cur,
            "B. Vendor Invoice vs Raw Usage Control",
            f"""
            SELECT
                billing_month,
                sku,
                vendor_invoice_sku,
                vendor_usage_sku,
                vendor_invoice_seats,
                vendor_raw_usage_seats,
                delta_seats,
                vendor_invoice_amount,
                vendor_raw_usage_amount,
                source_status
            FROM third_party_recon_vendor_invoice_usage_intra_prod
            WHERE vendor = 'KeepIT'
              AND {month_filter}
            ORDER BY billing_month, sku
            """,
            120,
        )
        query(
            cur,
            "C. Zuora Source Total by SKU and Charge Family",
            f"""
            SELECT
                billing_month,
                product_sku,
                CASE
                    WHEN product_sku ILIKE '%PROMO%' OR charge_name ILIKE '%PROMO%' OR product_name ILIKE '%PROMO%' THEN 'PROMO'
                    WHEN product_sku ILIKE '%TAKEOUT%' OR charge_name ILIKE '%TAKEOUT%' OR product_name ILIKE '%TAKEOUT%' THEN 'TAKEOUT'
                    ELSE 'MAIN'
                END AS inferred_family,
                COUNT(*) AS row_count,
                SUM(qty) AS qty,
                SUM(charge_amount_usd) AS amount
            FROM third_party_recon_source_zuora_prod
            WHERE vendor = 'KeepIT'
              AND {month_filter}
            GROUP BY 1,2,3
            ORDER BY 1,3,2
            """,
            120,
        )
        query(
            cur,
            "D. KEEPIT_RECON_DETAIL Totals by Source Family and Outcome",
            f"""
            SELECT
                billing_month,
                source_family,
                outcome_flag,
                COUNT(*) AS row_count,
                SUM(vendor_quantity) AS vendor_qty,
                SUM(total_billing_quantity) AS billing_qty,
                SUM(zuora_quantity) AS zuora_qty,
                SUM(vendor_amount) AS vendor_amount,
                SUM(total_billing_amount) AS billing_amount
            FROM keepit_recon_detail
            WHERE {month_filter}
            GROUP BY 1,2,3
            ORDER BY 1,2,ABS(SUM(COALESCE(vendor_quantity,0))-SUM(COALESCE(total_billing_quantity,0))) DESC
            """,
            160,
        )
        query(
            cur,
            "E. Unified DETAIL_PROD Totals",
            f"""
            SELECT
                billing_month,
                outcome_flag,
                COUNT(*) AS row_count,
                SUM(vendor_quantity) AS vendor_qty,
                SUM(total_billing_quantity) AS billing_qty,
                SUM(vendor_amount) AS vendor_amount,
                SUM(total_billing_amount) AS billing_amount
            FROM third_party_recon_detail_prod
            WHERE vendor = 'KeepIT'
              AND {month_filter}
            GROUP BY 1,2
            ORDER BY 1, ABS(SUM(COALESCE(vendor_quantity,0))-SUM(COALESCE(total_billing_quantity,0))) DESC
            """,
            120,
        )
        query(
            cur,
            "F. App OUTPUT_PROD Totals",
            f"""
            SELECT
                billing_month,
                exception_type,
                COUNT(*) AS row_count,
                SUM(vendor_quantity) AS vendor_qty,
                SUM(total_billing_quantity) AS billing_qty,
                SUM(vendor_amount) AS vendor_amount,
                SUM(total_billing_amount) AS billing_amount
            FROM third_party_recon_output_prod
            WHERE vendor = 'KeepIT'
              AND {month_filter}
            GROUP BY 1,2
            ORDER BY 1, ABS(SUM(COALESCE(vendor_quantity,0))-SUM(COALESCE(total_billing_quantity,0))) DESC
            """,
            120,
        )
        query(
            cur,
            "G. App SUMMARY_PROD Seat Trend Source",
            f"""
            SELECT
                vendor,
                billing_month,
                usage_row_count,
                total_rows,
                total_vendor_seats,
                total_billing_seats,
                total_vendor_amount,
                total_billing_amount,
                clear_pct,
                vendor_no_cw_rows,
                cw_no_vendor_rows,
                vendor_insuff_cw_rows,
                unmapped_partner_rows
            FROM third_party_recon_summary_prod
            WHERE vendor = 'KeepIT'
              AND {month_filter}
            ORDER BY billing_month
            """,
            20,
        )
        query(
            cur,
            "H. KeepIT Rows Excluded by Output Filter Regex",
            f"""
            WITH filtered AS (
                SELECT *
                FROM third_party_recon_detail_prod
                WHERE vendor = 'KeepIT'
                  AND {month_filter}
                  AND RLIKE(
                      LOWER(COALESCE(vendor_partner_name, '')),
                      '(connectwise|continuum|internal|test|devqa|recoverprod|pm continuum|pm connectwise)'
                  )
            )
            SELECT
                billing_month,
                vendor_partner_name,
                outcome_flag,
                COUNT(*) AS row_count,
                SUM(vendor_quantity) AS vendor_qty,
                SUM(total_billing_quantity) AS billing_qty,
                SUM(vendor_amount) AS vendor_amount,
                SUM(total_billing_amount) AS billing_amount
            FROM filtered
            GROUP BY 1,2,3
            ORDER BY 1, ABS(SUM(COALESCE(vendor_quantity,0))-SUM(COALESCE(total_billing_quantity,0))) DESC
            """,
            120,
        )
        query(
            cur,
            "I. Zuora Rows Mapped to CW-only Keys",
            f"""
            WITH sm AS (
                SELECT DISTINCT
                    UPPER(TRIM(sku_match_key)) AS sku_match_group,
                    UPPER(TRIM(tok.value)) AS cw_sku_token
                FROM third_party_recon_sku_map_prod,
                     LATERAL SPLIT_TO_TABLE(REPLACE(cw_sku, '/', '|'), '|') tok
                WHERE vendor = 'KeepIT'
                  AND sku_match_key IS NOT NULL
                  AND cw_sku IS NOT NULL
                  AND TRIM(tok.value) <> ''
            )
            SELECT
                z.billing_month,
                z.product_sku,
                sm.sku_match_group,
                COUNT(*) AS row_count,
                SUM(z.qty) AS qty,
                SUM(z.charge_amount_usd) AS amount
            FROM third_party_recon_source_zuora_prod z
            JOIN sm ON sm.cw_sku_token = UPPER(TRIM(z.product_sku))
            WHERE z.vendor = 'KeepIT'
              AND {month_filter}
              AND sm.sku_match_group ILIKE 'KEEPIT_CW_ONLY%'
            GROUP BY 1,2,3
            ORDER BY 1, qty DESC
            """,
            120,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
