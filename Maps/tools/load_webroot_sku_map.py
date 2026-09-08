"""Transactionally load the governed Webroot SKU seed into its source table."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = REPO.parents[2]
sys.path.insert(0, str(WORKSPACE_ROOT))

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

SEED_PATH = REPO / "Maps" / "seeds" / "WEBROOT_RECON_SKU_MAP.csv"
DATABASE = "ANALYTICS_DEV"
SCHEMA = "DBT_NFOLD_TRANSFORMATION"
TABLE = "THIRD_PARTY_RECON_SKU_MAP_PROD"
VENDOR = "Webroot"

USE = (
    "USE ROLE DEVELOPER; "
    "USE WAREHOUSE REPORTING_WH; "
    f"USE DATABASE {DATABASE}; "
    f"USE SCHEMA {SCHEMA};"
)

INSERT_SQL = f"""
INSERT INTO {TABLE} (
    VENDOR,
    VENDOR_PRODUCT,
    VENDOR_SKU,
    CW_SKU,
    SKU_MATCH_KEY,
    TRT_MATCH_KEY,
    CONTRACT_COST_RATE,
    VENDOR_UNIT_PRICE,
    CW_UNIT_PRICE,
    MAPPING_NOTES
)
SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
"""


def _none_if_blank(value: str) -> str | None:
    value = value.strip()
    return value or None


def _load_seed_rows() -> list[tuple[str | None, ...]]:
    with SEED_PATH.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise RuntimeError(f"Seed is empty: {SEED_PATH}")

    return [
        (
            row["VENDOR"],
            row["VENDOR_PRODUCT"],
            row["VENDOR_SKU"],
            row["CW_SKU"],
            row["SKU_MATCH_KEY"],
            row["TRT_MATCH_KEY"],
            _none_if_blank(row["CONTRACT_COST_RATE"]),
            _none_if_blank(row["VENDOR_UNIT_PRICE"]),
            _none_if_blank(row["CW_UNIT_PRICE"]),
            row["MAPPING_NOTES"],
        )
        for row in rows
    ]


def main() -> int:
    rows = _load_seed_rows()
    expected = {(row[1], row[2], row[3]) for row in rows}
    if len(expected) != len(rows):
        raise RuntimeError("Seed contains duplicate source_system/raw_sku/normalized_sku rows")

    conn = get_snowflake_connection()
    cur = conn.cursor()
    try:
        for statement in USE.split("; "):
            if statement.strip():
                cur.execute(statement)

        cur.execute("BEGIN")
        cur.execute(f"DELETE FROM {TABLE} WHERE UPPER(VENDOR) = UPPER(%s)", (VENDOR,))
        cur.executemany(INSERT_SQL, rows)

        cur.execute(
            f"""
            SELECT VENDOR_PRODUCT, VENDOR_SKU, CW_SKU
            FROM {TABLE}
            WHERE UPPER(VENDOR) = UPPER(%s)
            """,
            (VENDOR,),
        )
        loaded = set(cur.fetchall())
        missing = expected - loaded
        extra = loaded - expected
        if missing or extra:
            raise RuntimeError(
                f"Webroot SKU map validation failed; missing={sorted(missing)}, extra={sorted(extra)}"
            )

        conn.commit()
        print(f"Loaded and validated {len(loaded)} governed Webroot SKU mappings into {TABLE}.")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
