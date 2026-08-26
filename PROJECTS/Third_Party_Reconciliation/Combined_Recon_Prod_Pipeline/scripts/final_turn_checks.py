from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECTS = Path(r"C:\Users\Nate.Fold\projects")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECTS))

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


USE_SQL = """
USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;
"""


def query_df(conn, sql: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close()
    return pd.DataFrame(rows, columns=cols)


def main() -> int:
    out_dir = REPO / "outputs" / f"final_turn_checks_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        conn.execute_string(USE_SQL)
        checks = {
            "output_columns": """
                SELECT column_name, ordinal_position
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE table_schema = 'DBT_NFOLD_TRANSFORMATION'
                  AND table_name = 'THIRD_PARTY_RECON_OUTPUT_PROD'
                ORDER BY ordinal_position
            """,
            "duplicate_by_vendor": """
                SELECT vendor, COUNT(*) AS row_count,
                       ROUND(SUM(ABS(COALESCE(amount_delta, 0))), 2) AS abs_amount_delta
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                WHERE exception_type = 'Duplicated CW Invoice'
                GROUP BY vendor
                ORDER BY row_count DESC
            """,
            "keepit_carr_checks": """
                SELECT
                    COUNT_IF(vendor = 'KeepIT' AND COALESCE(billing_source_mix, '') ILIKE '%CARR%') AS keepit_carr_source_mix_rows,
                    COUNT_IF(vendor = 'KeepIT' AND exception_type = 'Duplicated CW Invoice') AS keepit_duplicate_rows,
                    COUNT_IF(vendor = 'KeepIT' AND COALESCE(marketplace_amount, 0) <> 0) AS keepit_marketplace_amount_rows,
                    ROUND(SUM(IFF(vendor = 'KeepIT', COALESCE(marketplace_amount, 0), 0)), 2) AS keepit_marketplace_amount
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
            """,
            "keepit_recon_native_flags": """
                SELECT outcome_flag, COUNT(*) AS row_count,
                       ROUND(SUM(ABS(COALESCE(abs_amount_delta, 0))), 2) AS abs_amount_delta
                FROM KEEPIT_RECON_DETAIL
                GROUP BY outcome_flag
                ORDER BY row_count DESC
            """,
            "map_counts": """
                SELECT 'THIRD_PARTY_RECON_PARTNER_MAP_PROD' AS table_name, COUNT(*) AS row_count FROM THIRD_PARTY_RECON_PARTNER_MAP_PROD
                UNION ALL SELECT 'RECON_PARTNER_MAP', COUNT(*) FROM RECON_PARTNER_MAP
                UNION ALL SELECT 'THIRD_PARTY_RECON_SKU_MAP_PROD', COUNT(*) FROM THIRD_PARTY_RECON_SKU_MAP_PROD
                UNION ALL SELECT 'RECON_SKU_MAP', COUNT(*) FROM RECON_SKU_MAP
                ORDER BY table_name
            """,
            "acronis_map_rows": """
                SELECT partner_name, sf_id, cms_id, zuora_name
                FROM RECON_PARTNER_MAP
                WHERE UPPER(partner_name) IN ('CONVERGENCE INFO-TECH', 'DPC INC', 'COMPUFIT, LLC.')
                ORDER BY partner_name
            """,
            "overall": """
                SELECT
                    COUNT(*) AS row_count,
                    COUNT_IF(exception_type = 'Clear') AS clear_rows,
                    ROUND(COUNT_IF(exception_type = 'Clear') * 100.0 / NULLIF(COUNT(*), 0), 2) AS clear_pct,
                    ROUND(SUM(ABS(COALESCE(qty_delta, 0))), 0) AS abs_qty_delta,
                    ROUND(SUM(ABS(COALESCE(amount_delta, 0))), 2) AS abs_amount_delta
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
            """,
            "vendor_metrics": """
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
            "outcomes": """
                SELECT exception_type, COUNT(*) AS row_count,
                       ROUND(SUM(ABS(COALESCE(qty_delta, 0))), 0) AS abs_qty_delta,
                       ROUND(SUM(ABS(COALESCE(amount_delta, 0))), 2) AS abs_amount_delta
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                GROUP BY exception_type
                ORDER BY row_count DESC
            """,
        }
        result = {}
        for name, sql in checks.items():
            frame = query_df(conn, sql)
            frame.to_csv(out_dir / f"{name}.csv", index=False)
            result[name] = frame.to_dict("records")
        (out_dir / "report.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(out_dir)
        print(json.dumps({
            "overall": result["overall"],
            "keepit_carr_checks": result["keepit_carr_checks"],
            "duplicate_by_vendor": result["duplicate_by_vendor"],
            "first_columns": result["output_columns"][:8],
        }, indent=2, default=str))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
