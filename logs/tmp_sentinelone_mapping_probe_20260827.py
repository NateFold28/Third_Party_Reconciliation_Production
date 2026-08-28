from __future__ import annotations

from pathlib import Path
from textwrap import dedent
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from TEMPLATES.Python.connection import fetch_dataframe

TARGET_NAMES = [
    "ELEVITYIT",
    "ELEVITY IT",
    "NUMSP",
    "SFY",
    "EXECUTECH",
    "KMICRO",
    "GFLEX",
    "ACCESS GROUP INC",
]


def _sql_list(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def main() -> None:
    names_sql = _sql_list(TARGET_NAMES)

    print("=== SentinelOne baseline clear-rate (before) ===")
    q_before = dedent(
        """
        SELECT
            BILLING_MONTH::DATE AS BILLING_MONTH,
            COUNT(*) AS TOTAL_ROWS,
            COUNT_IF(EXCEPTION_TYPE = 'Clear') AS CLEAR_ROWS,
            ROUND(100.0 * COUNT_IF(EXCEPTION_TYPE = 'Clear') / NULLIF(COUNT(*), 0), 2) AS CLEAR_PCT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE VENDOR = 'SentinelOne'
        GROUP BY 1
        ORDER BY 1
        """
    )
    print(fetch_dataframe(q_before).to_string(index=False))

    print("\n=== SentinelOne baseline clear-rate overall (before) ===")
    q_before_all = dedent(
        """
        SELECT
            COUNT(*) AS TOTAL_ROWS,
            COUNT_IF(EXCEPTION_TYPE = 'Clear') AS CLEAR_ROWS,
            ROUND(100.0 * COUNT_IF(EXCEPTION_TYPE = 'Clear') / NULLIF(COUNT(*), 0), 2) AS CLEAR_PCT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE VENDOR = 'SentinelOne'
        """
    )
    print(fetch_dataframe(q_before_all).to_string(index=False))

    print("\n=== Current central partner map rows for target names ===")
    q_cols = dedent(
        """
        SELECT COLUMN_NAME
        FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'DBT_NFOLD_TRANSFORMATION'
          AND TABLE_NAME = 'THIRD_PARTY_RECON_PARTNER_MAP_PROD'
        ORDER BY ORDINAL_POSITION
        """
    )
    print("Partner map columns:")
    print(fetch_dataframe(q_cols).to_string(index=False))

    q_map = dedent(
        f"""
        SELECT
            PARTNER_NAME,
            SF_ID,
            ZUORA_NAME,
            CMS_ID
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_PARTNER_MAP_PROD
        WHERE UPPER(TRIM(PARTNER_NAME)) IN ({names_sql})
        ORDER BY PARTNER_NAME
        """
    )
    print(fetch_dataframe(q_map).to_string(index=False))

    print("\n=== SentinelOne output rows for target partner names (latest months) ===")
    q_out_cols = dedent(
        """
        SELECT COLUMN_NAME
        FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'DBT_NFOLD_TRANSFORMATION'
          AND TABLE_NAME = 'THIRD_PARTY_RECON_OUTPUT_PROD'
        ORDER BY ORDINAL_POSITION
        """
    )
    print("Output columns:")
    print(fetch_dataframe(q_out_cols).to_string(index=False))

    q_out = dedent(
        f"""
        SELECT
            BILLING_MONTH::DATE AS BILLING_MONTH,
            VENDOR_PARTNER_NAME,
            SF_ID,
            EXCEPTION_TYPE,
            OUTCOME_FLAG,
                        VENDOR_QUANTITY,
                        TOTAL_BILLING_QUANTITY
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE VENDOR = 'SentinelOne'
          AND UPPER(TRIM(VENDOR_PARTNER_NAME)) IN ({names_sql})
        ORDER BY BILLING_MONTH DESC, VENDOR_PARTNER_NAME
        LIMIT 300
        """
    )
    out_df = fetch_dataframe(q_out)
    if out_df.empty:
        print("No direct matches in output for exact partner_name list.")
    else:
        print(out_df.to_string(index=False))

    print("\n=== Fuzzy partner-name hits in central map (contains tokens) ===")
    q_fuzzy = dedent(
        """
        SELECT PARTNER_NAME, SF_ID, ZUORA_NAME
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_PARTNER_MAP_PROD
        WHERE REGEXP_LIKE(UPPER(PARTNER_NAME), 'ELEVITY|NUMSP|SFY|EXECUTECH|KMICRO|GFLEX|ACCESS\\s+GROUP')
        ORDER BY PARTNER_NAME
        """
    )
    print(fetch_dataframe(q_fuzzy).to_string(index=False))

    print("\n=== Resolver mapping for SFIDs tied to target names ===")
    q_resolver = dedent(
        f"""
        WITH target_sf AS (
            SELECT DISTINCT SF_ID
            FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_PARTNER_MAP_PROD
            WHERE REGEXP_LIKE(UPPER(PARTNER_NAME), 'ELEVITY|NUMSP|SFY|EXECUTECH|KMICRO|GFLEX|ACCESS\\s+GROUP')
        )
        SELECT r.*
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_ACCOUNT_MERGE_RESOLVER r
        JOIN target_sf t
          ON t.SF_ID = r.OLD_SF_ID
        ORDER BY r.OLD_SF_ID
        """
    )
    print(fetch_dataframe(q_resolver).to_string(index=False))

    print("\n=== SentinelOne usage names fuzzy hits (raw ingestion) ===")
    q_usage = dedent(
        """
        SELECT
            BILLING_MONTH::DATE AS BILLING_MONTH,
            VENDOR_PARTNER_NAME,
            SUM(QUANTITY) AS QTY
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.SENTINELONE_USAGE
        WHERE BILLING_MONTH >= '2026-01-01'
          AND REGEXP_LIKE(UPPER(VENDOR_PARTNER_NAME), 'ELEVITY|NUMSP|SFY|EXECUTECH|KMICRO|GFLEX|ACCESS\\s+GROUP')
        GROUP BY 1, 2
        ORDER BY 1 DESC, 2
        """
    )
    print(fetch_dataframe(q_usage).to_string(index=False))

    print("\n=== SentinelOne recon detail hits for these names (post-mapping) ===")
    q_detail = dedent(
        """
        SELECT
            BILLING_MONTH::DATE AS BILLING_MONTH,
            VENDOR_PARTNER_NAME,
            SF_ID,
            PARTNER_MATCH_METHODS,
            OUTCOME_FLAG,
            VENDOR_QUANTITY,
            TOTAL_BILLING_QUANTITY
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.SENTINELONE_RECON_DETAIL
        WHERE BILLING_MONTH >= '2026-01-01'
          AND REGEXP_LIKE(UPPER(VENDOR_PARTNER_NAME), 'ELEVITY|NUMSP|SFY|EXECUTECH|KMICRO|GFLEX|ACCESS\\s+GROUP')
        ORDER BY BILLING_MONTH DESC, VENDOR_PARTNER_NAME
        """
    )
    print(fetch_dataframe(q_detail).to_string(index=False))


if __name__ == "__main__":
    main()
