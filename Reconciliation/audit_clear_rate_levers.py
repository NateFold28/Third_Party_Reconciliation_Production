"""
Audit the highest-ROI levers for improving third-party recon clear rates.

The script reads the canonical production marts and writes timestamped CSVs
under output/clear_rate_levers_<timestamp>. It does not mutate Snowflake.

Business framing:
  - Clear-rate lift is useful only when it reflects real matching improvement.
  - Partner/SKU gaps are data/catalog blockers.
  - Billing gaps are Finance/Sales actions, not pipeline fixes.
  - Discount/bundle candidates are review queues; they are not auto-cleared.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable


def find_projects_root(start: Path) -> Path:
    for path in [start, *start.parents]:
        if (path / "TEMPLATES").exists():
            return path
    raise RuntimeError("Could not find projects root containing TEMPLATES")


REPO = Path(__file__).resolve().parents[1]
PROJECTS_ROOT = find_projects_root(Path(__file__).resolve())
sys.path.insert(0, str(PROJECTS_ROOT))

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


USE_CONTEXT = [
    "USE ROLE DEVELOPER",
    "USE WAREHOUSE REPORTING_WH",
    "USE DATABASE ANALYTICS_DEV",
    "USE SCHEMA DBT_NFOLD_TRANSFORMATION",
]


@dataclass(frozen=True)
class QueryExport:
    file_name: str
    sql: str


def normalize_name(value: object) -> str:
    text = "" if value is None else str(value).upper()
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    text = re.sub(
        r"\b(INC|INCORPORATED|LLC|L L C|LTD|LIMITED|CORP|CORPORATION|COMPANY|CO|"
        r"TECHNOLOGIES|TECHNOLOGY|SYSTEMS|SOLUTIONS|SERVICES|GROUP|THE)\b",
        " ",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


def compact_name(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", "" if value is None else str(value).upper())


def csv_value(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def write_rows(path: Path, columns: list[str], rows: Iterable[Iterable[object]]) -> int:
    count = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([csv_value(value) for value in row])
            count += 1
    return count


def fetch(conn, sql: str) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description]
        return cols, rows
    finally:
        cur.close()


def export_query(conn, output_dir: Path, spec: QueryExport) -> int:
    columns, rows = fetch(conn, spec.sql)
    return write_rows(output_dir / spec.file_name, columns, rows)


def build_alias_candidates(conn, output_dir: Path, limit: int) -> int:
    unmapped_sql = f"""
        SELECT
            VENDOR,
            VENDOR_PARTNER_NAME,
            COUNT(*) AS ROW_COUNT,
            COUNT(DISTINCT BILLING_MONTH) AS MONTH_COUNT,
            TO_CHAR(MIN(BILLING_MONTH), 'YYYY-MM') AS FIRST_MONTH,
            TO_CHAR(MAX(BILLING_MONTH), 'YYYY-MM') AS LAST_MONTH,
            ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
            ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
            ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ABS_AMOUNT_DELTA,
            ROUND(SUM(ABS(COALESCE(QTY_DELTA, 0))), 2) AS ABS_QTY_DELTA,
            COUNT(DISTINCT COALESCE(SKU_MATCH_GROUP, VENDOR_PRODUCT, '(missing)')) AS PRODUCT_GROUP_COUNT
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE EXCEPTION_TYPE = 'Unmapped Partner'
          AND VENDOR_PARTNER_NAME IS NOT NULL
        GROUP BY 1, 2
        ORDER BY ROW_COUNT DESC, ABS_AMOUNT_DELTA DESC
        LIMIT {int(limit)}
    """
    map_sql = """
        SELECT
            PARTNER_NAME,
            PARENT_COMPANY,
            SF_ID,
            CMS_ID,
            SF_ID_SOURCE
        FROM RECON_PARTNER_MAP
        WHERE PARTNER_NAME IS NOT NULL
          AND SF_ID IS NOT NULL
          AND UPPER(TRIM(SF_ID)) NOT IN ('', 'UNKNOWN', 'NONE', 'UNMAPPED', 'NULL')
    """
    _, unmapped = fetch(conn, unmapped_sql)
    _, mapped = fetch(conn, map_sql)

    mapped_records = []
    exact_compact: dict[str, list[tuple]] = {}
    exact_core: dict[str, list[tuple]] = {}
    first_char_index: dict[str, list[tuple]] = {}
    for row in mapped:
        partner_name = row[0]
        compact = compact_name(partner_name)
        core = normalize_name(partner_name)
        if not compact or not core:
            continue
        rec = (*row, compact, core)
        mapped_records.append(rec)
        exact_compact.setdefault(compact, []).append(rec)
        exact_core.setdefault(core, []).append(rec)
        first_char_index.setdefault(core[:1], []).append(rec)

    out_rows = []
    for row in unmapped:
        vendor_partner_name = row[1]
        compact = compact_name(vendor_partner_name)
        core = normalize_name(vendor_partner_name)
        candidate_pool: list[tuple] = []
        candidate_pool.extend(exact_compact.get(compact, []))
        candidate_pool.extend(exact_core.get(core, []))
        if not candidate_pool and core:
            candidate_pool = first_char_index.get(core[:1], [])

        scored = []
        seen = set()
        for rec in candidate_pool:
            key = (rec[0], rec[2], rec[3])
            if key in seen:
                continue
            seen.add(key)
            rec_compact = rec[5]
            rec_core = rec[6]
            if compact and rec_compact == compact:
                score = 1.0
                reason = "punctuation_case_exact"
            elif core and rec_core == core:
                score = 0.97
                reason = "suffix_normalized_exact"
            elif core and rec_core and (core in rec_core or rec_core in core):
                shorter = min(len(core), len(rec_core))
                score = 0.92 if shorter >= 6 else 0.0
                reason = "possible_parent_child_or_truncated_alias"
            else:
                score = SequenceMatcher(None, core, rec_core).ratio() if core and rec_core else 0.0
                reason = "fuzzy_name_similarity"
            if score >= 0.84:
                scored.append((score, reason, rec))

        scored.sort(key=lambda item: item[0], reverse=True)
        for score, reason, rec in scored[:3]:
            out_rows.append(
                (
                    *row,
                    normalize_name(vendor_partner_name),
                    rec[0],
                    rec[1],
                    rec[2],
                    rec[3],
                    rec[4],
                    round(score, 3),
                    reason,
                    "Review before mapping; parent/child billing level must be verified",
                )
            )

    columns = [
        "VENDOR",
        "VENDOR_PARTNER_NAME",
        "ROW_COUNT",
        "MONTH_COUNT",
        "FIRST_MONTH",
        "LAST_MONTH",
        "VENDOR_AMOUNT",
        "CW_BILLING_AMOUNT",
        "ABS_AMOUNT_DELTA",
        "ABS_QTY_DELTA",
        "PRODUCT_GROUP_COUNT",
        "NORMALIZED_VENDOR_PARTNER_NAME",
        "CANDIDATE_PARTNER_NAME",
        "CANDIDATE_PARENT_COMPANY",
        "CANDIDATE_SF_ID",
        "CANDIDATE_CMS_ID",
        "CANDIDATE_SOURCE",
        "MATCH_CONFIDENCE",
        "MATCH_REASON",
        "CONTROL_NOTE",
    ]
    return write_rows(output_dir / "alias_parent_child_partner_candidates.csv", columns, out_rows)


def query_exports(limit: int) -> list[QueryExport]:
    return [
        QueryExport(
            "00_app_scoped_vendor_scorecard.csv",
            """
            WITH loaded_months AS (
                SELECT
                    VENDOR,
                    BILLING_MONTH
                FROM THIRD_PARTY_RECON_SUMMARY_PROD
                WHERE BILLING_MONTH BETWEEN '2026-01-01' AND '2026-08-01'
                  AND (
                      UPPER(COALESCE(DATA_LOAD_STATUS, '')) = 'LOADED'
                      OR COALESCE(USAGE_ROW_COUNT, 0) > 0
                  )
            ),
            scoped AS (
                SELECT o.*
                FROM THIRD_PARTY_RECON_OUTPUT_PROD o
                JOIN loaded_months m
                  ON m.VENDOR = o.VENDOR
                 AND m.BILLING_MONTH = o.BILLING_MONTH
            )
            SELECT
                VENDOR,
                LISTAGG(DISTINCT TO_CHAR(BILLING_MONTH, 'YYYY-MM'), ', ')
                    WITHIN GROUP (ORDER BY TO_CHAR(BILLING_MONTH, 'YYYY-MM')) AS INCLUDED_MONTHS,
                COUNT(*) AS TOTAL_ROWS,
                COUNT_IF(EXCEPTION_TYPE = 'Clear') AS CLEAR_ROWS,
                ROUND(COUNT_IF(EXCEPTION_TYPE = 'Clear') * 100.0 / NULLIF(COUNT(*), 0), 1) AS CLEAR_PCT,
                ROUND(SUM(COALESCE(VENDOR_QUANTITY, 0)), 0) AS TOTAL_VENDOR_SEATS,
                ROUND(SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0)), 0) AS TOTAL_BILLING_SEATS,
                ROUND(SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0)) / NULLIF(SUM(COALESCE(VENDOR_QUANTITY, 0)), 0) * 100, 1)
                    AS SEAT_PARITY_PCT,
                ROUND(SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0)) - SUM(COALESCE(VENDOR_QUANTITY, 0)), 0) AS SEAT_DELTA,
                ROUND(
                    (SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)) - SUM(COALESCE(VENDOR_AMOUNT, 0)))
                    / NULLIF(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 0) * 100,
                    1
                ) AS CW_MARGIN_PCT,
                ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)) - SUM(COALESCE(VENDOR_AMOUNT, 0)), 0) AS CW_MARGIN_DOLLARS,
                'Matches app scope: Jan-Aug 2026 selected, but only vendor-months with vendor usage loaded' AS CONTROL_NOTE
            FROM scoped
            GROUP BY 1
            ORDER BY CLEAR_PCT ASC
            """,
        ),
        QueryExport(
            "01_executive_vendor_scorecard.csv",
            """
            SELECT
                VENDOR,
                COUNT(*) AS TOTAL_ROWS,
                COUNT_IF(EXCEPTION_TYPE = 'Clear') AS CLEAR_ROWS,
                ROUND(COUNT_IF(EXCEPTION_TYPE = 'Clear') * 100.0 / NULLIF(COUNT(*), 0), 1) AS CLEAR_PCT,
                ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
                ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
                ROUND(
                    (SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)) - SUM(COALESCE(VENDOR_AMOUNT, 0)))
                    / NULLIF(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 0) * 100,
                    1
                ) AS CW_MARGIN_PCT,
                ROUND(SUM(ABS(COALESCE(QTY_DELTA, 0))), 2) AS ABS_QTY_DELTA,
                ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ABS_AMOUNT_DELTA,
                COUNT_IF(EXCEPTION_TYPE = 'Unmapped Partner') AS UNMAPPED_ROWS,
                COUNT_IF(EXCEPTION_TYPE = 'Vendor SKU, No CW SKU') AS SKU_GAP_ROWS,
                COUNT_IF(EXCEPTION_TYPE = 'Vendor Billing, No CW Billing') AS VENDOR_NO_CW_ROWS,
                COUNT_IF(EXCEPTION_TYPE = 'Vendor Billing, Insufficient CW Billing') AS VENDOR_INSUFF_CW_ROWS,
                COUNT_IF(EXCEPTION_TYPE = 'API Usage, Insufficient CW Billing') AS API_INSUFF_CW_ROWS,
                COUNT_IF(EXCEPTION_TYPE = 'CW Billing, No Vendor Billing') AS CW_NO_VENDOR_ROWS,
                COUNT_IF(EXCEPTION_TYPE = 'Marketplace Billing Delay') AS TIMING_ROWS,
                COUNT_IF(EXCEPTION_TYPE = 'Other Issue') AS OTHER_ISSUE_ROWS
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            GROUP BY 1
            ORDER BY CLEAR_PCT ASC, TOTAL_ROWS DESC
            """,
        ),
        QueryExport(
            "02_clear_rate_lever_scorecard.csv",
            """
            WITH totals AS (
                SELECT VENDOR, COUNT(*) AS TOTAL_ROWS
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                GROUP BY 1
            ),
            levers AS (
                SELECT
                    VENDOR,
                    'Partner map gaps' AS LEVER,
                    'Data team' AS OWNER,
                    'Map vendor partner aliases to the CW billing-level SF_ID' AS ACTION,
                    COUNT(*) AS ROW_COUNT,
                    COUNT(DISTINCT VENDOR_PARTNER_NAME || '|' || TO_CHAR(BILLING_MONTH, 'YYYY-MM')) AS PARTNER_MONTHS,
                    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
                    ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ABS_AMOUNT_DELTA,
                    ROUND(SUM(ABS(COALESCE(QTY_DELTA, 0))), 2) AS ABS_QTY_DELTA
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                WHERE EXCEPTION_TYPE = 'Unmapped Partner'
                GROUP BY 1
                UNION ALL
                SELECT
                    VENDOR,
                    'SKU map/catalog gaps' AS LEVER,
                    'Product / Catalog' AS OWNER,
                    'Add or correct CW rebill SKU mapping for vendor product' AS ACTION,
                    COUNT(*) AS ROW_COUNT,
                    COUNT(DISTINCT COALESCE(SF_ID, VENDOR_PARTNER_NAME) || '|' || TO_CHAR(BILLING_MONTH, 'YYYY-MM')) AS PARTNER_MONTHS,
                    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
                    ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ABS_AMOUNT_DELTA,
                    ROUND(SUM(ABS(COALESCE(QTY_DELTA, 0))), 2) AS ABS_QTY_DELTA
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                WHERE EXCEPTION_TYPE = 'Vendor SKU, No CW SKU'
                GROUP BY 1
                UNION ALL
                SELECT
                    VENDOR,
                    'Finance onboarding gaps' AS LEVER,
                    'Finance / Sales' AS OWNER,
                    'Create or correct rebill contract where vendor charges CW and CW has no billing' AS ACTION,
                    COUNT(*) AS ROW_COUNT,
                    COUNT(DISTINCT COALESCE(SF_ID, VENDOR_PARTNER_NAME) || '|' || TO_CHAR(BILLING_MONTH, 'YYYY-MM')) AS PARTNER_MONTHS,
                    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
                    ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ABS_AMOUNT_DELTA,
                    ROUND(SUM(ABS(COALESCE(QTY_DELTA, 0))), 2) AS ABS_QTY_DELTA
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                WHERE EXCEPTION_TYPE = 'Vendor Billing, No CW Billing'
                GROUP BY 1
                UNION ALL
                SELECT
                    VENDOR,
                    'Insufficient billing gaps' AS LEVER,
                    'Finance / Sales' AS OWNER,
                    'Close material underbilling where vendor/API is ahead of CW billing' AS ACTION,
                    COUNT(*) AS ROW_COUNT,
                    COUNT(DISTINCT COALESCE(SF_ID, VENDOR_PARTNER_NAME) || '|' || TO_CHAR(BILLING_MONTH, 'YYYY-MM')) AS PARTNER_MONTHS,
                    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
                    ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ABS_AMOUNT_DELTA,
                    ROUND(SUM(ABS(COALESCE(QTY_DELTA, 0))), 2) AS ABS_QTY_DELTA
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                WHERE EXCEPTION_TYPE IN (
                    'Vendor Billing, Insufficient CW Billing',
                    'API Usage, Insufficient CW Billing'
                )
                GROUP BY 1
                UNION ALL
                SELECT
                    VENDOR,
                    'Known discount/bundle review' AS LEVER,
                    'Data + Finance policy' AS OWNER,
                    'Confirm intentional bundle/discount before no-action tagging' AS ACTION,
                    COUNT(*) AS ROW_COUNT,
                    COUNT(DISTINCT COALESCE(SF_ID, VENDOR_PARTNER_NAME) || '|' || TO_CHAR(BILLING_MONTH, 'YYYY-MM')) AS PARTNER_MONTHS,
                    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
                    ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ABS_AMOUNT_DELTA,
                    ROUND(SUM(ABS(COALESCE(QTY_DELTA, 0))), 2) AS ABS_QTY_DELTA
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                WHERE EXCEPTION_TYPE IN (
                    'Vendor Billing, Insufficient CW Billing',
                    'API Usage, Insufficient CW Billing',
                    'Vendor Billing, No CW Billing'
                )
                  AND COALESCE(TOTAL_BILLING_AMOUNT, 0) > 0
                  AND COALESCE(VENDOR_AMOUNT, 0) > COALESCE(TOTAL_BILLING_AMOUNT, 0)
                  AND (
                      ABS(COALESCE(QTY_DELTA, 0)) <= GREATEST(2, ABS(COALESCE(VENDOR_QUANTITY, 0)) * 0.05)
                      OR UPPER(COALESCE(VENDOR_PRODUCT, '') || ' ' || COALESCE(SKU_MATCH_GROUP, '')) RLIKE '.*(RMM|MDR|BUNDLE|PROMO|TAKEOUT).*'
                  )
                GROUP BY 1
            )
            SELECT
                l.*,
                t.TOTAL_ROWS,
                ROUND(l.ROW_COUNT * 100.0 / NULLIF(t.TOTAL_ROWS, 0), 1) AS THEORETICAL_CLEAR_RATE_POINT_LIFT,
                CASE
                    WHEN l.LEVER IN ('Partner map gaps', 'SKU map/catalog gaps') THEN 'Unlocks matching; final outcome may become Clear or a true billing exception'
                    WHEN l.LEVER = 'Known discount/bundle review' THEN 'Only no-action if business owner confirms intentional pricing'
                    ELSE 'Operational fix; should not be relabeled by data logic'
                END AS CONTROL_NOTE
            FROM levers l
            JOIN totals t ON t.VENDOR = l.VENDOR
            ORDER BY l.ROW_COUNT DESC, l.ABS_AMOUNT_DELTA DESC
            """,
        ),
        QueryExport(
            "03_unmapped_partner_months_for_seed_triage.csv",
            f"""
            WITH base AS (
                SELECT
                    VENDOR,
                    VENDOR_PARTNER_NAME,
                    BILLING_MONTH,
                    UPPER(REGEXP_REPLACE(COALESCE(VENDOR_PARTNER_NAME, ''), '[^A-Za-z0-9]', '')) AS PARTNER_NAME_COMPACT,
                    COUNT(*) AS ROW_COUNT,
                    COUNT(DISTINCT COALESCE(SKU_MATCH_GROUP, VENDOR_PRODUCT, '(missing)')) AS PRODUCT_GROUP_COUNT,
                    ROUND(SUM(COALESCE(VENDOR_QUANTITY, 0)), 2) AS VENDOR_QUANTITY,
                    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
                    ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ABS_AMOUNT_DELTA,
                    ROUND(SUM(ABS(COALESCE(QTY_DELTA, 0))), 2) AS ABS_QTY_DELTA,
                    MIN(COALESCE(SKU_MATCH_GROUP, VENDOR_PRODUCT, '(missing)')) AS SAMPLE_PRODUCT_GROUP
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                WHERE EXCEPTION_TYPE = 'Unmapped Partner'
                GROUP BY 1, 2, 3, 4
            ),
            exact_map AS (
                SELECT
                    UPPER(REGEXP_REPLACE(COALESCE(PARTNER_NAME, ''), '[^A-Za-z0-9]', '')) AS PARTNER_NAME_COMPACT,
                    COUNT(DISTINCT SF_ID) AS CANDIDATE_SF_ID_COUNT,
                    MIN(SF_ID) AS SAMPLE_CANDIDATE_SF_ID,
                    MIN(PARTNER_NAME) AS SAMPLE_CANDIDATE_PARTNER_NAME,
                    MIN(PARENT_COMPANY) AS SAMPLE_CANDIDATE_PARENT_COMPANY
                FROM RECON_PARTNER_MAP
                WHERE PARTNER_NAME IS NOT NULL
                  AND SF_ID IS NOT NULL
                  AND UPPER(TRIM(SF_ID)) NOT IN ('', 'UNKNOWN', 'NONE', 'UNMAPPED', 'NULL')
                GROUP BY 1
            )
            SELECT
                b.*,
                IFF(e.PARTNER_NAME_COMPACT IS NULL, 'NO_EXACT_COMPACT_MATCH', 'EXACT_COMPACT_MATCH_REVIEW') AS MAP_CANDIDATE_STATUS,
                e.CANDIDATE_SF_ID_COUNT,
                e.SAMPLE_CANDIDATE_SF_ID,
                e.SAMPLE_CANDIDATE_PARTNER_NAME,
                e.SAMPLE_CANDIDATE_PARENT_COMPANY,
                'Validate row presence, parent/child billing level, quantity, and amount before adding seed map' AS CONTROL_NOTE
            FROM base b
            LEFT JOIN exact_map e
                ON e.PARTNER_NAME_COMPACT = b.PARTNER_NAME_COMPACT
            ORDER BY b.ROW_COUNT DESC, b.ABS_AMOUNT_DELTA DESC
            LIMIT {int(limit)}
            """,
        ),
        QueryExport(
            "04_sku_catalog_gap_triage.csv",
            f"""
            WITH output_gap AS (
                SELECT
                    VENDOR,
                    COALESCE(SKU_MATCH_GROUP, '(missing)') AS SKU_MATCH_GROUP,
                    COALESCE(VENDOR_PRODUCT, '(missing)') AS VENDOR_PRODUCT,
                    COUNT(*) AS ROW_COUNT,
                    COUNT(DISTINCT COALESCE(SF_ID, VENDOR_PARTNER_NAME)) AS PARTNER_COUNT,
                    COUNT(DISTINCT BILLING_MONTH) AS MONTH_COUNT,
                    ROUND(SUM(COALESCE(VENDOR_QUANTITY, 0)), 2) AS VENDOR_QUANTITY,
                    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
                    ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ABS_AMOUNT_DELTA
                FROM THIRD_PARTY_RECON_OUTPUT_PROD
                WHERE EXCEPTION_TYPE = 'Vendor SKU, No CW SKU'
                GROUP BY 1, 2, 3
            ),
            map_agg AS (
                SELECT
                    VENDOR,
                    COALESCE(SKU_MATCH_KEY, '(missing)') AS SKU_MATCH_GROUP,
                    COALESCE(VENDOR_PRODUCT, '(missing)') AS VENDOR_PRODUCT,
                    COUNT(DISTINCT NULLIF(CW_SKU, 'UNMAPPED')) AS EXISTING_CW_SKU_MAP_COUNT,
                    MIN(NULLIF(CW_SKU, 'UNMAPPED')) AS SAMPLE_EXISTING_CW_SKU
                FROM RECON_SKU_MAP
                GROUP BY 1, 2, 3
            )
            SELECT
                o.*,
                COALESCE(MAX(m.EXISTING_CW_SKU_MAP_COUNT), 0) AS EXISTING_CW_SKU_MAP_COUNT,
                MIN(m.SAMPLE_EXISTING_CW_SKU) AS SAMPLE_EXISTING_CW_SKU,
                'Catalog owner must confirm whether a rebill SKU exists, is retired, or must be created' AS CONTROL_NOTE
            FROM output_gap o
            LEFT JOIN map_agg m
                ON m.VENDOR = o.VENDOR
               AND (
                    UPPER(TRIM(m.SKU_MATCH_GROUP)) = UPPER(TRIM(o.SKU_MATCH_GROUP))
                    OR UPPER(TRIM(m.VENDOR_PRODUCT)) = UPPER(TRIM(o.VENDOR_PRODUCT))
               )
            GROUP BY
                o.VENDOR,
                o.SKU_MATCH_GROUP,
                o.VENDOR_PRODUCT,
                o.ROW_COUNT,
                o.PARTNER_COUNT,
                o.MONTH_COUNT,
                o.VENDOR_QUANTITY,
                o.VENDOR_AMOUNT,
                o.CW_BILLING_AMOUNT,
                o.ABS_AMOUNT_DELTA
            ORDER BY o.ROW_COUNT DESC, o.ABS_AMOUNT_DELTA DESC
            LIMIT {int(limit)}
            """,
        ),
        QueryExport(
            "05_finance_onboarding_backlog.csv",
            f"""
            SELECT
                VENDOR,
                BILLING_MONTH,
                SF_ID,
                VENDOR_PARTNER_NAME,
                COALESCE(SKU_MATCH_GROUP, VENDOR_PRODUCT, '(missing)') AS PRODUCT_GROUP,
                EXCEPTION_TYPE,
                COUNT(*) AS ROW_COUNT,
                ROUND(SUM(COALESCE(VENDOR_QUANTITY, 0)), 2) AS VENDOR_QUANTITY,
                ROUND(SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0)), 2) AS CW_BILLING_QUANTITY,
                ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
                ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
                ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ABS_AMOUNT_DELTA,
                MAX(ACTION_NEEDED) AS ACTION_NEEDED,
                'Business action required; do not fix by changing map/classification unless source linkage is wrong' AS CONTROL_NOTE
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            WHERE EXCEPTION_TYPE IN (
                'Vendor Billing, No CW Billing',
                'Vendor Billing, Insufficient CW Billing',
                'API Usage, Insufficient CW Billing'
            )
            GROUP BY 1, 2, 3, 4, 5, 6
            ORDER BY ABS_AMOUNT_DELTA DESC, ROW_COUNT DESC
            LIMIT {int(limit)}
            """,
        ),
        QueryExport(
            "06_known_discount_bundle_review_candidates.csv",
            f"""
            SELECT
                VENDOR,
                BILLING_MONTH,
                SF_ID,
                VENDOR_PARTNER_NAME,
                COALESCE(SKU_MATCH_GROUP, VENDOR_PRODUCT, '(missing)') AS PRODUCT_GROUP,
                EXCEPTION_TYPE,
                HAS_DISCOUNT,
                ROUND(SUM(COALESCE(VENDOR_QUANTITY, 0)), 2) AS VENDOR_QUANTITY,
                ROUND(SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0)), 2) AS CW_BILLING_QUANTITY,
                ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
                ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
                ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ABS_AMOUNT_DELTA,
                ROUND(SUM(ABS(COALESCE(QTY_DELTA, 0))), 2) AS ABS_QTY_DELTA,
                'Candidate only; require policy evidence before tagging Known Discount / Bundle' AS CONTROL_NOTE
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            WHERE EXCEPTION_TYPE IN (
                'Vendor Billing, Insufficient CW Billing',
                'API Usage, Insufficient CW Billing',
                'Vendor Billing, No CW Billing'
            )
              AND COALESCE(TOTAL_BILLING_AMOUNT, 0) > 0
              AND COALESCE(VENDOR_AMOUNT, 0) > COALESCE(TOTAL_BILLING_AMOUNT, 0)
              AND (
                  ABS(COALESCE(QTY_DELTA, 0)) <= GREATEST(2, ABS(COALESCE(VENDOR_QUANTITY, 0)) * 0.05)
                  OR UPPER(COALESCE(VENDOR_PRODUCT, '') || ' ' || COALESCE(SKU_MATCH_GROUP, '')) RLIKE '.*(RMM|MDR|BUNDLE|PROMO|TAKEOUT).*'
              )
            GROUP BY 1, 2, 3, 4, 5, 6, 7
            ORDER BY ABS_AMOUNT_DELTA DESC, VENDOR_AMOUNT DESC
            LIMIT {int(limit)}
            """,
        ),
        QueryExport(
            "07_partner_map_cardinality_risks.csv",
            f"""
            SELECT
                UPPER(REGEXP_REPLACE(COALESCE(PARTNER_NAME, ''), '[^A-Za-z0-9]', '')) AS PARTNER_NAME_COMPACT,
                COUNT(*) AS MAP_ROWS,
                COUNT(DISTINCT SF_ID) AS DISTINCT_SF_IDS,
                COUNT(DISTINCT CMS_ID) AS DISTINCT_CMS_IDS,
                MIN(PARTNER_NAME) AS SAMPLE_PARTNER_NAME,
                LISTAGG(DISTINCT SF_ID, ', ') WITHIN GROUP (ORDER BY SF_ID) AS SF_ID_LIST,
                'Multiple SF_IDs for the same normalized partner name can create false matches or fanout' AS CONTROL_NOTE
            FROM RECON_PARTNER_MAP
            WHERE PARTNER_NAME IS NOT NULL
            GROUP BY 1
            HAVING COUNT(DISTINCT SF_ID) > 1
            ORDER BY DISTINCT_SF_IDS DESC, MAP_ROWS DESC
            LIMIT {int(limit)}
            """,
        ),
        QueryExport(
            "08_sku_map_cardinality_risks.csv",
            f"""
            SELECT
                VENDOR,
                COALESCE(SKU_MATCH_KEY, '(missing)') AS SKU_MATCH_KEY,
                COALESCE(VENDOR_PRODUCT, '(missing)') AS VENDOR_PRODUCT,
                COUNT(*) AS MAP_ROWS,
                COUNT(DISTINCT CW_SKU) AS DISTINCT_CW_SKUS,
                LISTAGG(DISTINCT CW_SKU, ', ') WITHIN GROUP (ORDER BY CW_SKU) AS CW_SKU_LIST,
                'Many CW SKUs can be valid bundle logic, but must be joined at the intended grain to avoid fanout' AS CONTROL_NOTE
            FROM RECON_SKU_MAP
            GROUP BY 1, 2, 3
            HAVING COUNT(DISTINCT CW_SKU) > 1
            ORDER BY DISTINCT_CW_SKUS DESC, MAP_ROWS DESC
            LIMIT {int(limit)}
            """,
        ),
        QueryExport(
            "09_invoice_vs_raw_usage_control_gaps.csv",
            f"""
            SELECT
                VENDOR,
                BILLING_MONTH,
                SKU,
                SOURCE_STATUS,
                VENDOR_INVOICE_SKU,
                VENDOR_USAGE_SKU,
                VENDOR_INVOICE_LINE_COUNT,
                VENDOR_RAW_USAGE_LINE_COUNT,
                VENDOR_INVOICE_SEATS,
                VENDOR_RAW_USAGE_SEATS,
                DELTA_SEATS,
                VENDOR_INVOICE_AMOUNT,
                VENDOR_RAW_USAGE_AMOUNT,
                DELTA_AMOUNT,
                'Invoice-vs-usage control must tie before clear-rate movement is trusted' AS CONTROL_NOTE
            FROM THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
            WHERE ABS(COALESCE(DELTA_SEATS, 0)) > 0.01
               OR ABS(COALESCE(DELTA_AMOUNT, 0)) > 1
               OR SOURCE_STATUS <> 'BOTH'
            ORDER BY ABS(COALESCE(DELTA_AMOUNT, 0)) DESC, ABS(COALESCE(DELTA_SEATS, 0)) DESC
            LIMIT {int(limit)}
            """,
        ),
        QueryExport(
            "10_month_load_health.csv",
            """
            SELECT
                VENDOR,
                BILLING_MONTH,
                DATA_LOAD_STATUS,
                USAGE_ROW_COUNT,
                VENDOR_MEDIAN_USAGE_ROWS,
                TOTAL_ROWS,
                PERFECT_MATCH_ROWS,
                CLEAR_PCT,
                TOTAL_VENDOR_AMOUNT,
                TOTAL_BILLING_AMOUNT,
                TOTAL_LEAKAGE_AMOUNT,
                'Do not optimize clear rate on PARTIAL or NOT_LOADED months' AS CONTROL_NOTE
            FROM THIRD_PARTY_RECON_SUMMARY_PROD
            ORDER BY VENDOR, BILLING_MONTH
            """,
        ),
        QueryExport(
            "11_mapped_partner_month_offset_candidates.csv",
            f"""
            WITH loaded_months AS (
                SELECT VENDOR, BILLING_MONTH
                FROM THIRD_PARTY_RECON_SUMMARY_PROD
                WHERE BILLING_MONTH BETWEEN '2026-01-01' AND '2026-08-01'
                  AND (
                      UPPER(COALESCE(DATA_LOAD_STATUS, '')) = 'LOADED'
                      OR COALESCE(USAGE_ROW_COUNT, 0) > 0
                  )
            ),
            base AS (
                SELECT o.*
                FROM THIRD_PARTY_RECON_OUTPUT_PROD o
                JOIN loaded_months m
                  ON m.VENDOR = o.VENDOR
                 AND m.BILLING_MONTH = o.BILLING_MONTH
                WHERE o.SF_ID IS NOT NULL
                  AND NOT STARTSWITH(UPPER(TRIM(o.SF_ID)), 'UNMAPPED')
            ),
            grouped AS (
                SELECT
                    VENDOR,
                    SF_ID,
                    BILLING_MONTH,
                    COUNT(*) AS ROW_COUNT,
                    COUNT_IF(EXCEPTION_TYPE = 'Clear') AS CLEAR_ROWS,
                    COUNT_IF(EXCEPTION_TYPE = 'CW Billing, No Vendor Billing') AS CW_NO_VENDOR_ROWS,
                    COUNT_IF(EXCEPTION_TYPE = 'CW SKU, No Vendor SKU') AS CW_SKU_NO_VENDOR_ROWS,
                    COUNT_IF(EXCEPTION_TYPE = 'Vendor Billing, No CW Billing') AS VENDOR_NO_CW_ROWS,
                    COUNT_IF(EXCEPTION_TYPE = 'Vendor Billing, Insufficient CW Billing') AS VENDOR_INSUFF_CW_ROWS,
                    COUNT_IF(EXCEPTION_TYPE = 'API Usage, Insufficient CW Billing') AS API_INSUFF_CW_ROWS,
                    COUNT_IF(EXCEPTION_TYPE = 'Vendor SKU, No CW SKU') AS VENDOR_SKU_NO_CW_ROWS,
                    ROUND(SUM(COALESCE(VENDOR_QUANTITY, 0)), 2) AS VENDOR_QUANTITY,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0)), 2) AS CW_BILLING_QUANTITY,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0)) - SUM(COALESCE(VENDOR_QUANTITY, 0)), 2) AS NET_QTY_DELTA,
                    ROUND(SUM(ABS(COALESCE(QTY_DELTA, 0))), 2) AS ROW_ABS_QTY_DELTA,
                    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)) - SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS NET_AMOUNT_DELTA,
                    ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ROW_ABS_AMOUNT_DELTA,
                    LISTAGG(DISTINCT EXCEPTION_TYPE, ' | ') WITHIN GROUP (ORDER BY EXCEPTION_TYPE) AS EXCEPTION_TYPES,
                    LISTAGG(DISTINCT COALESCE(SKU_MATCH_GROUP, VENDOR_PRODUCT, '(missing)'), ' | ')
                        WITHIN GROUP (ORDER BY COALESCE(SKU_MATCH_GROUP, VENDOR_PRODUCT, '(missing)')) AS PRODUCT_GROUPS,
                    MIN(VENDOR_PARTNER_NAME) AS SAMPLE_VENDOR_PARTNER_NAME
                FROM base
                GROUP BY 1, 2, 3
            )
            SELECT
                *,
                CASE
                    WHEN CW_BILLING_AMOUNT >= VENDOR_AMOUNT AND VENDOR_AMOUNT > 0
                        THEN 'SHOULD_CLEAR_AT_PARTNER_MONTH_AMOUNT_IF_ROWS_ARE_COMPARABLE'
                    WHEN ABS(NET_QTY_DELTA) <= GREATEST(5, VENDOR_QUANTITY * 0.03)
                        THEN 'QTY_OFFSETS_WITHIN_NOISE_BUT_DOLLARS_DO_NOT'
                    ELSE 'OFFSET_REVIEW'
                END AS OFFSET_OPPORTUNITY_TYPE,
                'Review for SKU-grain split, source-family split, or duplicate row partitioning before relabeling as Clear' AS CONTROL_NOTE
            FROM grouped
            WHERE (CW_NO_VENDOR_ROWS + CW_SKU_NO_VENDOR_ROWS) > 0
              AND (VENDOR_NO_CW_ROWS + VENDOR_INSUFF_CW_ROWS + API_INSUFF_CW_ROWS + VENDOR_SKU_NO_CW_ROWS) > 0
              AND (
                    CW_BILLING_AMOUNT >= VENDOR_AMOUNT
                    OR ABS(NET_QTY_DELTA) <= GREATEST(5, VENDOR_QUANTITY * 0.03)
                  )
            ORDER BY ROW_ABS_AMOUNT_DELTA DESC, ROW_ABS_QTY_DELTA DESC
            LIMIT {int(limit)}
            """,
        ),
        QueryExport(
            "12_normalized_partner_name_offset_candidates.csv",
            f"""
            WITH loaded_months AS (
                SELECT VENDOR, BILLING_MONTH
                FROM THIRD_PARTY_RECON_SUMMARY_PROD
                WHERE BILLING_MONTH BETWEEN '2026-01-01' AND '2026-08-01'
                  AND (
                      UPPER(COALESCE(DATA_LOAD_STATUS, '')) = 'LOADED'
                      OR COALESCE(USAGE_ROW_COUNT, 0) > 0
                  )
            ),
            base AS (
                SELECT
                    o.*,
                    TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(COALESCE(o.VENDOR_PARTNER_NAME, '')), '[^a-z0-9]+', ' '), '\\\\s+', ' ')) AS PARTNER_NAME_NORMALIZED
                FROM THIRD_PARTY_RECON_OUTPUT_PROD o
                JOIN loaded_months m
                  ON m.VENDOR = o.VENDOR
                 AND m.BILLING_MONTH = o.BILLING_MONTH
                WHERE o.VENDOR_PARTNER_NAME IS NOT NULL
            ),
            grouped AS (
                SELECT
                    VENDOR,
                    PARTNER_NAME_NORMALIZED,
                    BILLING_MONTH,
                    COUNT(*) AS ROW_COUNT,
                    COUNT(DISTINCT SF_ID) AS DISTINCT_SF_IDS,
                    COUNT_IF(SF_ID IS NULL OR STARTSWITH(UPPER(TRIM(COALESCE(SF_ID, ''))), 'UNMAPPED')) AS UNMAPPED_ROWS,
                    COUNT_IF(EXCEPTION_TYPE = 'CW Billing, No Vendor Billing') AS CW_NO_VENDOR_ROWS,
                    COUNT_IF(EXCEPTION_TYPE = 'CW SKU, No Vendor SKU') AS CW_SKU_NO_VENDOR_ROWS,
                    COUNT_IF(EXCEPTION_TYPE = 'Vendor Billing, No CW Billing') AS VENDOR_NO_CW_ROWS,
                    COUNT_IF(EXCEPTION_TYPE = 'Vendor Billing, Insufficient CW Billing') AS VENDOR_INSUFF_CW_ROWS,
                    COUNT_IF(EXCEPTION_TYPE = 'API Usage, Insufficient CW Billing') AS API_INSUFF_CW_ROWS,
                    COUNT_IF(EXCEPTION_TYPE = 'Vendor SKU, No CW SKU') AS VENDOR_SKU_NO_CW_ROWS,
                    ROUND(SUM(COALESCE(VENDOR_QUANTITY, 0)), 2) AS VENDOR_QUANTITY,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0)), 2) AS CW_BILLING_QUANTITY,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0)) - SUM(COALESCE(VENDOR_QUANTITY, 0)), 2) AS NET_QTY_DELTA,
                    ROUND(SUM(ABS(COALESCE(QTY_DELTA, 0))), 2) AS ROW_ABS_QTY_DELTA,
                    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS VENDOR_AMOUNT,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2) AS CW_BILLING_AMOUNT,
                    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)) - SUM(COALESCE(VENDOR_AMOUNT, 0)), 2) AS NET_AMOUNT_DELTA,
                    ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA, 0))), 2) AS ROW_ABS_AMOUNT_DELTA,
                    LISTAGG(DISTINCT COALESCE(SF_ID, '(null)'), ' | ') WITHIN GROUP (ORDER BY COALESCE(SF_ID, '(null)')) AS SF_IDS,
                    LISTAGG(DISTINCT EXCEPTION_TYPE, ' | ') WITHIN GROUP (ORDER BY EXCEPTION_TYPE) AS EXCEPTION_TYPES,
                    LISTAGG(DISTINCT COALESCE(SKU_MATCH_GROUP, VENDOR_PRODUCT, '(missing)'), ' | ')
                        WITHIN GROUP (ORDER BY COALESCE(SKU_MATCH_GROUP, VENDOR_PRODUCT, '(missing)')) AS PRODUCT_GROUPS,
                    MIN(VENDOR_PARTNER_NAME) AS SAMPLE_VENDOR_PARTNER_NAME
                FROM base
                GROUP BY 1, 2, 3
            )
            SELECT
                *,
                CASE
                    WHEN UNMAPPED_ROWS > 0
                        THEN 'PARTNER_MAPPING_MAY_UNLOCK_OFFSET'
                    WHEN DISTINCT_SF_IDS > 1
                        THEN 'POSSIBLE_MERGE_OR_PARENT_CHILD_SPLIT'
                    WHEN CW_BILLING_AMOUNT >= VENDOR_AMOUNT AND VENDOR_AMOUNT > 0
                        THEN 'NAME_GRAIN_AMOUNT_OFFSET'
                    ELSE 'NAME_GRAIN_QTY_OFFSET'
                END AS OFFSET_OPPORTUNITY_TYPE,
                'Use as candidate evidence only; final fix belongs in partner map, SKU map, or vendor source grain, not manual relabeling' AS CONTROL_NOTE
            FROM grouped
            WHERE (CW_NO_VENDOR_ROWS + CW_SKU_NO_VENDOR_ROWS) > 0
              AND (UNMAPPED_ROWS + VENDOR_NO_CW_ROWS + VENDOR_INSUFF_CW_ROWS + API_INSUFF_CW_ROWS + VENDOR_SKU_NO_CW_ROWS) > 0
              AND (
                    CW_BILLING_AMOUNT >= VENDOR_AMOUNT
                    OR ABS(NET_QTY_DELTA) <= GREATEST(5, VENDOR_QUANTITY * 0.03)
                  )
            ORDER BY ROW_ABS_AMOUNT_DELTA DESC, ROW_ABS_QTY_DELTA DESC
            LIMIT {int(limit)}
            """,
        ),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export live clear-rate lever audit CSVs.")
    parser.add_argument("--limit", type=int, default=1000, help="Max rows for detail triage CSVs.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional explicit output directory. Defaults to output/clear_rate_levers_<timestamp>.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or REPO / "output" / f"clear_rate_levers_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        for stmt in USE_CONTEXT:
            conn.cursor().execute(stmt)

        print(f"Writing clear-rate lever audit to {output_dir}")
        for spec in query_exports(args.limit):
            count = export_query(conn, output_dir, spec)
            print(f"  {spec.file_name}: {count:,} rows")

        count = build_alias_candidates(conn, output_dir, args.limit)
        print(f"  alias_parent_child_partner_candidates.csv: {count:,} rows")
        print("Done.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
