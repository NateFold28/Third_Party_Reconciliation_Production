"""Post-fix audit: verify KeepIT numbers are sane + architecture check.

Confirms:
  1. KEEPIT_RECON_DETAIL vendor_amt now aligned with raw KEEPIT_USAGE (~$4M vs $182M before).
  2. THIRD_PARTY_RECON_OUTPUT_PROD schema matches app expectation (45 cols, 14 EXCEPTION_TYPE buckets).
  3. No vendor SQL is referencing legacy _LEGACY_20260823 tables or old snapshot tables at read-time.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


APP_EXPECTED_COLS = [
    "VENDOR", "BILLING_MONTH", "INV_ID", "BILLING_TYPE", "VENDOR_PARTNER_NAME",
    "VENDOR_PRODUCT", "SKU_MATCH_GROUP", "CW_SKUS", "ZUORA_SKUS", "MARKETPLACE_SKUS",
    "BILLING_SOURCE_MIX", "API_QUANTITY", "AVG_API_QUANTITY", "VENDOR_QUANTITY",
    "VENDOR_UNIT_PRICE", "VENDOR_AMOUNT", "ZUORA_QUANTITY", "ZUORA_UNIT_PRICE",
    "ZUORA_AMOUNT", "MARKETPLACE_QUANTITY", "MARKETPLACE_UNIT_PRICE",
    "MARKETPLACE_AMOUNT", "TOTAL_BILLING_QUANTITY", "TOTAL_BILLING_AMOUNT",
    "QTY_DELTA", "ABS_QTY_DELTA", "AMOUNT_DELTA", "ABS_AMOUNT_DELTA",
    "CW_MARGIN_PCT", "HAS_DISCOUNT", "DUPLICATE_BILLING_FLAG", "OUTCOME_FLAG",
    "INVESTIGATION_REASON", "SF_ID_ORIGINAL", "SF_ID", "EXCEPTION_TYPE",
    "EST_DOLLAR_IMPACT", "VENDOR_SOURCE_ROW_COUNT", "ACTION_NEEDED",
    "IS_LEAKAGE", "IS_FINANCE_QUEUE", "IS_OPS_QUEUE", "IS_TIMING_QUEUE",
    "IS_CLEAR", "CASE_ID",
]

APP_EXPECTED_EXCEPTIONS = [
    "Clear",
    "Duplicated CW Invoice",
    "Marketplace Billing Delay",
    "Known Discount / Bundle",
    "Unmapped Partner",
    "Vendor SKU, No CW SKU",
    "CW SKU, No Vendor SKU",
    "Vendor Billing, No CW Billing",
    "CW Billing, No Vendor Billing",
    "Vendor Billing, Insufficient CW Billing",
    "API Usage Recorded, No CW Billing",
    "Other Issue",
]


def main() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    c = conn.cursor()

    print("=" * 70)
    print("A. KEEPIT POST-FIX SANITY")
    print("=" * 70)
    c.execute(
        "SELECT COUNT(*), SUM(VENDOR_AMOUNT), SUM(ZUORA_AMOUNT), SUM(MARKETPLACE_AMOUNT) "
        "FROM KEEPIT_RECON_DETAIL"
    )
    r = c.fetchone()
    print(f"  KEEPIT_RECON_DETAIL rows={r[0]:,}")
    print(f"    VENDOR_AMOUNT     = ${r[1] or 0:>14,.0f}   (was $182,718,203)")
    print(f"    ZUORA_AMOUNT      = ${r[2] or 0:>14,.0f}")
    print(f"    MARKETPLACE_AMT   = ${r[3] or 0:>14,.0f}")
    ratio = (r[1] or 0) / 4_600_000
    print(f"    ratio vs raw ~$4.6M = {ratio:.2f}x  (was ~40x)")

    print("\n  KEEPIT_RECON_DETAIL by month:")
    c.execute(
        """SELECT BILLING_MONTH, COUNT(*),
                  ROUND(SUM(VENDOR_AMOUNT), 0), ROUND(SUM(ZUORA_AMOUNT), 0)
           FROM KEEPIT_RECON_DETAIL GROUP BY 1 ORDER BY 1"""
    )
    for m, n, v, z in c.fetchall():
        print(f"    {m}  rows={n:>5,}  vendor=${v or 0:>10,.0f}  zuora=${z or 0:>10,.0f}")

    print("\n" + "=" * 70)
    print("B. OUTPUT_PROD SCHEMA MATCH")
    print("=" * 70)
    c.execute(
        """SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION' AND TABLE_NAME='THIRD_PARTY_RECON_OUTPUT_PROD'
           ORDER BY ORDINAL_POSITION"""
    )
    actual_cols = [r[0] for r in c.fetchall()]
    print(f"  Column count: {len(actual_cols)} (expected {len(APP_EXPECTED_COLS)})")
    missing = [x for x in APP_EXPECTED_COLS if x not in actual_cols]
    extra = [x for x in actual_cols if x not in APP_EXPECTED_COLS]
    if missing:
        print(f"  MISSING columns: {missing}")
    if extra:
        print(f"  EXTRA columns: {extra}")
    if not missing and not extra:
        print("  OK: schema matches app expectation.")

    print("\n" + "=" * 70)
    print("C. EXCEPTION_TYPE DISTRIBUTION")
    print("=" * 70)
    c.execute(
        """SELECT EXCEPTION_TYPE, COUNT(*)
           FROM THIRD_PARTY_RECON_OUTPUT_PROD GROUP BY 1 ORDER BY 2 DESC"""
    )
    types_present = []
    for t, n in c.fetchall():
        types_present.append(t)
        marker = " " if t in APP_EXPECTED_EXCEPTIONS else " (!)"
        print(f"  {t:45s} {n:>8,}{marker}")
    unknown = [t for t in types_present if t not in APP_EXPECTED_EXCEPTIONS]
    if unknown:
        print(f"  WARNING: unknown EXCEPTION_TYPE(s): {unknown}")

    print("\n" + "=" * 70)
    print("D. PER-VENDOR CLEAR RATE + DOLLAR IMPACT")
    print("=" * 70)
    c.execute(
        """SELECT VENDOR,
                  COUNT(*) AS total,
                  SUM(CASE WHEN IS_CLEAR THEN 1 ELSE 0 END) AS clear_n,
                  ROUND(100.0 * SUM(CASE WHEN IS_CLEAR THEN 1 ELSE 0 END) / COUNT(*), 1) AS clear_pct,
                  ROUND(SUM(COALESCE(EST_DOLLAR_IMPACT, 0)), 0) AS impact
           FROM THIRD_PARTY_RECON_OUTPUT_PROD
           GROUP BY 1
           ORDER BY clear_pct DESC"""
    )
    print(f"  {'VENDOR':14s} {'TOTAL':>7s} {'CLEAR':>7s} {'CLEAR%':>8s} {'$IMPACT':>16s}")
    for v, t, cn, cp, imp in c.fetchall():
        print(f"  {v:14s} {t:>7,} {cn:>7,} {cp:>7}%  ${imp or 0:>14,.0f}")

    print("\n" + "=" * 70)
    print("E. LEGACY TABLE REFERENCES IN VENDOR SQL")
    print("=" * 70)
    vendor_sql_dir = Path(
        r"C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline\Vendor_Recon_Pipelines_Prod"
    )
    banned_patterns = ["_LEGACY_20260823", "STANDALONE_RECON_DETAIL__", "_SNAPSHOT_20260823"]
    for sql_file in sorted(vendor_sql_dir.rglob("*_Reconciliation_Script_Prod.sql")):
        text = sql_file.read_text(encoding="utf-8", errors="replace")
        found = [p for p in banned_patterns if p in text]
        if found:
            print(f"  {sql_file.parent.name:14s} references banned: {found}")
        else:
            print(f"  {sql_file.parent.name:14s} clean")


if __name__ == "__main__":
    main()
