from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
PROJECTS = Path(r"C:\Users\Nate.Fold\projects")
sys.path.insert(0, str(PROJECTS))

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


EXPECTED_PARTNER_ROWS = [
    ("KeepIT", "Kelley Connect dba Core Business Services, LLC", "", "ACT-00245191", "16624", "Kelley Create"),
    ("KeepIT", "ICS (TechMD)", "", "ACT-00203594", "", "Integris"),
    ("KeepIT", "Neuways Ltd", "", "ACT-00048607", "", "Zenzero Solutions Ltd"),
    ("KeepIT", "Ited Solution Inc", "", "ACT-00019966", "", "ited solutions inc"),
    ("KeepIT", "Techguides, Inc.", "", "ACT-00144806", "21092", ""),
    ("KeepIT", "First Solution Technologies Ltd", "", "ACT-00209449", "", "Emerge Digital Limited"),
    ("KeepIT", "Computerland of East Texas", "", "ACT-00002557", "", ""),
    ("KeepIT", "Cloud & More", "", "ACT-00253411", "", "Cloud & More"),
    ("KeepIT", "Technology Associates", "", "ACT-00218879", "", ""),
    ("KeepIT", "GoodSuite", "", "ACT-00134200", "", "GoodSuite"),
    ("KeepIT", "Richline Technical Services, LLC", "", "ACT-00011178", "20332", ""),
    ("KeepIT", "IT Kauai, Inc.", "", "ACT-00033691", "26895", ""),
    ("KeepIT", "ThreatAdvice Technologies", "", "ACT-00118650", "32122", "Magna5 MS"),
    ("KeepIT", "Advanced Networks", "", "ACT-00101925", "", "Advanced Networks"),
    ("KeepIT", "World Synergy", "", "ACT-00159769", "", "World Synergy"),
    ("KeepIT", "Abacus IT Inc", "", "ACT-00197019", "", ""),
    ("KeepIT", "Control Solutions, Inc", "", "ACT-00079030", "26361", ""),
    ("KeepIT", "Crowder Gulf", "", "ACT-00302313", "27834", ""),
    ("KeepIT", "Simplified IT Consulting", "", "ACT-00008759", "", "Simplified IT Consulting"),
    ("KeepIT", "Pickard Solutions", "", "ACT-00336886", "28401", ""),
    ("KeepIT", "Granite Information Technology, LLC", "", "ACT-00114156", "13113", ""),
    ("Auvik", "NetOps Consulting", "", "ACT-00124838", "", "NetOps Consulting, LLC"),
    ("Auvik", "Intrinsic Technology Group", "", "ACT-00053452", "", ""),
    ("Auvik", "International Computer Services (ICSI)", "", "ACT-00006517", "", ""),
    ("Auvik", "HighPoint Technology Group", "", "ACT-00153532", "", ""),
    ("Auvik", "Business Information Group", "", "ACT-00058011", "", ""),
    ("Auvik", "Tusker Technology", "", "ACT-00180484", "", ""),
    ("Auvik", "Bramatt Computing", "", "ACT-00108546", "", ""),
    ("Auvik", "Cinos", "", "ACT-00112875", "", ""),
    ("Auvik", "ISI", "", "ACT-00118822", "", ""),
    ("Auvik", "New England Network Solutions", "", "ACT-00107793", "", ""),
    ("Exium", "Sharp Europe", "", "ACT-00056702", "", "Sharp Electronics Europe"),
    ("Exium", "Entech", "", "ACT-00080802", "", "Entech Computer Services"),
]


EXPECTED_S1_SKU_ROWS = [
    ("SentinelOne", "Purple AI", "PR-AIAST-ND-T1-SA", "UNMAPPED", "PURPLE_AI"),
    ("SentinelOne", "WatchTower", "SS-WAT-ND-T2-SA", "UNMAPPED", "WATCHTOWER"),
    ("SentinelOne", "Ranger Insights", "SP-RGI-ND-T2-SA", "UNMAPPED", "RANGER_INSIGHTS"),
    ("SentinelOne", "Ranger AD", "SP-RAD-ND-T2-SA", "UNMAPPED", "RANGER_AD"),
]


USE_SQL = """
USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;
"""


def norm(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"none", "nan", "null"}:
        return ""
    return text


def query_df(conn, sql: str, params: tuple[object, ...] | None = None) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql, params or ())
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close()
    return pd.DataFrame(rows, columns=cols)


