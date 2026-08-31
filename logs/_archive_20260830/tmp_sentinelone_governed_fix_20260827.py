from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from textwrap import dedent

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from TEMPLATES.Python.connection import fetch_dataframe, get_snowflake_connection

ROOT = Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline")
MAP_SQL = ROOT / "Maps" / "sql" / "02_unified_reference_maps.sql"
SKELETON = ROOT / "Reconciliation" / "_run_skeleton_pipeline.py"

PARTNER_UPSERT_ROWS = [
    ("ELEVITYIT", "ACT-00238028", "Elevity IT"),
    ("ELEVITY IT", "ACT-00238028", "Elevity IT"),
    ("NUMSP", "ACT-00245551", "NuMSP"),
    ("SFY", "ACT-00035427", "Sfy It"),
    ("SFY IT", "ACT-00035427", "Sfy It"),
    ("EXECUTECH", "ACT-00246790", "Executech"),
    ("KMICRO", "ACT-00246783", "KMicro"),
    ("GFLEX", "ACT-00245462", "Gflex"),
    ("ACCESS GROUP INC", "ACT-00200001", "Access Group Inc"),
]


def before_after_clear_query() -> str:
    return dedent(
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


def vendor_clear_query() -> str:
    return dedent(
        """
        SELECT
            VENDOR,
            COUNT(*) AS TOTAL_ROWS,
            COUNT_IF(EXCEPTION_TYPE = 'Clear') AS CLEAR_ROWS,
            ROUND(100.0 * COUNT_IF(EXCEPTION_TYPE = 'Clear') / NULLIF(COUNT(*), 0), 2) AS CLEAR_PCT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        GROUP BY 1
        ORDER BY 1
        """
    )


def upsert_partner_rows(conn) -> None:
    values_sql = ",\n        ".join(
        f"('{pn.replace("'", "''")}', '{sfid}', '{zn.replace("'", "''")}')"
        for pn, sfid, zn in PARTNER_UPSERT_ROWS
    )
    sql = dedent(
        f"""
        MERGE INTO ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_PARTNER_MAP_PROD t
        USING (
            SELECT *
            FROM VALUES
                {values_sql}
            v(partner_name, sf_id, zuora_name)
        ) s
        ON UPPER(TRIM(t.PARTNER_NAME)) = UPPER(TRIM(s.partner_name))
        WHEN MATCHED THEN UPDATE SET
            t.SF_ID = s.sf_id,
            t.ZUORA_NAME = COALESCE(NULLIF(t.ZUORA_NAME, ''), s.zuora_name)
        WHEN NOT MATCHED THEN INSERT (PARTNER_NAME, PARENT_COMPANY, SF_ID, CMS_ID, ZUORA_NAME)
        VALUES (s.partner_name, NULL, s.sf_id, NULL, s.zuora_name)
        """
    )
    with conn.cursor() as cur:
        cur.execute(sql)


def run_multi_statement_sql(conn, sql_text: str) -> None:
    filtered_sql = "\n".join(
        line for line in sql_text.splitlines()
        if not line.strip().startswith("--")
    )
    for cur in conn.execute_string(filtered_sql, return_cursors=True):
        try:
            cur.fetchall()
        except Exception:
            pass
    conn.commit()


def run_map_rebuild(conn) -> None:
    sql_text = MAP_SQL.read_text(encoding="utf-8")
    run_multi_statement_sql(conn, sql_text)


def mapping_integrity_checks() -> tuple[pd.DataFrame, pd.DataFrame]:
    q1 = dedent(
        """
        SELECT
            COUNT(*) AS partner_names_with_multi_sf_or_zuora
        FROM (
            SELECT
                UPPER(TRIM(PARTNER_NAME)) AS partner_name_norm,
                COUNT(DISTINCT SF_ID) AS sfid_cnt,
                COUNT(DISTINCT COALESCE(ZUORA_NAME, '')) AS zuora_cnt
            FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
            WHERE PARTNER_NAME IS NOT NULL
            GROUP BY 1
            HAVING COUNT(DISTINCT SF_ID) > 1
               OR COUNT(DISTINCT COALESCE(ZUORA_NAME, '')) > 1
        )
        """
    )
    q2 = dedent(
        """
        SELECT
            COUNT(*) AS monthly_partner_names_with_multi_sf_or_zuora
        FROM (
            SELECT
                BILLING_MONTH::DATE AS BILLING_MONTH,
                UPPER(TRIM(PARTNER_NAME)) AS partner_name_norm,
                COUNT(DISTINCT SF_ID) AS sfid_cnt,
                COUNT(DISTINCT COALESCE(ZUORA_NAME, '')) AS zuora_cnt
            FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP_MONTHLY
            WHERE PARTNER_NAME IS NOT NULL
            GROUP BY 1, 2
            HAVING COUNT(DISTINCT SF_ID) > 1
               OR COUNT(DISTINCT COALESCE(ZUORA_NAME, '')) > 1
        )
        """
    )
    return fetch_dataframe(q1), fetch_dataframe(q2)


def resolver_check() -> pd.DataFrame:
    q = dedent(
        """
        SELECT *
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_ACCOUNT_MERGE_RESOLVER
        WHERE OLD_SF_ID IN (
            'ACT-00238028', 'ACT-00245551', 'ACT-00246790',
            'ACT-00246783', 'ACT-00245462', 'ACT-00200001', 'ACT-00035427'
        )
        ORDER BY OLD_SF_ID
        """
    )
    return fetch_dataframe(q)


def main() -> int:
    print("=== BEFORE: SentinelOne clear by month ===")
    before_s1 = fetch_dataframe(before_after_clear_query())
    print(before_s1.to_string(index=False))

    print("\n=== BEFORE: all-vendor clear rates ===")
    before_vendor = fetch_dataframe(vendor_clear_query())
    print(before_vendor.to_string(index=False))

    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        print("\n=== APPLY: upsert central partner map rows ===")
        upsert_partner_rows(conn)
        conn.commit()
        print("Partner map upsert complete.")

        print("\n=== APPLY: rebuild shared reference maps (02_unified_reference_maps.sql) ===")
        run_map_rebuild(conn)
        print("Shared map rebuild complete.")
    finally:
        conn.close()

    print("\n=== APPLY: rebuild reconciliation output via skeleton pipeline ===")
    proc = subprocess.run(
        [sys.executable, str(SKELETON)],
        cwd=str(ROOT / "Reconciliation"),
        capture_output=True,
        text=True,
    )
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr)
        print(f"Skeleton pipeline failed with exit code {proc.returncode}")
        return proc.returncode

    print("\n=== AFTER: SentinelOne clear by month ===")
    after_s1 = fetch_dataframe(before_after_clear_query())
    print(after_s1.to_string(index=False))

    print("\n=== AFTER: SentinelOne clear delta (after - before) ===")
    delta = before_s1.merge(after_s1, on="BILLING_MONTH", how="outer", suffixes=("_BEFORE", "_AFTER")).fillna(0)
    delta["CLEAR_ROWS_DELTA"] = delta["CLEAR_ROWS_AFTER"] - delta["CLEAR_ROWS_BEFORE"]
    delta["CLEAR_PCT_DELTA"] = (delta["CLEAR_PCT_AFTER"] - delta["CLEAR_PCT_BEFORE"]).round(2)
    print(delta[["BILLING_MONTH", "CLEAR_ROWS_BEFORE", "CLEAR_ROWS_AFTER", "CLEAR_ROWS_DELTA", "CLEAR_PCT_BEFORE", "CLEAR_PCT_AFTER", "CLEAR_PCT_DELTA"]].to_string(index=False))

    print("\n=== AFTER: SentinelOne clear overall ===")
    q_all = dedent(
        """
        SELECT
            COUNT(*) AS TOTAL_ROWS,
            COUNT_IF(EXCEPTION_TYPE = 'Clear') AS CLEAR_ROWS,
            ROUND(100.0 * COUNT_IF(EXCEPTION_TYPE = 'Clear') / NULLIF(COUNT(*), 0), 2) AS CLEAR_PCT
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE VENDOR = 'SentinelOne'
        """
    )
    print(fetch_dataframe(q_all).to_string(index=False))

    print("\n=== VALIDATION: partner_name many-to-one integrity ===")
    integ1, integ2 = mapping_integrity_checks()
    print(integ1.to_string(index=False))
    print(integ2.to_string(index=False))

    print("\n=== VALIDATION: resolver rows for target SFIDs ===")
    print(resolver_check().to_string(index=False))

    print("\n=== AFTER: all-vendor clear rates and delta (high-level regression check) ===")
    after_vendor = fetch_dataframe(vendor_clear_query())
    merged = before_vendor.merge(after_vendor, on="VENDOR", how="outer", suffixes=("_BEFORE", "_AFTER")).fillna(0)
    merged["CLEAR_PCT_DELTA"] = (merged["CLEAR_PCT_AFTER"] - merged["CLEAR_PCT_BEFORE"]).round(2)
    merged["CLEAR_ROWS_DELTA"] = merged["CLEAR_ROWS_AFTER"] - merged["CLEAR_ROWS_BEFORE"]
    print(merged[["VENDOR", "CLEAR_PCT_BEFORE", "CLEAR_PCT_AFTER", "CLEAR_PCT_DELTA", "CLEAR_ROWS_BEFORE", "CLEAR_ROWS_AFTER", "CLEAR_ROWS_DELTA"]].to_string(index=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
