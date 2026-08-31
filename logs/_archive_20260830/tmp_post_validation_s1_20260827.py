from __future__ import annotations

from pathlib import Path
import sys
from textwrap import dedent

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from TEMPLATES.Python.connection import fetch_dataframe

BEFORE_S1 = pd.DataFrame(
    [
        ("2026-01-01", 2526, 2089, 82.70),
        ("2026-02-01", 2578, 2103, 81.57),
        ("2026-03-01", 2575, 2102, 81.63),
        ("2026-04-01", 2597, 2101, 80.90),
        ("2026-05-01", 2633, 2120, 80.52),
        ("2026-06-01", 2626, 2110, 80.35),
        ("2026-07-01", 2534, 16, 0.63),
    ],
    columns=["BILLING_MONTH", "TOTAL_ROWS_BEFORE", "CLEAR_ROWS_BEFORE", "CLEAR_PCT_BEFORE"],
)
BEFORE_S1["BILLING_MONTH"] = pd.to_datetime(BEFORE_S1["BILLING_MONTH"])

BEFORE_VENDOR = pd.DataFrame(
    [
        ("Acronis", 81.15, 15722),
        ("Auvik", 66.56, 1961),
        ("Bitdefender", 91.70, 3073),
        ("ESET", 78.83, 2000),
        ("Exium", 78.81, 554),
        ("KeepIT", 49.43, 3412),
        ("Proofpoint", 94.90, 4764),
        ("SentinelOne", 69.96, 12641),
        ("Webroot", 54.11, 7419),
    ],
    columns=["VENDOR", "CLEAR_PCT_BEFORE", "CLEAR_ROWS_BEFORE"],
)


