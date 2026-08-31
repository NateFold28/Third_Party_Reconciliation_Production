"""Emit still-unmapped partner-months across all 9 vendors → CSV for seed-backfill triage.

Scope: rows where vendor usage has quantity/amount != 0 AND both the exact
UPPER(TRIM(partner_name)) join AND the normalized-key join against
RECON_PARTNER_MAP_MONTHLY / V_RECON_PARTNER_MAP_MONTHLY_NORM miss.

Output columns:
    VENDOR, BILLING_MONTH, VENDOR_PARTNER_NAME, USAGE_ROWS,
    TOTAL_QUANTITY, TOTAL_AMOUNT, EST_DOLLAR_IMPACT, SAMPLE_KEY

Sort: EST_DOLLAR_IMPACT DESC to surface highest-value gaps first.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

# Make repo root importable
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from TEMPLATES.Python.connection import get_snowflake_connection


VENDOR_SOURCES = [
    # (vendor_label, usage_table, vendor_filter_column, vendor_filter_value)
    ("Acronis",     "ACRONIS_USAGE",                       None,     None),
    ("Auvik",       "AUVIK_USAGE",                         None,     None),
    ("Bitdefender", "THIRD_PARTY_RECON_VENDOR_USAGE_PROD", "vendor", "Bitdefender"),
    ("ESET",        "ESET_USAGE",                          None,     None),
    ("Exium",       "EXIUM_USAGE",                         None,     None),
    ("KeepIT",      "KEEPIT_USAGE",                        None,     None),
    ("Proofpoint",  "PROOFPOINT_USAGE",                    None,     None),
    ("SentinelOne", "SENTINELONE_USAGE",                   None,     None),
    ("Webroot",     "WEBROOT_USAGE",                       None,     None),
]


def run() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    cur = conn.cursor()

    all_rows: list[tuple] = []

    for vendor, usage_table, filt_col, filt_val in VENDOR_SOURCES:
        if filt_col:
            vfilter = f"WHERE UPPER({filt_col}) = '{filt_val.upper()}'"
            amt_filter = "AND (COALESCE(quantity,0) <> 0 OR COALESCE(amount,0) <> 0)"
        else:
            vfilter = "WHERE (COALESCE(quantity,0) <> 0 OR COALESCE(amount,0) <> 0)"
            amt_filter = ""
        q = f"""
        WITH usage_scoped AS (
            SELECT
                billing_month::DATE                    AS billing_month,
                vendor_partner_name,
                UPPER(TRIM(vendor_partner_name))       AS exact_key,
                TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(vendor_partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS norm_key,
                COALESCE(quantity, 0)::FLOAT            AS quantity,
                COALESCE(amount,   0)::FLOAT            AS amount
            FROM {usage_table}
            {vfilter}
            {amt_filter}
        ),
        joined AS (
            SELECT
                u.*,
                pe.sf_id AS exact_sf_id,
                pn.sf_id AS norm_sf_id
            FROM usage_scoped u
            LEFT JOIN RECON_PARTNER_MAP_MONTHLY pe
                ON pe.billing_month = u.billing_month
               AND UPPER(TRIM(pe.partner_name)) = u.exact_key
            LEFT JOIN V_RECON_PARTNER_MAP_MONTHLY_NORM pn
                ON pn.billing_month = u.billing_month
               AND pn.PARTNER_NAME_NORMALIZED = u.norm_key
        )
        SELECT
            billing_month,
            vendor_partner_name,
            COUNT(*)                                        AS usage_rows,
            SUM(quantity)                                   AS total_quantity,
            SUM(amount)                                     AS total_amount,
            ANY_VALUE(exact_key)                            AS exact_key,
            ANY_VALUE(norm_key)                             AS norm_key
        FROM joined
        WHERE exact_sf_id IS NULL AND norm_sf_id IS NULL
          AND vendor_partner_name IS NOT NULL
          AND TRIM(vendor_partner_name) <> ''
        GROUP BY billing_month, vendor_partner_name
        """
        cur.execute(q)
        rows = cur.fetchall()
        for r in rows:
            billing_month, vpn, usage_rows, total_qty, total_amt, exact_key, norm_key = r
            est_dollar = float(total_amt or 0.0)
            all_rows.append((
                vendor,
                billing_month.isoformat() if billing_month else "",
                vpn,
                int(usage_rows or 0),
                float(total_qty or 0.0),
                float(total_amt or 0.0),
                est_dollar,
                norm_key or "",
            ))
        print(f"  {vendor:12s}: {len(rows):5d} unmapped partner-months")

    # Sort by EST_DOLLAR_IMPACT desc
    all_rows.sort(key=lambda r: -abs(r[6]))

    out_path = REPO_ROOT / "logs" / "unmapped_partner_months_for_seed_triage_20260830.csv"
    header = [
        "VENDOR", "BILLING_MONTH", "VENDOR_PARTNER_NAME", "USAGE_ROWS",
        "TOTAL_QUANTITY", "TOTAL_AMOUNT", "EST_DOLLAR_IMPACT", "NORMALIZED_KEY",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(all_rows)
    print()
    print(f"  total: {len(all_rows):,} unmapped partner-months")
    print(f"  total dollar exposure: ${sum(abs(r[6]) for r in all_rows):,.0f}")
    print(f"  wrote: {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    run()
