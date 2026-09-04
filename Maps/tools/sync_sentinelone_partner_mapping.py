from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


PARTNER_SHEET = "PARTNER_MAPPING"
TARGET_TABLE = "ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_VENDOR_PARTNER_MANUAL_MAP"


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).replace("\xa0", " ").strip().lower()).strip("_")


def _norm_partner(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(name).lower())).strip()


def _clean_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def load_partner_sheet(xlsx_path: Path) -> pd.DataFrame:
    frame = pd.read_excel(xlsx_path, sheet_name=PARTNER_SHEET)
    frame.columns = [_norm_col(c) for c in frame.columns]

    rename_map = {
        "account_name": "partner_name",
        "sf_id": "sf_id",
        "cms_id": "cms_id",
        "zuora_name": "zuora_name",
        "parent_co": "parent_company",
    }
    frame = frame.rename(columns=rename_map)

    for col in ["partner_name", "sf_id", "cms_id", "zuora_name", "parent_company"]:
        if col not in frame.columns:
            frame[col] = None

    frame = frame[["partner_name", "sf_id", "cms_id", "zuora_name", "parent_company"]].copy()
    frame["partner_name"] = frame["partner_name"].map(_clean_value)
    frame["sf_id"] = frame["sf_id"].map(_clean_value)
    frame["cms_id"] = frame["cms_id"].map(_clean_value)
    frame["zuora_name"] = frame["zuora_name"].map(_clean_value)
    frame["parent_company"] = frame["parent_company"].map(_clean_value)

    frame = frame[frame["partner_name"].notna() & frame["sf_id"].notna()].copy()
    frame = frame[frame["sf_id"].str.upper().str.match(r"^ACT-[0-9A-Z-]+$")].copy()
    frame["pn_norm"] = frame["partner_name"].map(_norm_partner)

    frame = frame.sort_values(["pn_norm", "zuora_name", "cms_id"], ascending=[True, False, False])
    frame = frame.drop_duplicates(subset=["pn_norm"], keep="first")
    return frame.drop(columns=["pn_norm"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync SentinelOne partner mapping sheet to Snowflake manual map table.")
    parser.add_argument("--xlsx", required=True, help="Path to SentinelOne mapping workbook")
    parser.add_argument("--source-tag", default="SentinelOne PARTNER_MAPPING", help="Audit tag stored in SOURCE_TAG")
    args = parser.parse_args()

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Workbook not found: {xlsx_path}")

    df = load_partner_sheet(xlsx_path)
    if df.empty:
        print("No valid ACT-* partner mappings found in PARTNER_MAPPING sheet.")
        return

    values_sql = ",\n                ".join(
        "(" + ", ".join([
            "'SentinelOne'",
            f"'{str(r.partner_name).replace("'", "''")}'",
            f"'{str(r.sf_id).replace("'", "''")}'",
            "NULL" if r.cms_id is None else f"'{str(r.cms_id).replace("'", "''")}'",
            "NULL" if r.zuora_name is None else f"'{str(r.zuora_name).replace("'", "''")}'",
            "NULL" if r.parent_company is None else f"'{str(r.parent_company).replace("'", "''")}'",
            f"'{str(args.source_tag).replace("'", "''")}'",
            "CURRENT_TIMESTAMP()",
        ]) + ")"
        for r in df.itertuples(index=False)
    )

    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )

    create_sql = f"""
        CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
            VENDOR VARCHAR,
            PARTNER_NAME VARCHAR,
            SF_ID VARCHAR,
            CMS_ID VARCHAR,
            ZUORA_NAME VARCHAR,
            PARENT_COMPANY VARCHAR,
            SOURCE_TAG VARCHAR,
            UPDATED_AT TIMESTAMP_NTZ
        )
    """

    merge_sql = f"""
        MERGE INTO {TARGET_TABLE} t
        USING (
            SELECT * FROM VALUES
                {values_sql}
            v(vendor, partner_name, sf_id, cms_id, zuora_name, parent_company, source_tag, updated_at)
        ) s
        ON UPPER(TRIM(t.vendor)) = UPPER(TRIM(s.vendor))
       AND UPPER(TRIM(t.partner_name)) = UPPER(TRIM(s.partner_name))
        WHEN MATCHED THEN UPDATE SET
            t.sf_id = s.sf_id,
            t.cms_id = COALESCE(s.cms_id, t.cms_id),
            t.zuora_name = COALESCE(s.zuora_name, t.zuora_name),
            t.parent_company = COALESCE(s.parent_company, t.parent_company),
            t.source_tag = s.source_tag,
            t.updated_at = s.updated_at
        WHEN NOT MATCHED THEN INSERT (
            vendor, partner_name, sf_id, cms_id, zuora_name, parent_company, source_tag, updated_at
        ) VALUES (
            s.vendor, s.partner_name, s.sf_id, s.cms_id, s.zuora_name, s.parent_company, s.source_tag, s.updated_at
        )
    """

    try:
        with conn.cursor() as cur:
            cur.execute(create_sql)
            cur.execute(merge_sql)
        conn.commit()
    finally:
        conn.close()

    print(f"Synced {len(df)} SentinelOne partner rows into {TARGET_TABLE}")


if __name__ == "__main__":
    main()
