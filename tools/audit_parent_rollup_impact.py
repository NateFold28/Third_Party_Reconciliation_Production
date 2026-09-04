"""Focused checks for parent-rollup side effects and Secur-Serv remediation."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
import sys

import snowflake.connector

PROJECT_ROOT = Path(r"C:\Users\Nate.Fold\projects")
REPO = PROJECT_ROOT / "PROJECTS" / "Third_Party_Reconciliation" / "Combined_Recon_Prod_Pipeline"
sys.path.insert(0, str(PROJECT_ROOT))

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def fetch(cur: snowflake.connector.DictCursor, sql: str) -> list[dict]:
    cur.execute(sql)
    return [dict(row) for row in cur.fetchall()]


def main() -> int:
    out_dir = REPO / "output" / f"parent_rollup_impact_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        with conn.cursor(snowflake.connector.DictCursor) as cur:
            checks = {
                "secur_serv_map": """
                    SELECT partner_name, parent_company, raw_sf_id, sf_id, sf_id_source, cms_id, zuora_name
                    FROM RECON_PARTNER_MAP
                    WHERE UPPER(partner_name) IN ('SECUR-SERV INC.', 'SECUR-SERV INC', 'SECURSERV', 'SECUR-SERV')
                       OR raw_sf_id = 'ACT-00246623'
                    ORDER BY partner_name, raw_sf_id
                """,
                "secur_serv_output": """
                    SELECT billing_month, inv_id, sf_id, sf_id_original, vendor_partner_name,
                           product_display, exception_type, outcome_flag,
                           vendor_quantity, total_billing_quantity, qty_delta, abs_qty_delta,
                           vendor_amount, total_billing_amount, amount_delta, est_dollar_impact,
                           partner_display_name, is_aggregator_account, partner_alias_count
                    FROM THIRD_PARTY_RECON_OUTPUT_PROD
                    WHERE vendor = 'Auvik'
                      AND billing_month BETWEEN '2026-01-01' AND '2026-08-01'
                      AND (
                            sf_id = 'ACT-00246623'
                         OR sf_id_original = 'ACT-00246623'
                         OR UPPER(vendor_partner_name) LIKE '%SECUR-SERV%'
                      )
                    ORDER BY billing_month, product_display, exception_type, sf_id
                """,
                "auvik_multi_inv_summary": """
                    SELECT
                        COUNT(*) AS multi_inv_rows,
                        COUNT_IF(vendor_partner_name LIKE '% | %') AS pipe_partner_rows,
                        COUNT(DISTINCT sf_id) AS sf_ids,
                        SUM(abs_qty_delta) AS abs_qty_delta,
                        ROUND(SUM(est_dollar_impact), 2) AS est_dollar_impact
                    FROM THIRD_PARTY_RECON_OUTPUT_PROD
                    WHERE vendor = 'Auvik'
                      AND billing_month BETWEEN '2026-01-01' AND '2026-08-01'
                                            AND inv_id LIKE '% | %'
                """,
                "top_remaining_parent_like_rows": """
                    SELECT vendor, sf_id, partner_display_name, product_display, billing_month,
                           exception_type, inv_id, vendor_partner_name,
                           abs_qty_delta, est_dollar_impact, partner_alias_count
                    FROM THIRD_PARTY_RECON_OUTPUT_PROD
                    WHERE billing_month BETWEEN '2026-01-01' AND '2026-08-01'
                      AND (
                            inv_id LIKE '% | %'
                         OR vendor_partner_name LIKE '% | %'
                         OR partner_alias_count > 20
                      )
                      AND exception_type <> 'Clear'
                    ORDER BY est_dollar_impact DESC, abs_qty_delta DESC
                    LIMIT 100
                """,
                "resolver_source_counts": """
                    SELECT canonical_source, COUNT(*) AS row_count
                    FROM RECON_ACCOUNT_MERGE_RESOLVER
                    GROUP BY 1
                    ORDER BY row_count DESC
                """,
            }
            for name, sql in checks.items():
                rows = fetch(cur, sql)
                write_csv(out_dir / f"{name}.csv", rows)
                print(f"{name}: {len(rows)} rows")
                for row in rows[:12]:
                    print(row)
                if len(rows) > 12:
                    print(f"... {len(rows) - 12} more")
                print()
    finally:
        conn.close()
    print(f"Wrote audit files to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
