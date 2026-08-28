from TEMPLATES.Python.connection import get_snowflake_connection


def main() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )

    queries = {
        "usage_by_vendor": (
            "SELECT VENDOR, COUNT(*) AS ROW_COUNT, MIN(BILLING_MONTH) AS MIN_M, MAX(BILLING_MONTH) AS MAX_M "
            "FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD "
            "GROUP BY 1 ORDER BY 1"
        ),
        "zuora_by_vendor": (
            "SELECT VENDOR, COUNT(*) AS ROW_COUNT, MIN(BILLING_MONTH) AS MIN_M, MAX(BILLING_MONTH) AS MAX_M "
            "FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD "
            "GROUP BY 1 ORDER BY 1"
        ),
        "marketplace_by_vendor": (
            "SELECT VENDOR, COUNT(*) AS ROW_COUNT, MIN(BILLING_MONTH) AS MIN_M, MAX(BILLING_MONTH) AS MAX_M "
            "FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD "
            "GROUP BY 1 ORDER BY 1"
        ),
        "detail_by_vendor": (
            "SELECT VENDOR, COUNT(*) AS ROW_COUNT, MIN(BILLING_MONTH) AS MIN_M, MAX(BILLING_MONTH) AS MAX_M "
            "FROM THIRD_PARTY_RECON_DETAIL_PROD "
            "GROUP BY 1 ORDER BY 1"
        ),
        "proofpoint_usage_months": (
            "SELECT BILLING_MONTH, COUNT(*) AS ROW_COUNT, SUM(QUANTITY) AS QTY, SUM(AMOUNT) AS AMT "
            "FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD "
            "WHERE VENDOR = 'Proofpoint' "
            "GROUP BY 1 ORDER BY 1"
        ),
        "proofpoint_zuora_months": (
            "SELECT BILLING_MONTH, COUNT(*) AS ROW_COUNT, SUM(QTY) AS QTY, SUM(CHARGE_AMOUNT_USD) AS AMT "
            "FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD "
            "WHERE VENDOR = 'Proofpoint' "
            "GROUP BY 1 ORDER BY 1"
        ),
    }

    try:
        with conn.cursor() as cur:
            for name, query in queries.items():
                print(f"\n=== {name} ===")
                cur.execute(query)
                rows = cur.fetchall()
                print(f"row_count: {len(rows)}")
                for row in rows:
                    print(row)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
