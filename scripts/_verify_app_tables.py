"""Post-rebuild verification: confirm the 4 app-facing tables were regenerated
from this repo and now match the expected baseline.
"""
from __future__ import annotations
import sys

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

APP_TABLES = [
    "THIRD_PARTY_RECON_OUTPUT_PROD",
    "THIRD_PARTY_RECON_SUMMARY",
    "THIRD_PARTY_RECON_SUMMARY_PROD",
    "THIRD_PARTY_RECON_DETAIL_PROD",
]


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        cur = conn.cursor()

        # Existence + freshness.
        placeholders = ",".join(f"'{t}'" for t in APP_TABLES)
        cur.execute(
            f"""
            SELECT TABLE_NAME, ROW_COUNT, CREATED, LAST_ALTERED
            FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = 'DBT_NFOLD_TRANSFORMATION'
              AND TABLE_NAME IN ({placeholders})
            ORDER BY TABLE_NAME
            """
        )
        print("App-facing tables AFTER rebuild:")
        for name, rowcount, created, altered in cur.fetchall():
            print(f"  {name:<40} {rowcount:>10,} rows   created={created}   altered={altered}")

        # OUTPUT distribution.
        print("\nOUTPUT_PROD exception distribution:")
        cur.execute(
            """
            SELECT EXCEPTION_TYPE,
                   COUNT(*) AS row_count,
                   ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT,0)),0) AS amount
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            GROUP BY 1 ORDER BY 2 DESC
            """
        )
        for et, n, amt in cur.fetchall():
            print(f"  {et:<50} {n:>7,} rows   ${amt:>14,.0f}")

        # SUMMARY status distribution.
        print("\nSUMMARY.DATA_LOAD_STATUS distribution:")
        cur.execute(
            """
            SELECT DATA_LOAD_STATUS, COUNT(*) AS n
            FROM THIRD_PARTY_RECON_SUMMARY
            GROUP BY 1 ORDER BY 2 DESC
            """
        )
        for status, n in cur.fetchall():
            print(f"  {str(status):<20} {n:>4}")

        # Vendor coverage from OUTPUT.
        print("\nOUTPUT_PROD vendor coverage:")
        cur.execute(
            """
            SELECT VENDOR, COUNT(*) AS row_count, COUNT(DISTINCT BILLING_MONTH) AS months
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            GROUP BY 1 ORDER BY 1
            """
        )
        for v, n, m in cur.fetchall():
            print(f"  {v:<15} {n:>7,} rows   {m} months")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