def main() -> None:
    q_s1_after = dedent(
        """
        SELECT
            BILLING_MONTH::DATE AS BILLING_MONTH,
            COUNT(*) AS TOTAL_ROWS_AFTER,
            COUNT_IF(EXCEPTION_TYPE = 'Clear') AS CLEAR_ROWS_AFTER,
            ROUND(100.0 * COUNT_IF(EXCEPTION_TYPE = 'Clear') / NULLIF(COUNT(*), 0), 2) AS CLEAR_PCT_AFTER
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE VENDOR = 'SentinelOne'
        GROUP BY 1
        ORDER BY 1
        """
    )
    s1_after = fetch_dataframe(q_s1_after)
    s1_after["BILLING_MONTH"] = pd.to_datetime(s1_after["BILLING_MONTH"])
    s1_after["CLEAR_PCT_AFTER"] = pd.to_numeric(s1_after["CLEAR_PCT_AFTER"], errors="coerce")
    s1_after["CLEAR_ROWS_AFTER"] = pd.to_numeric(s1_after["CLEAR_ROWS_AFTER"], errors="coerce")

    print("=== SentinelOne clear by month (after) ===")
    print(s1_after.to_string(index=False))

    s1_delta = BEFORE_S1.merge(s1_after, on="BILLING_MONTH", how="outer").fillna(0)
    s1_delta["CLEAR_PCT_BEFORE"] = pd.to_numeric(s1_delta["CLEAR_PCT_BEFORE"], errors="coerce")
    s1_delta["CLEAR_PCT_AFTER"] = pd.to_numeric(s1_delta["CLEAR_PCT_AFTER"], errors="coerce")
    s1_delta["CLEAR_ROWS_BEFORE"] = pd.to_numeric(s1_delta["CLEAR_ROWS_BEFORE"], errors="coerce")
    s1_delta["CLEAR_ROWS_AFTER"] = pd.to_numeric(s1_delta["CLEAR_ROWS_AFTER"], errors="coerce")
    s1_delta["CLEAR_ROWS_DELTA"] = s1_delta["CLEAR_ROWS_AFTER"] - s1_delta["CLEAR_ROWS_BEFORE"]
    s1_delta["CLEAR_PCT_DELTA"] = (s1_delta["CLEAR_PCT_AFTER"] - s1_delta["CLEAR_PCT_BEFORE"]).round(2)
    print("\n=== SentinelOne before/after delta by month ===")
    print(
        s1_delta[
            [
                "BILLING_MONTH",
                "CLEAR_ROWS_BEFORE",
                "CLEAR_ROWS_AFTER",
                "CLEAR_ROWS_DELTA",
                "CLEAR_PCT_BEFORE",
                "CLEAR_PCT_AFTER",
                "CLEAR_PCT_DELTA",
            ]
        ].to_string(index=False)
    )

    q_s1_overall = dedent(
        """
        SELECT
            COUNT(*) AS TOTAL_ROWS,
            COUNT_IF(EXCEPTION_TYPE = 'Clear') AS CLEAR_ROWS,
            ROUND(100.0 * COUNT_IF(EXCEPTION_TYPE = 'Clear') / NULLIF(COUNT(*), 0), 2) AS CLEAR_PCT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE VENDOR = 'SentinelOne'
        """
    )
    print("\n=== SentinelOne overall (after) ===")
    print(fetch_dataframe(q_s1_overall).to_string(index=False))

    q_integrity_1 = dedent(
        """
        SELECT COUNT(*) AS partner_name_conflicts
        FROM (
            SELECT UPPER(TRIM(PARTNER_NAME)) AS partner_name_norm
            FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
            GROUP BY 1
            HAVING COUNT(DISTINCT SF_ID) > 1
               OR COUNT(DISTINCT COALESCE(ZUORA_NAME, '')) > 1
        )
        """
    )
    q_integrity_2 = dedent(
        """
        SELECT COUNT(*) AS monthly_partner_name_conflicts
        FROM (
            SELECT BILLING_MONTH::DATE AS BILLING_MONTH, UPPER(TRIM(PARTNER_NAME)) AS partner_name_norm
            FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP_MONTHLY
            GROUP BY 1, 2
            HAVING COUNT(DISTINCT SF_ID) > 1
               OR COUNT(DISTINCT COALESCE(ZUORA_NAME, '')) > 1
        )
        """
    )
    print("\n=== Mapping integrity checks ===")
    print(fetch_dataframe(q_integrity_1).to_string(index=False))
    print(fetch_dataframe(q_integrity_2).to_string(index=False))

    q_resolver = dedent(
        """
        SELECT OLD_SF_ID, CANONICAL_SF_ID, MERGE_EFFECTIVE_MONTH, CANONICAL_SOURCE
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_ACCOUNT_MERGE_RESOLVER
        WHERE OLD_SF_ID IN (
            'ACT-00238028', 'ACT-00245551', 'ACT-00035427',
            'ACT-00246790', 'ACT-00246783', 'ACT-00245462', 'ACT-00200001'
        )
        ORDER BY OLD_SF_ID
        """
    )
    print("\n=== Resolver rows for provided SFIDs ===")
    print(fetch_dataframe(q_resolver).to_string(index=False))

    q_vendor_after = dedent(
        """
        SELECT
            VENDOR,
            COUNT_IF(EXCEPTION_TYPE = 'Clear') AS CLEAR_ROWS_AFTER,
            ROUND(100.0 * COUNT_IF(EXCEPTION_TYPE = 'Clear') / NULLIF(COUNT(*), 0), 2) AS CLEAR_PCT_AFTER
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        GROUP BY 1
        ORDER BY 1
        """
    )
    vendor_after = fetch_dataframe(q_vendor_after)
    vendor_after["CLEAR_PCT_AFTER"] = pd.to_numeric(vendor_after["CLEAR_PCT_AFTER"], errors="coerce")
    vendor_after["CLEAR_ROWS_AFTER"] = pd.to_numeric(vendor_after["CLEAR_ROWS_AFTER"], errors="coerce")
    vendor_delta = BEFORE_VENDOR.merge(vendor_after, on="VENDOR", how="outer").fillna(0)
    vendor_delta["CLEAR_PCT_BEFORE"] = pd.to_numeric(vendor_delta["CLEAR_PCT_BEFORE"], errors="coerce")
    vendor_delta["CLEAR_ROWS_BEFORE"] = pd.to_numeric(vendor_delta["CLEAR_ROWS_BEFORE"], errors="coerce")
    vendor_delta["CLEAR_ROWS_DELTA"] = vendor_delta["CLEAR_ROWS_AFTER"] - vendor_delta["CLEAR_ROWS_BEFORE"]
    vendor_delta["CLEAR_PCT_DELTA"] = (vendor_delta["CLEAR_PCT_AFTER"] - vendor_delta["CLEAR_PCT_BEFORE"]).round(2)

    print("\n=== High-level vendor regression check (before vs after) ===")
    print(vendor_delta.to_string(index=False))

    q_names = dedent(
        """
        SELECT PARTNER_NAME, SF_ID, ZUORA_NAME
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_PARTNER_MAP_PROD
        WHERE UPPER(TRIM(PARTNER_NAME)) IN (
            'ELEVITYIT', 'ELEVITY IT', 'NUMSP', 'NU MSP', 'SFY', 'SFY IT',
            'EXECUTECH', 'KMICRO', 'GFLEX', 'ACCESS GROUP INC'
        )
        ORDER BY PARTNER_NAME
        """
    )
    print("\n=== Central partner map rows for requested names ===")
    print(fetch_dataframe(q_names).to_string(index=False))


if __name__ == "__main__":
    main()
