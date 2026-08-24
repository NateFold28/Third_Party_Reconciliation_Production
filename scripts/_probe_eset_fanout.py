"""Probe ESET quantity fanout between usage, recon detail, and output."""
from __future__ import annotations

import sys

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


QUERIES = {
    "columns": "DESC TABLE ESET_RECON_DETAIL",
    "quantity_path": """
        SELECT 'usage' AS source_name, COUNT(1) AS row_count, SUM(quantity) AS qty
        FROM ESET_USAGE
        UNION ALL
        SELECT 'recon_detail' AS source_name, COUNT(1) AS row_count, SUM(vendor_quantity) AS qty
        FROM ESET_RECON_DETAIL
        UNION ALL
        SELECT 'output_prod' AS source_name, COUNT(1) AS row_count, SUM(vendor_quantity) AS qty
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE vendor = 'ESET'
    """,
    "recon_dupe_keys": """
        SELECT
            billing_month,
            sf_id,
            vendor_product,
            cw_skus,
            zuora_skus,
            marketplace_skus,
            COUNT(1) AS row_count,
            SUM(vendor_quantity) AS vendor_qty,
            SUM(total_billing_quantity) AS cw_qty
        FROM ESET_RECON_DETAIL
        GROUP BY 1, 2, 3, 4, 5, 6
        HAVING COUNT(1) > 1
        ORDER BY row_count DESC, vendor_qty DESC
        LIMIT 30
    """,
    "sample_repeated_rows": """
        SELECT
            billing_month,
            sf_id,
            vendor_partner_name,
            vendor_product,
            cw_skus,
            zuora_skus,
            marketplace_skus,
            billing_source_mix,
            vendor_quantity,
            total_billing_quantity,
            qty_delta,
            outcome_flag
        FROM ESET_RECON_DETAIL
        WHERE billing_month = '2026-01-01'
          AND sf_id = 'ACT-00235750'
          AND vendor_product = 'MSP - Endpoint Antivirus'
        ORDER BY cw_skus, zuora_skus, marketplace_skus
        LIMIT 20
    """,
    "billing_key_rows": """
        SELECT
            'zuora' AS source_name,
            billing_month,
            sf_id,
            sku_match_group,
            COUNT(1) AS row_count,
            SUM(zuora_quantity) AS qty,
            SUM(zuora_charge_amount) AS amount
        FROM ESET_BILLING_MATCHED
        GROUP BY 1, 2, 3, 4
        HAVING COUNT(1) > 1
        UNION ALL
        SELECT
            'marketplace' AS source_name,
            billing_month,
            sf_id,
            sku_match_group,
            COUNT(1) AS row_count,
            SUM(marketplace_quantity) AS qty,
            SUM(marketplace_amount) AS amount
        FROM ESET_MARKETPLACE_BILLING_MATCHED
        GROUP BY 1, 2, 3, 4
        HAVING COUNT(1) > 1
        ORDER BY row_count DESC
        LIMIT 50
    """,
    "contract_rate_dupes": """
        SELECT
            sku_match_group,
            currency,
            valid_from,
            valid_to,
            COUNT(1) AS row_count,
            COUNT(DISTINCT contract_cost_rate) AS distinct_rates,
            MIN(contract_cost_rate) AS min_rate,
            MAX(contract_cost_rate) AS max_rate,
            LISTAGG(DISTINCT source_doc, ' | ')
                WITHIN GROUP (ORDER BY source_doc) AS source_docs
        FROM ESET_CONTRACT_RATES
        GROUP BY 1, 2, 3, 4
        ORDER BY row_count DESC
        LIMIT 30
    """,
}


def main() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    cur = conn.cursor()
    for name, sql in QUERIES.items():
        print(f"\n--- {name} ---")
        cur.execute(sql)
        print([d[0] for d in cur.description])
        for row in cur.fetchall():
            print(row)


if __name__ == "__main__":
    main()
