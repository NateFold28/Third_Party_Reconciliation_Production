from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from TEMPLATES.Python.connection import get_snowflake_connection


def fetchall(cur, sql: str):
    cur.execute(sql)
    return cur.fetchall()


def main() -> None:
    conn = get_snowflake_connection(
        warehouse="REPORTING_WH",
        database="STREAMLIT_APPS",
        schema="DBO",
        role="STREAMLIT_USER",
    )
    cur = conn.cursor()
    cur.execute("USE DATABASE STREAMLIT_APPS")
    cur.execute("USE SCHEMA DBO")

    print("OBJECT_STATUS")
    rows = fetchall(
        cur,
        """
        SELECT TABLE_NAME, ROW_COUNT, LAST_ALTERED
        FROM STREAMLIT_APPS.INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = 'DBO'
          AND TABLE_NAME IN (
            'V5_SANDBOX_APP_CONTRACT_DETAIL',
            'V5_SANDBOX_APP_BACKTEST',
            'V5_APP_CONTRACT_DETAIL',
            'V5_APP_BACKTEST'
          )
        ORDER BY TABLE_NAME
        """,
    )
    for row in rows:
        print(f"{row[0]} | rows={row[1]} | last_altered={row[2]}")

    print("\nLIVE_TABLE_SMOKE")
    for table_name in (
        "V5_SANDBOX_APP_CONTRACT_DETAIL",
        "V5_SANDBOX_APP_BACKTEST",
    ):
        rows = fetchall(
            cur,
            f"""
            SELECT
                '{table_name}' AS TABLE_NAME,
                COUNT(*) AS N_ROWS,
                MAX(RENEWAL_MONTH) AS MAX_RENEWAL_MONTH
            FROM STREAMLIT_APPS.DBO.{table_name}
            """,
        )
        for row in rows:
            print(f"{row[0]} | rows={row[1]} | max_renewal_month={row[2]}")

    print("\nAPP_CARR_PARITY_H0_H5")
    rows = fetchall(
        cur,
        """
        WITH carr AS (
            SELECT SUM(COALESCE(ADJ_ATR_C_BUDGET_RATE, 0)) AS CARR_ATR
            FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
            WHERE INCLUDE_FLAG_C = 1
              AND DATE_TRUNC('MONTH', MASTER_DATE) >= DATE_TRUNC('MONTH', CURRENT_DATE())
              AND DATE_TRUNC('MONTH', MASTER_DATE) <= DATEADD('MONTH', 5, DATE_TRUNC('MONTH', CURRENT_DATE()))
        ),
        app AS (
            SELECT
                SUM(ATR) AS APP_ATR,
                SUM(RENEWAL_FORECAST) AS APP_FORECAST,
                SUM(RENEWAL_FORECAST) / NULLIF(SUM(ATR), 0) AS APP_RATE
            FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
            WHERE RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
              AND RENEWAL_MONTH <= DATEADD('MONTH', 5, DATE_TRUNC('MONTH', CURRENT_DATE()))
        )
        SELECT
            ROUND(c.CARR_ATR / 1e6, 2) AS CARR_ATR_M,
            ROUND(a.APP_ATR / 1e6, 2) AS APP_ATR_M,
            ROUND(a.APP_FORECAST / 1e6, 2) AS APP_FORECAST_M,
            ROUND(a.APP_RATE * 100, 2) AS APP_RATE_PCT,
            ROUND((a.APP_ATR / NULLIF(c.CARR_ATR, 0) - 1) * 100, 4) AS ATR_DIFF_PCT
        FROM carr c
        CROSS JOIN app a
        """,
    )
    for row in rows:
        print(
            "carr_atr_m={} | app_atr_m={} | app_forecast_m={} | "
            "app_rate_pct={} | atr_diff_pct={}".format(*row)
        )

    print("\nRECENT_OLD_OBJECT_REFERENCES")
    query_history_sql = """
    SELECT START_TIME, EXECUTION_STATUS, ERROR_MESSAGE, QUERY_TEXT
    FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(
        END_TIME_RANGE_START=>DATEADD('hour', -12, CURRENT_TIMESTAMP()),
        END_TIME_RANGE_END=>CURRENT_TIMESTAMP(),
        RESULT_LIMIT=>200
    ))
    WHERE ERROR_MESSAGE ILIKE '%V5_APP_CONTRACT_DETAIL%'
       OR QUERY_TEXT ILIKE '%V5_APP_CONTRACT_DETAIL%'
    ORDER BY START_TIME DESC
    """
    try:
        rows = fetchall(cur, query_history_sql)
    except Exception as exc:  # noqa: BLE001
        print(f"query_history_unavailable: {exc}")
        return

    print(f"rows={len(rows)}")
    for row in rows[:25]:
        query_text = " ".join(str(row[3]).split())[:500]
        print(f"{row[0]} | {row[1]} | {row[2]} | {query_text}")


if __name__ == "__main__":
    main()
