"""Deep gap audit for the combined third-party reconciliation pipeline.

Outputs CSVs that identify the biggest levers for improving clear rate and
reducing SUM(ABS(qty_delta)) without changing the locked app classifier.
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "outputs" / f"deep_gap_audit_{datetime.now():%Y%m%d_%H%M%S}"


def write_csv(name: str, columns: list[str], rows: list[tuple]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)
    print(f"Wrote {path.relative_to(REPO)} ({len(rows):,} rows)")


def fetch(cur, sql: str) -> tuple[list[str], list[tuple]]:
    cur.execute(sql)
    columns = [d[0].lower() for d in cur.description]
    return columns, cur.fetchall()


def main() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    cur = conn.cursor()

    audits = {
        "01_vendor_month_scorecard.csv": """
            SELECT
                vendor,
                billing_month,
                COUNT(*) AS row_count,
                COUNT_IF(exception_type = 'Clear') AS clear_rows,
                ROUND(100 * COUNT_IF(exception_type = 'Clear') / NULLIF(COUNT(*), 0), 2) AS clear_pct,
                ROUND(SUM(COALESCE(vendor_quantity, 0)), 2) AS vendor_qty,
                ROUND(SUM(COALESCE(total_billing_quantity, 0)), 2) AS cw_qty,
                ROUND(SUM(COALESCE(abs_qty_delta, 0)), 2) AS abs_qty_delta,
                ROUND(SUM(COALESCE(vendor_amount, 0)), 2) AS vendor_amount,
                ROUND(SUM(COALESCE(total_billing_amount, 0)), 2) AS cw_amount,
                ROUND(SUM(COALESCE(abs_amount_delta, 0)), 2) AS abs_amount_delta
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            GROUP BY 1, 2
            ORDER BY vendor, billing_month
        """,
        "02_vendor_exception_qty_impact.csv": """
            SELECT
                vendor,
                exception_type,
                COUNT(*) AS row_count,
                ROUND(SUM(COALESCE(abs_qty_delta, 0)), 2) AS abs_qty_delta,
                ROUND(SUM(COALESCE(est_dollar_impact, 0)), 2) AS est_dollar_impact,
                ROUND(SUM(COALESCE(vendor_quantity, 0)), 2) AS vendor_qty,
                ROUND(SUM(COALESCE(total_billing_quantity, 0)), 2) AS cw_qty
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            GROUP BY 1, 2
            ORDER BY abs_qty_delta DESC, est_dollar_impact DESC
        """,
        "03_top_abs_qty_rows.csv": """
            SELECT *
            FROM (
                SELECT
                    vendor,
                    billing_month,
                    exception_type,
                    sf_id,
                    vendor_partner_name,
                    vendor_product,
                    sku_match_group,
                    cw_skus,
                    ROUND(COALESCE(vendor_quantity, 0), 2) AS vendor_qty,
                    ROUND(COALESCE(total_billing_quantity, 0), 2) AS cw_qty,
                    ROUND(COALESCE(qty_delta, 0), 2) AS qty_delta,
                    ROUND(COALESCE(abs_qty_delta, 0), 2) AS abs_qty_delta,
                    ROUND(COALESCE(vendor_amount, 0), 2) AS vendor_amount,
                    ROUND(COALESCE(total_billing_amount, 0), 2) AS cw_amount,
                    ROUND(COALESCE(est_dollar_impact, 0), 2) AS est_dollar_impact,
                    investigation_reason,
                    ROW_NUMBER() OVER (
                        PARTITION BY vendor
                        ORDER BY COALESCE(abs_qty_delta, 0) DESC, COALESCE(est_dollar_impact, 0) DESC
                    ) AS vendor_rank
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                WHERE exception_type <> 'Clear'
            )
            WHERE vendor_rank <= 50
            ORDER BY vendor, vendor_rank
        """,
        "04_unmapped_partner_samples.csv": """
            SELECT
                vendor,
                vendor_partner_name,
                COUNT(*) AS row_count,
                ROUND(SUM(COALESCE(abs_qty_delta, 0)), 2) AS abs_qty_delta,
                ROUND(SUM(COALESCE(est_dollar_impact, 0)), 2) AS est_dollar_impact,
                LISTAGG(DISTINCT TO_VARCHAR(billing_month), ', ')
                    WITHIN GROUP (ORDER BY TO_VARCHAR(billing_month)) AS months
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            WHERE exception_type = 'Unmapped Partner'
            GROUP BY 1, 2
            ORDER BY est_dollar_impact DESC, abs_qty_delta DESC
            LIMIT 200
        """,
        "05_cw_sku_gap_top_rows.csv": """
            WITH vendor_filters AS (
                SELECT * FROM VALUES
                    ('Acronis', '%acronis%'),
                    ('Auvik', '%auvik%'),
                    ('Bitdefender', '%bitdefender%'),
                    ('ESET', '%eset%'),
                    ('Exium', '%exium%'),
                    ('KeepIT', '%keepit%'),
                    ('Proofpoint', '%proofpoint%'),
                    ('SentinelOne', '%sentinelone%'),
                    ('Webroot', '%webroot%')
                AS t(vendor, pattern)
            ),
            carr AS (
                SELECT prod_sku, SUM(COALESCE(arr_budget_rate, 0) / 12) AS mrr_summed_last_12
                FROM analytics.dbo.carr__all_transactions
                WHERE month_year BETWEEN '2025-08-31' AND '2027-07-31'
                GROUP BY 1
            ),
            cw_universe AS (
                SELECT
                    vf.vendor,
                    p.prod_sku,
                    pr.name AS product_name,
                    p.vendor AS cw_vendor,
                    p.sub_category,
                    c.mrr_summed_last_12,
                    UPPER(TRIM(p.prod_sku)) AS prod_sku_key
                FROM vendor_filters vf
                JOIN analytics.dbo_transformation.seed__product_categorization p
                  ON p.sub_category ILIKE vf.pattern OR p.vendor ILIKE vf.pattern
                LEFT JOIN carr c ON c.prod_sku = p.prod_sku
                LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__product pr
                  ON pr.product_code = p.prod_sku AND pr.is_deleted = FALSE
            ),
            mapped AS (
                SELECT DISTINCT vendor, UPPER(TRIM(cw_sku)) AS prod_sku_key
                FROM RECON_SKU_MAP
                WHERE cw_sku IS NOT NULL
            )
            SELECT
                u.vendor,
                u.prod_sku,
                u.product_name,
                u.cw_vendor,
                u.sub_category,
                ROUND(COALESCE(u.mrr_summed_last_12, 0) * 12, 2) AS annual_arr
            FROM cw_universe u
            LEFT JOIN mapped m
              ON m.vendor = u.vendor AND m.prod_sku_key = u.prod_sku_key
            WHERE m.prod_sku_key IS NULL
              AND COALESCE(u.mrr_summed_last_12, 0) > 0
            ORDER BY annual_arr DESC
            LIMIT 500
        """,
        "06_map_duplicate_health.csv": """
            WITH partner_dupes AS (
                SELECT
                    'partner_name_to_sf_id' AS check_name,
                    vendor,
                    UPPER(TRIM(partner_name)) AS key_value,
                    COUNT(*) AS row_count,
                    COUNT(DISTINCT sf_id) AS distinct_target_count
                FROM RECON_PARTNER_MAP
                WHERE partner_name IS NOT NULL
                GROUP BY 1, 2, 3
                HAVING COUNT(DISTINCT sf_id) > 1
            ),
            sku_dupes AS (
                SELECT
                    'cw_sku_to_sku_match_key' AS check_name,
                    vendor,
                    UPPER(TRIM(cw_sku)) AS key_value,
                    COUNT(*) AS row_count,
                    COUNT(DISTINCT sku_match_key) AS distinct_target_count
                FROM RECON_SKU_MAP
                WHERE cw_sku IS NOT NULL
                GROUP BY 1, 2, 3
                HAVING COUNT(DISTINCT sku_match_key) > 1
            )
            SELECT * FROM partner_dupes
            UNION ALL
            SELECT * FROM sku_dupes
            ORDER BY distinct_target_count DESC, row_count DESC
        """,
        "07_eset_usage_source_profile.csv": """
            SELECT
                billing_month,
                COUNT(*) AS row_count,
                COUNT(DISTINCT vendor_partner_name) AS distinct_partners,
                COUNT(DISTINCT vendor_product_sku) AS distinct_vendor_skus,
                ROUND(SUM(COALESCE(quantity, 0)), 2) AS total_quantity,
                ROUND(AVG(COALESCE(quantity, 0)), 2) AS avg_quantity,
                ROUND(MAX(COALESCE(quantity, 0)), 2) AS max_quantity
            FROM ESET_USAGE
            GROUP BY 1
            ORDER BY 1
        """,
        "08_eset_top_usage_rows.csv": """
            SELECT
                billing_month,
                vendor_partner_name,
                vendor_product_sku,
                ROUND(quantity, 2) AS quantity
            FROM ESET_USAGE
            ORDER BY quantity DESC NULLS LAST
            LIMIT 100
        """,
        "09_source_mix_qty_impact.csv": """
            SELECT
                vendor,
                billing_source_mix,
                exception_type,
                COUNT(*) AS row_count,
                ROUND(SUM(COALESCE(vendor_quantity, 0)), 2) AS vendor_qty,
                ROUND(SUM(COALESCE(total_billing_quantity, 0)), 2) AS cw_qty,
                ROUND(SUM(COALESCE(abs_qty_delta, 0)), 2) AS abs_qty_delta,
                ROUND(SUM(COALESCE(est_dollar_impact, 0)), 2) AS est_dollar_impact
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            GROUP BY 1, 2, 3
            ORDER BY vendor, abs_qty_delta DESC
        """,
    }

    for file_name, sql in audits.items():
        columns, rows = fetch(cur, sql)
        write_csv(file_name, columns, rows)


if __name__ == "__main__":
    main()
