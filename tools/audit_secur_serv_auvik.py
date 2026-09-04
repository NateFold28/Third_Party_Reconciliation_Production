"""Audit the Secur-Serv Auvik parent-rollup issue."""

from __future__ import annotations

from pathlib import Path
import sys

import snowflake.connector

PROJECT_ROOT = Path(r"C:\Users\Nate.Fold\projects")
sys.path.insert(0, str(PROJECT_ROOT))

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


QUERIES = {
    "partner_map_secur_serv": """
        SELECT partner_name, parent_company, raw_sf_id, sf_id, sf_id_source, cms_id, zuora_name
        FROM RECON_PARTNER_MAP
        WHERE partner_name ILIKE '%secur%'
           OR raw_sf_id IN ('ACT-00246623', 'ACT-00059853')
           OR sf_id IN ('ACT-00246623', 'ACT-00059853')
        ORDER BY partner_name
    """,
    "resolver_pair": """
        SELECT old_sf_id, canonical_sf_id, canonical_source, merge_effective_month, resolver_depth
        FROM RECON_ACCOUNT_MERGE_RESOLVER
        WHERE old_sf_id IN ('ACT-00246623', 'ACT-00059853')
           OR canonical_sf_id IN ('ACT-00246623', 'ACT-00059853')
        ORDER BY old_sf_id
    """,
    "raw_auvik_usage": """
        SELECT billing_month, vendor_partner_name, modifier, vendor_product_sku,
               SUM(quantity) AS qty, SUM(amount) AS amount, COUNT(*) AS row_count
        FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
        WHERE vendor = 'Auvik'
          AND billing_month BETWEEN '2026-01-01' AND '2026-08-01'
          AND vendor_partner_name ILIKE '%secur%'
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 3, 4
    """,
    "current_output": """
        SELECT billing_month, inv_id, sf_id, sf_id_original, vendor_partner_name,
               product_display, exception_type, outcome_flag,
               vendor_quantity, total_billing_quantity, qty_delta, abs_qty_delta,
               vendor_amount, total_billing_amount, amount_delta, est_dollar_impact,
               partner_display_name, is_aggregator_account, partner_alias_count
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE vendor = 'Auvik'
          AND billing_month BETWEEN '2026-01-01' AND '2026-08-01'
          AND (
              vendor_partner_name ILIKE '%SECUR-SERV%'
              OR partner_display_name ILIKE '%Secur-Serv%'
              OR sf_id IN ('ACT-00246623', 'ACT-00059853')
          )
        ORDER BY billing_month, product_display, exception_type
    """,
}


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        with conn.cursor(snowflake.connector.DictCursor) as cur:
            for label, sql in QUERIES.items():
                print(f"\n=== {label} ===")
                cur.execute(sql)
                rows = [dict(r) for r in cur.fetchall()]
                print(f"rows={len(rows)}")
                for row in rows[:40]:
                    print(row)
                if len(rows) > 40:
                    print(f"... {len(rows) - 40} more")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