def execute_sql_file(conn, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    for statement in sql.split(";"):
        executable = re.sub(r"^\s*--.*$", "", statement, flags=re.MULTILINE).strip()
        if executable:
            conn.cursor().execute(statement)


def metrics(conn) -> dict[str, object]:
    overall = query_df(
        conn,
        """
        SELECT
            COUNT(*) AS row_count,
            COUNT_IF(exception_type = 'Clear') AS clear_rows,
            ROUND(COUNT_IF(exception_type = 'Clear') * 100.0 / NULLIF(COUNT(*), 0), 2) AS clear_pct,
            ROUND(SUM(ABS(COALESCE(qty_delta, 0))), 0) AS abs_qty_delta,
            ROUND(SUM(ABS(COALESCE(amount_delta, 0))), 2) AS abs_amount_delta
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
        """,
    )
    by_vendor = query_df(
        conn,
        """
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
        ORDER BY o.vendor
        """,
    )
    outcomes = query_df(
        conn,
        """
        SELECT
            exception_type,
            COUNT(*) AS row_count,
            ROUND(SUM(ABS(COALESCE(qty_delta, 0))), 0) AS abs_qty_delta,
            ROUND(SUM(ABS(COALESCE(amount_delta, 0))), 2) AS abs_amount_delta
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
        GROUP BY exception_type
        ORDER BY row_count DESC
        """,
    )
    return {
        "overall": overall.to_dict("records"),
        "by_vendor": by_vendor.to_dict("records"),
        "outcomes": outcomes.to_dict("records"),
    }


def seed_presence(path: Path, rows: list[tuple[str, str, str, str, str, str]]) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        existing = {
            (norm(r["VENDOR"]).upper(), norm(r["PARTNER_NAME"]).upper()): r
            for r in reader
        }
    result = []
    for vendor, partner, _, sf_id, cms_id, zuora in rows:
        found = existing.get((vendor.upper(), partner.upper()))
        result.append(
            {
                "vendor": vendor,
                "partner_name": partner,
                "seed_present": found is not None,
                "seed_sf_id": norm(found.get("SF_ID")) if found else "",
                "expected_sf_id": sf_id,
                "seed_cms_id": norm(found.get("CMS_ID")) if found else "",
                "expected_cms_id": cms_id,
                "seed_zuora_name": norm(found.get("ZUORA_NAME")) if found else "",
                "expected_zuora_name": zuora,
            }
        )
    return result


def upsert_partner(conn, row: tuple[str, str, str, str, str, str]) -> str:
    vendor, partner, parent, sf_id, cms_id, zuora = (norm(x) for x in row)
    current = query_df(
        conn,
        """
        SELECT partner_name, parent_company, sf_id, cms_id, zuora_name
        FROM THIRD_PARTY_RECON_PARTNER_MAP_PROD
        WHERE UPPER(partner_name) = UPPER(%s)
        """,
        (partner,),
    )
    if current.empty:
        conn.cursor().execute(
            """
            INSERT INTO THIRD_PARTY_RECON_PARTNER_MAP_PROD
                (partner_name, parent_company, sf_id, cms_id, zuora_name)
            SELECT %s, NULLIF(%s, ''), %s, NULLIF(%s, ''), NULLIF(%s, '')
            """,
            (partner, parent, sf_id, cms_id, zuora),
        )
        return "inserted"

    existing = current.iloc[0].to_dict()
    needs_update = (
        norm(existing.get("SF_ID")) != sf_id
        or (cms_id and norm(existing.get("CMS_ID")) != cms_id)
        or (zuora and norm(existing.get("ZUORA_NAME")) != zuora)
    )
    if needs_update:
        conn.cursor().execute(
            """
            UPDATE THIRD_PARTY_RECON_PARTNER_MAP_PROD
               SET parent_company = COALESCE(NULLIF(%s, ''), parent_company),
                   sf_id = %s,
                   cms_id = COALESCE(NULLIF(%s, ''), cms_id),
                   zuora_name = COALESCE(NULLIF(%s, ''), zuora_name)
             WHERE UPPER(partner_name) = UPPER(%s)
            """,
            (parent, sf_id, cms_id, zuora, partner),
        )
        return "updated"
    return "unchanged"


def upsert_s1_sku(conn, row: tuple[str, str, str, str, str]) -> str:
    vendor, product, vendor_sku, cw_sku, sku_key = (norm(x) for x in row)
    current = query_df(
        conn,
        """
        SELECT vendor, vendor_product, vendor_sku, cw_sku, sku_match_key
        FROM THIRD_PARTY_RECON_SKU_MAP_PROD
        WHERE UPPER(vendor) = UPPER(%s)
          AND (UPPER(vendor_product) = UPPER(%s) OR UPPER(vendor_sku) = UPPER(%s))
        """,
        (vendor, product, vendor_sku),
    )
    if current.empty:
        conn.cursor().execute(
            """
            INSERT INTO THIRD_PARTY_RECON_SKU_MAP_PROD
                (vendor, vendor_product, vendor_sku, cw_sku, sku_match_key, mapping_notes)
            SELECT %s, %s, %s, %s, %s,
                   'Catalog gap: vendor add-on has no CW rebill SKU; routed to Vendor SKU, No CW SKU'
            """,
            (vendor, product, vendor_sku, cw_sku, sku_key),
        )
        return "inserted"

    conn.cursor().execute(
        """
        UPDATE THIRD_PARTY_RECON_SKU_MAP_PROD
           SET cw_sku = %s,
               sku_match_key = %s,
               mapping_notes = COALESCE(mapping_notes, 'Catalog gap: vendor add-on has no CW rebill SKU')
         WHERE UPPER(vendor) = UPPER(%s)
           AND (UPPER(vendor_product) = UPPER(%s) OR UPPER(vendor_sku) = UPPER(%s))
           AND (COALESCE(cw_sku, '') <> %s OR COALESCE(sku_match_key, '') <> %s)
        """,
        (cw_sku, sku_key, vendor, product, vendor_sku, cw_sku, sku_key),
    )
    return "checked"


def main() -> int:
    out_dir = REPO / "outputs" / f"finding_map_validation_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)

    seed_file = REPO / "Maps" / "seeds" / "RECON_PARTNER_MAP.csv"
    seed_check = seed_presence(seed_file, EXPECTED_PARTNER_ROWS)

    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        conn.execute_string(USE_SQL)
        before = metrics(conn)

        partner_actions = []
        for row in EXPECTED_PARTNER_ROWS:
            partner_actions.append(
                {
                    "vendor": row[0],
                    "partner_name": row[1],
                    "sf_id": row[3],
                    "action": upsert_partner(conn, row),
                }
            )

        sku_actions = []
        for row in EXPECTED_S1_SKU_ROWS:
            sku_actions.append(
                {
                    "vendor": row[0],
                    "vendor_product": row[1],
                    "vendor_sku": row[2],
                    "sku_match_key": row[4],
                    "action": upsert_s1_sku(conn, row),
                }
            )

        execute_sql_file(conn, REPO / "Maps" / "sql" / "02_unified_reference_maps.sql")

        map_counts = query_df(
            conn,
            """
            SELECT 'THIRD_PARTY_RECON_PARTNER_MAP_PROD' AS table_name, COUNT(*) AS row_count FROM THIRD_PARTY_RECON_PARTNER_MAP_PROD
            UNION ALL SELECT 'RECON_PARTNER_MAP', COUNT(*) FROM RECON_PARTNER_MAP
            UNION ALL SELECT 'THIRD_PARTY_RECON_SKU_MAP_PROD', COUNT(*) FROM THIRD_PARTY_RECON_SKU_MAP_PROD
            UNION ALL SELECT 'RECON_SKU_MAP', COUNT(*) FROM RECON_SKU_MAP
            ORDER BY table_name
            """,
        )

        partner_check = query_df(
            conn,
            """
            WITH expected(vendor, partner_name, expected_sf_id) AS (
                SELECT * FROM VALUES
            """
            + ",\n".join(["(%s, %s, %s)"] * len(EXPECTED_PARTNER_ROWS))
            + """
            )
            SELECT
                e.vendor,
                e.partner_name,
                e.expected_sf_id,
                p.sf_id AS prod_sf_id,
                r.sf_id AS runtime_sf_id,
                IFF(p.sf_id = e.expected_sf_id AND r.sf_id = e.expected_sf_id, 'PASS', 'FAIL') AS status
            FROM expected e
            LEFT JOIN THIRD_PARTY_RECON_PARTNER_MAP_PROD p
              ON UPPER(p.partner_name) = UPPER(e.partner_name)
            LEFT JOIN RECON_PARTNER_MAP r
              ON UPPER(r.partner_name) = UPPER(e.partner_name)
            ORDER BY status, vendor, partner_name
            """,
            tuple(item for row in EXPECTED_PARTNER_ROWS for item in (row[0], row[1], row[3])),
        )

        sku_check = query_df(
            conn,
            """
            SELECT vendor, vendor_product, vendor_sku, cw_sku, sku_match_key
            FROM RECON_SKU_MAP
            WHERE vendor = 'SentinelOne'
              AND sku_match_key IN ('PURPLE_AI', 'WATCHTOWER', 'RANGER_INSIGHTS', 'RANGER_AD')
            ORDER BY sku_match_key, vendor_product, vendor_sku
            """,
        )

        summary = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "before_metrics": before,
            "seed_check": seed_check,
            "partner_actions": partner_actions,
            "sku_actions": sku_actions,
            "map_counts": map_counts.to_dict("records"),
            "partner_check": partner_check.to_dict("records"),
            "sku_check": sku_check.to_dict("records"),
        }
        (out_dir / "validation_before_rerun.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        pd.DataFrame(seed_check).to_csv(out_dir / "seed_partner_presence.csv", index=False)
        pd.DataFrame(partner_actions).to_csv(out_dir / "partner_actions.csv", index=False)
        pd.DataFrame(sku_actions).to_csv(out_dir / "sku_actions.csv", index=False)
        map_counts.to_csv(out_dir / "map_counts.csv", index=False)
        partner_check.to_csv(out_dir / "partner_runtime_check.csv", index=False)
        sku_check.to_csv(out_dir / "s1_sku_runtime_check.csv", index=False)
        print(out_dir)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
