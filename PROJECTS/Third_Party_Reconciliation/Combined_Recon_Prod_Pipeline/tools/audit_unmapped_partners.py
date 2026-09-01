"""
audit_unmapped_partners.py — show ALL partner names in vendor usage files
that currently have no match in THIRD_PARTY_RECON_PARTNER_MAP_PROD.

Run this before editing the mapping table so you can fix everything in one
Snowsight session, then rerun the pipeline to pick up all your changes at once.

Usage:
    python PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline\tools\audit_unmapped_partners.py

Output:
    - Full list of unmapped partner names per vendor (sorted by seat volume)
    - Summary count
    - Snowsight-ready INSERT template you can paste into the mapping table

Excludes:
    - Known CW internal test accounts (CW DEV *, CONTINUUM-TEST, etc.)
    - Accounts you have already mapped (they match RECON_PARTNER_MAP)
"""
from __future__ import annotations
import sys
sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe  # noqa: E402

# CW-internal test accounts that should never appear as real partner mapping candidates.
# These are filtered at ingestion in SentinelOne SQL and globally here for audit clarity.
INTERNAL_ACCOUNT_PREFIXES = ("CW DEV", "CW-DEV")
INTERNAL_ACCOUNT_EXACT = {
    "CONTINUUM-TEST", "CW AUTOMATE", "PMT-TEST", "MP-AMARTEST1",
    "MRGA", "NJTECH", "RUSHAB", "MAHESH-TEST", "SAHIL",
    "TEAM-40-AI-TEST", "CAISOFTWARE-COVID731ACCESS", "BCDR NOC",
    "SECURENETWORKS (PALMETTO TECH)", "JD_ELITESUPPORT", "TISDALE_DEMO",
    "ONENET", "RD_ELITESUPPORT", "PARROINFODEVELOPPEMENT",
    # Webroot/KeepIT internal test + platform accounts
    "COMMANDQA", "HARESHN", "CWPRODTESTPARTNER", "WEBROOTINTEGRATION",
}

SQL = """
WITH vendor_names AS (
    -- All distinct partner names in the raw vendor usage file,
    -- with their total seat volume (so we can triage high-volume first)
    SELECT
        VENDOR,
        UPPER(TRIM(VENDOR_PARTNER_NAME)) AS partner_upper,
        VENDOR_PARTNER_NAME              AS partner_raw,
        SUM(QUANTITY)                    AS total_seats,
        COUNT(DISTINCT BILLING_MONTH)    AS months_active
    FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD
    WHERE VENDOR_PARTNER_NAME IS NOT NULL
      AND TRIM(VENDOR_PARTNER_NAME) != ''
      AND QUANTITY > 0
    GROUP BY 1, 2, 3
),
mapped AS (
    -- Names that already resolve via the current mapping table
    SELECT UPPER(TRIM(partner_name)) AS partner_upper
    FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_PARTNER_MAP_PROD
    UNION
    SELECT UPPER(TRIM(partner_name)) AS partner_upper
    FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_VENDOR_PARTNER_MANUAL_MAP
)
SELECT
    v.VENDOR,
    v.partner_raw              AS VENDOR_PARTNER_NAME,
    v.total_seats              AS TOTAL_SEATS,
    v.months_active            AS MONTHS_ACTIVE
FROM vendor_names v
LEFT JOIN mapped m ON m.partner_upper = v.partner_upper
WHERE m.partner_upper IS NULL
  -- Exclude known pipe-concatenated multi-account strings (SentinelOne aggregated unmapped)
  AND NOT CONTAINS(v.partner_raw, ' | ')
ORDER BY v.VENDOR, v.total_seats DESC
"""


def is_internal(name: str) -> bool:
    u = name.upper().strip()
    if u in INTERNAL_ACCOUNT_EXACT:
        return True
    for prefix in INTERNAL_ACCOUNT_PREFIXES:
        if u.startswith(prefix):
            return True
    return False


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        print("[audit] querying unmapped partners across all vendor usage files ...")
        df = fetch_dataframe(SQL, conn=conn)
    finally:
        conn.close()

    if df.empty:
        print("\n✓ No unmapped partners found — every vendor partner name resolves to a mapping.")
        return 0

    # Split: real candidates vs known internal
    df["IS_INTERNAL"] = df["VENDOR_PARTNER_NAME"].apply(is_internal)
    real = df[~df["IS_INTERNAL"]].reset_index(drop=True)
    internal = df[df["IS_INTERNAL"]].reset_index(drop=True)

    print(f"\n{'='*80}")
    print(f"  UNMAPPED PARTNER AUDIT — {len(real)} actionable ({len(internal)} internal/test suppressed)")
    print(f"{'='*80}\n")

    if real.empty:
        print("✓ No actionable unmapped partners. Only internal/test accounts are unresolved.")
    else:
        current_vendor = None
        for _, row in real.iterrows():
            if row["VENDOR"] != current_vendor:
                current_vendor = row["VENDOR"]
                print(f"\n── {current_vendor} ──")
            seats = int(row["TOTAL_SEATS"])
            months = int(row["MONTHS_ACTIVE"])
            print(f"  {row['VENDOR_PARTNER_NAME']:<55}  {seats:>8,} seats  {months:>2} months")

        print(f"\n{'─'*80}")
        print(f"  Total actionable unmapped: {len(real)}")
        print(f"\n  To fix: add the above partner names to THIRD_PARTY_RECON_PARTNER_MAP_PROD")
        print(f"  in Snowsight, then rerun the pipeline. Every pipeline run will pick up")
        print(f"  new entries automatically.\n")

        # Snowsight INSERT template
        print("  Snowsight INSERT template (fill in SF_ID, CMS_ID, ZUORA_NAME):")
        print(f"  {'─'*76}")
        for _, row in real.iterrows():
            name_esc = row["VENDOR_PARTNER_NAME"].replace("'", "''")
            print(f"  INSERT INTO ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_PARTNER_MAP_PROD")
            print(f"    (PARTNER_NAME, SF_ID, CMS_ID, ZUORA_NAME, PARENT_COMPANY)")
            print(f"  VALUES ('{name_esc}', '<SF_ID>', <CMS_ID>, '<ZUORA_NAME>', NULL);")

    if not internal.empty:
        print(f"\n  Suppressed internal/test accounts ({len(internal)}) — no action needed:")
        for _, row in internal.iterrows():
            print(f"    {row['VENDOR']:<14} {row['VENDOR_PARTNER_NAME']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
