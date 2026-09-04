"""Load the governed Webroot SKU map from the focused repository seed.

The loader replaces only Webroot rows in THIRD_PARTY_RECON_SKU_MAP_PROD. It
uses one transaction, validates current billing coverage before commit, and
creates no persistent auxiliary objects.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROJECTS_ROOT = REPO.parents[2]
sys.path.insert(0, str(PROJECTS_ROOT))

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

SEED_PATH = REPO / "Maps" / "seeds" / "WEBROOT_RECON_SKU_MAP.csv"
TARGET = "THIRD_PARTY_RECON_SKU_MAP_PROD"
COLUMNS = (
    "VENDOR",
    "VENDOR_PRODUCT",
    "VENDOR_SKU",
    "CW_SKU",
    "SKU_MATCH_KEY",
    "TRT_MATCH_KEY",
    "CONTRACT_COST_RATE",
    "VENDOR_UNIT_PRICE",
    "CW_UNIT_PRICE",
    "MAPPING_NOTES",
)
ALLOWED_KEYS = {"GSM", "DNS", "SAT", "OTHER_WEBROOT_TAGGED"}


def _nullable_float(value: str | None) -> float | None:
    text = (value or "").strip()
    return float(text) if text else None


def load_seed() -> list[tuple[object, ...]]:
    with SEED_PATH.open(newline="", encoding="utf-8-sig") as handle:
        records = list(csv.DictReader(handle))

    if not records:
        raise ValueError("Webroot SKU seed is empty")
    if set(records[0]) != set(COLUMNS):
        raise ValueError(f"Unexpected columns in {SEED_PATH.name}: {list(records[0])}")

    seen: set[str] = set()
    rows: list[tuple[object, ...]] = []
    for record in records:
        vendor = record["VENDOR"].strip()
        cw_sku = record["CW_SKU"].strip().upper()
        match_key = record["SKU_MATCH_KEY"].strip().upper()
        if vendor != "Webroot":
            raise ValueError(f"Non-Webroot row found: {vendor!r}")
        if not cw_sku or cw_sku in seen:
            raise ValueError(f"Blank or duplicate CW SKU: {cw_sku!r}")
        if match_key not in ALLOWED_KEYS:
            raise ValueError(f"Unsupported match key for {cw_sku}: {match_key}")
        seen.add(cw_sku)
        rows.append(
            (
                vendor,
                record["VENDOR_PRODUCT"].strip() or None,
                record["VENDOR_SKU"].strip() or None,
                cw_sku,
                match_key,
                record["TRT_MATCH_KEY"].strip() or None,
                _nullable_float(record["CONTRACT_COST_RATE"]),
                _nullable_float(record["VENDOR_UNIT_PRICE"]),
                _nullable_float(record["CW_UNIT_PRICE"]),
                record["MAPPING_NOTES"].strip() or None,
            )
        )
    return rows


def main() -> int:
    rows = load_seed()
    connection = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        connection.autocommit(False)
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {TARGET} WHERE VENDOR = 'Webroot'")
            placeholders = ", ".join(["%s"] * len(COLUMNS))
            cursor.executemany(
                f"INSERT INTO {TARGET} ({', '.join(COLUMNS)}) VALUES ({placeholders})",
                rows,
            )
            cursor.execute(
                f"""
                WITH billed AS (
                    SELECT DISTINCT UPPER(TRIM(PRODUCT_SKU)) AS CW_SKU
                    FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
                    WHERE VENDOR = 'Webroot'
                    UNION
                    SELECT DISTINCT UPPER(TRIM(PRODUCT_SKU))
                    FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
                    WHERE VENDOR = 'Webroot'
                )
                SELECT COUNT(*)
                FROM billed b
                LEFT JOIN {TARGET} m
                  ON m.VENDOR = 'Webroot'
                 AND UPPER(TRIM(m.CW_SKU)) = b.CW_SKU
                WHERE m.CW_SKU IS NULL
                """
            )
            (missing_count,) = cursor.fetchone()
            if missing_count:
                raise ValueError(f"Map would leave {missing_count} current billed SKUs absent")
        connection.commit()
        print(f"Loaded {len(rows)} governed Webroot SKU rows from {SEED_PATH.name}")
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
