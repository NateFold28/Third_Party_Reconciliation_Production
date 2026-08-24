"""Build the missing vendor maps for Exium / SentinelOne / Webroot.

Source of truth:
  - MASTER_PARTNER_MAPPING_SEED.xlsx : 5,874 partners × (SF_ID, CMS_ID, ZUORA_NAME)
  - Manual recon workbooks: per-vendor vendor SKU ↔ CW SKU relationships

Writes two Snowflake tables:
  - RECON_MANUAL_SEED_PARTNER_MAP  (VENDOR, PARTNER_NAME, PARENT_COMPANY, SF_ID, CMS_ID, ZUORA_NAME)
  - RECON_MANUAL_SEED_SKU_MAP      (VENDOR, VENDOR_PRODUCT, VENDOR_SKU, CW_SKU, SKU_MATCH_KEY,
                                    MAPPING_NOTES, CONTRACT_COST_RATE, CW_RETAIL_RATE)

Then `sql/02_unified_reference_maps.sql` UNIONs these into RECON_PARTNER_MAP / RECON_SKU_MAP.
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection

MANUAL_ROOT = Path(r"C:\Users\Nate.Fold\OneDrive - ConnectWise, Inc\THIRD_PARTY_RECONCILIATION\Manual Recon Files 2026")

# -----------------------------------------------------------------------------
# 1. PARTNER MAP -- source: MASTER_PARTNER_MAPPING_SEED.xlsx
#    We use this universal master, tagged per vendor, ONLY for vendors whose
#    per-vendor V5 map is missing or incomplete (Exium has none; S1/Webroot
#    already match >84% but miss stragglers).
# -----------------------------------------------------------------------------
print(">> loading MASTER_PARTNER_MAPPING_SEED ...", flush=True)
master = pd.read_excel(
    MANUAL_ROOT / "MASTER_PARTNER_MAPPING_SEED.xlsx",
    sheet_name="MASTER_PARTNER_MAPPING_SEED",
)
master.columns = [str(c).strip() for c in master.columns]
master = master.rename(columns={"CMS ID": "CMS_ID"})
master = master[["PARTNER_NAME", "PARENT_COMPANY", "SF_ID", "CMS_ID", "ZUORA_NAME"]].copy()
# Normalize
for col in ("PARTNER_NAME", "PARENT_COMPANY", "SF_ID", "CMS_ID", "ZUORA_NAME"):
    master[col] = master[col].astype(str).where(master[col].notna(), None)
    master[col] = master[col].apply(lambda v: None if v in ("nan", "None", "") else str(v).strip())
master = master.dropna(subset=["PARTNER_NAME"]).drop_duplicates()
print(f"   master rows: {len(master):,}", flush=True)

partner_rows = []
for vendor in ("Exium", "SentinelOne", "Webroot"):
    for _, r in master.iterrows():
        partner_rows.append({
            "VENDOR": vendor,
            "PARTNER_NAME": r["PARTNER_NAME"],
            "PARENT_COMPANY": r["PARENT_COMPANY"],
            "SF_ID": r["SF_ID"],
            "CMS_ID": r["CMS_ID"],
            "ZUORA_NAME": r["ZUORA_NAME"],
        })
partner_df = pd.DataFrame(partner_rows)
print(f"   partner rows tagged for 3 vendors: {len(partner_df):,}", flush=True)


# -----------------------------------------------------------------------------
# 2. SKU MAP -- hand-curated per vendor from manual recon files + probe results.
#    The columns: VENDOR, VENDOR_PRODUCT, VENDOR_SKU, CW_SKU, SKU_MATCH_KEY,
#                 MAPPING_NOTES, CONTRACT_COST_RATE, CW_RETAIL_RATE
# -----------------------------------------------------------------------------

EXIUM_SKU_MAP = [
    # (VENDOR_SKU/PRODUCT, CW_SKU, SKU_MATCH_KEY, contract_cost, notes)
    ("EX-SIA",             "EXIUM_SIA",         "EXIUM_SIA",   5.50, "Secure Internet Access (per-agent)"),
    ("EX-SIA",             "EX-SIA",            "EXIUM_SIA",   5.50, "SIA legacy Zuora SKU"),
    ("EX-SPA",             "EXIUM_SPA",         "EXIUM_SPA",   5.50, "Secure Private Access (per-agent)"),
    ("EX-SPA",             "EX-SPA",            "EXIUM_SPA",   5.50, "SPA legacy Zuora SKU"),
    ("EX-CGW",             "EXIUM_CGW",         "EXIUM_CGW",  22.50, "Cyber Gateway existing"),
    ("EX-CGW",             "EX-CGW",            "EXIUM_CGW",  22.50, "CGW legacy Zuora SKU"),
    ("EX-CGW-NEW",         "EXIUM_CGW",         "EXIUM_CGW",  50.00, "New CGW pricing tier"),
    ("EX-SASE-PRO",        "EX-SASE-PRO",       "EXIUM_SASE_PRO",       8.75, "SASE Pro (bundled)"),
    ("EX-SASE-ESSENTIALS", "EX-SASE-ESSENTIALS","EXIUM_SASE_ESSENTIALS",5.50, "SASE Essentials (bundled)"),
    ("EX-XDR-7DAY",        "EX-XDR-7DAY",       "EXIUM_XDR",           None, "XDR 7-day retention (Zuora-only)"),
]

# SentinelOne: multiple CW SKU shapes rolling up to one SKU_MATCH_KEY per product family.
# Source: probe of Zuora vendor='SentinelOne' and manual recon workbook Data sheets.
SENTINELONE_SKU_MAP = [
    # Control family
    ("Control", "CU-3P-SAAS-S1Control",              "S1_CONTROL",  0.72, "Standalone Control"),
    ("Control", "SENTINELONE-CONTROL",               "S1_CONTROL",  0.72, "Legacy standalone Control"),
    ("Control", "TA-SENTINELONE-CONTROL",            "S1_CONTROL",  0.72, "Marketplace Control"),
    ("Control", "SwO-3P-CYBR-SOLP-SAAS-S1CNTROL",    "S1_CONTROL",  0.72, "Solution package Control"),
    ("Control", "CUSERVOTHR300520EPSB",              "S1_CONTROL",  0.72, "MDR bundle - Control tier"),
    ("Control", "M2MSEROTHR300520EPSB",              "S1_CONTROL",  0.72, "M2M MDR bundle - Control tier"),
    # Complete family
    ("Complete","CU-3P-SAASS1Complete",              "S1_COMPLETE", 1.01, "Standalone Complete"),
    ("Complete","SENTINELONE-COMPLETE",              "S1_COMPLETE", 1.01, "Legacy standalone Complete"),
    ("Complete","TA-SENTINELONE-COMPLETE",           "S1_COMPLETE", 1.01, "Marketplace Complete"),
    ("Complete","SwO-3P-CYBR-SOLP-SAAS-S1CMPLET",    "S1_COMPLETE", 1.01, "Solution package Complete"),
    ("Complete","CUSERVOTHRFFEPSECPRM",              "S1_COMPLETE", 1.01, "MDR premium bundle - Complete tier"),
    ("Complete","SwO-IH-CYBR-SIEM-SAAS-EPSECADV",    "S1_COMPLETE", 1.01, "SIEM Complete tier"),
    ("Complete","SwO_IH_CYBR_SIEM_SAAS_0520EPSB",    "S1_COMPLETE", 1.01, "SIEM Complete bundle"),
    # Ranger family
    ("Ranger",           "SP-RGR-ND",                "S1_RANGER",  0.38, "Ranger no-download"),
    ("Ranger Insights",  "SP-RGR-ND",                "S1_RANGER_INSIGHTS", 0.75, "Ranger Insights"),
    # RSO / recovery
    ("RSO",              "SP-RSO-ND-T1-C",           "S1_RSO",     None, "Recovery on RSO no-download T1"),
    # Data Retention family
    ("Data Retention - 30 Days",  "CU-PM-RT30-ND",   "S1_DR30",  0.09, "DR 30 days no-download"),
    ("Data Retention - 90 Days",  "CU-PM-RT90-ND",   "S1_DR90",  0.18, "DR 90 days no-download"),
    ("Data Retention - 180 Days", "CU-PM-RT1Y-ND",   "S1_DR180", 0.27, "DR 180 days (billed 1Y)"),
    ("Data Retention - 365 Days", "CU-PM-RT1Y-ND",   "S1_DR365", 0.36, "DR 365 days"),
    # Other product families
    ("Purple AI",           "PURPLE-AI",             "S1_PURPLE_AI",           0.50, "Purple AI (SKU not yet in Zuora)"),
    ("Forensics",           "FORENSICS",             "S1_FORENSICS",           0.30, "Forensics (SKU not yet in Zuora)"),
    ("Cloud Funnel",        "CLOUD-FUNNEL",          "S1_CLOUD_FUNNEL",        0.30, "Cloud Funnel (SKU not yet in Zuora)"),
    ("Threat Intelligence", "THREAT-INTEL",          "S1_THREAT_INTEL",        0.88, "Threat Intelligence (SKU not yet in Zuora)"),
    ("Ranger AD",           "RANGER-AD",             "S1_RANGER_AD",           0.55, "Ranger AD (SKU not yet in Zuora)"),
    ("Singularity Identity","SINGULARITY-IDENTITY",  "S1_SINGULARITY_IDENTITY",0.95, "Singularity Identity (SKU not yet in Zuora)"),
    ("Core",                "CORE",                  "S1_CORE",                None, "Core (SKU not yet in Zuora)"),
]

# Webroot: 3 vendor SKUs to many CW SKUs across per-tier billing shapes.
# Source: probe of Zuora vendor='Webroot' and manual recon Data sheets.
WEBROOT_SKU_MAP = [
    # GSM (endpoint protection) - tiered pricing
    ("GSM", "WRSECGSM10",                        "WEBROOT_GSM", 0.85, "GSM 10-49 endpoints"),
    ("GSM", "WRSECGSM100",                       "WEBROOT_GSM", 0.79, "GSM 100-249"),
    ("GSM", "WRSECGSM250",                       "WEBROOT_GSM", 0.72, "GSM 250-499"),
    ("GSM", "WRSECGSM500",                       "WEBROOT_GSM", 0.65, "GSM 500-999"),
    ("GSM", "WRSECGSM1000",                      "WEBROOT_GSM", 0.55, "GSM 1000+"),
    ("GSM", "SEWRSGSM10",                        "WEBROOT_GSM", 0.85, "SE-tier GSM 10-49"),
    ("GSM", "SEWRSGSM100",                       "WEBROOT_GSM", 0.79, "SE-tier GSM 100-249"),
    ("GSM", "SEWRSGSM250",                       "WEBROOT_GSM", 0.72, "SE-tier GSM 250-499"),
    ("GSM", "SEWRSGSM2500",                      "WEBROOT_GSM", 0.45, "SE-tier GSM 2500+"),
    ("GSM", "CU-WEBROOT-EPP-RMM",                "WEBROOT_GSM", 0.42, "EPP bundled with RMM"),
    ("GSM", "3P-SAAS3002315EPPRMM",              "WEBROOT_GSM", 0.42, "EPP RMM 3rd-party SaaS"),
    ("GSM", "3RDPARTYSAASIITBUEPP",              "WEBROOT_GSM", 0.42, "3rd-party SaaS EPP legacy"),
    ("GSM", "CW-RMM-WR-EEP-OVERAG",              "WEBROOT_GSM", 0.42, "RMM overage bill"),
    ("GSM", "3P-SAAS30020015PARNT",              "WEBROOT_GSM", 0.55, "Parent-level GSM SaaS bill"),
    # DNS Protection
    ("DNS", "WSADNSP-STAND-ALONE",               "WEBROOT_DNS", 0.53, "DNS standalone"),
    ("DNS", "3P-SAAS30021010FFDNS",              "WEBROOT_DNS", 0.53, "DNS 3rd-party SaaS"),
    # SAT Security Awareness Training
    ("SAT", "SEWRSSAT10",                        "WEBROOT_SAT", 0.35, "SAT 10-49"),
    ("SAT", "3P-SAAS30022019FFSAT",              "WEBROOT_SAT", 0.35, "SAT 3rd-party SaaS"),
]

sku_rows = []
for vendor, entries in (
    ("Exium", EXIUM_SKU_MAP),
    ("SentinelOne", SENTINELONE_SKU_MAP),
    ("Webroot", WEBROOT_SKU_MAP),
):
    for row in entries:
        vendor_prod, cw_sku, match_key, cost, notes = row
        sku_rows.append({
            "VENDOR": vendor,
            "VENDOR_PRODUCT": vendor_prod,
            "VENDOR_SKU": vendor_prod,       # for these vendors, product and sku are the same field
            "CW_SKU": cw_sku,
            "SKU_MATCH_KEY": match_key,
            "MAPPING_NOTES": notes,
            "CONTRACT_COST_RATE": cost,
            "CW_RETAIL_RATE": None,
        })
sku_df = pd.DataFrame(sku_rows)
print(f"   sku rows total: {len(sku_df):,}", flush=True)

# -----------------------------------------------------------------------------
# 3. Load both tables into Snowflake
# -----------------------------------------------------------------------------
conn = get_snowflake_connection(
    role="DEVELOPER", warehouse="REPORTING_WH",
    database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
)
c = conn.cursor()

print(">> creating RECON_MANUAL_SEED_PARTNER_MAP ...", flush=True)
c.execute("DROP TABLE IF EXISTS RECON_MANUAL_SEED_PARTNER_MAP")
c.execute("""
    CREATE TABLE RECON_MANUAL_SEED_PARTNER_MAP (
        VENDOR         STRING,
        PARTNER_NAME   STRING,
        PARENT_COMPANY STRING,
        SF_ID          STRING,
        CMS_ID         STRING,
        ZUORA_NAME     STRING
    )
""")
partner_df = partner_df.where(pd.notna(partner_df), None)
rows = [tuple(r) for r in partner_df[
    ["VENDOR","PARTNER_NAME","PARENT_COMPANY","SF_ID","CMS_ID","ZUORA_NAME"]
].to_records(index=False)]
# use executemany
BATCH = 5000
for i in range(0, len(rows), BATCH):
    c.executemany(
        "INSERT INTO RECON_MANUAL_SEED_PARTNER_MAP VALUES (%s,%s,%s,%s,%s,%s)",
        rows[i:i+BATCH],
    )
    print(f"   inserted {min(i+BATCH, len(rows)):,} / {len(rows):,}", flush=True)
c.execute("SELECT COUNT(*) FROM RECON_MANUAL_SEED_PARTNER_MAP")
print(f"   RECON_MANUAL_SEED_PARTNER_MAP row count = {c.fetchone()[0]:,}", flush=True)

print(">> creating RECON_MANUAL_SEED_SKU_MAP ...", flush=True)
c.execute("DROP TABLE IF EXISTS RECON_MANUAL_SEED_SKU_MAP")
c.execute("""
    CREATE TABLE RECON_MANUAL_SEED_SKU_MAP (
        VENDOR             STRING,
        VENDOR_PRODUCT     STRING,
        VENDOR_SKU         STRING,
        CW_SKU             STRING,
        SKU_MATCH_KEY      STRING,
        MAPPING_NOTES      STRING,
        CONTRACT_COST_RATE FLOAT,
        CW_RETAIL_RATE     FLOAT
    )
""")
sku_df = sku_df.where(pd.notna(sku_df), None)
sku_rows_tup = []
for _, r in sku_df.iterrows():
    def clean(v):
        if v is None:
            return None
        if isinstance(v, float) and pd.isna(v):
            return None
        return v
    sku_rows_tup.append((
        clean(r["VENDOR"]), clean(r["VENDOR_PRODUCT"]), clean(r["VENDOR_SKU"]),
        clean(r["CW_SKU"]), clean(r["SKU_MATCH_KEY"]), clean(r["MAPPING_NOTES"]),
        clean(r["CONTRACT_COST_RATE"]), clean(r["CW_RETAIL_RATE"]),
    ))
c.executemany(
    "INSERT INTO RECON_MANUAL_SEED_SKU_MAP VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
    sku_rows_tup,
)
c.execute("SELECT COUNT(*) FROM RECON_MANUAL_SEED_SKU_MAP")
print(f"   RECON_MANUAL_SEED_SKU_MAP row count = {c.fetchone()[0]:,}", flush=True)

print("\n>> Per-vendor counts:")
for tbl, key in (
    ("RECON_MANUAL_SEED_PARTNER_MAP","VENDOR"),
    ("RECON_MANUAL_SEED_SKU_MAP","VENDOR"),
):
    c.execute(f"SELECT {key}, COUNT(*) FROM {tbl} GROUP BY 1 ORDER BY 1")
    print(f"   {tbl}:")
    for r in c.fetchall():
        print(f"     {r[0]:<15} {r[1]:>7,}")

conn.close()
print("\n>> DONE. Now run scripts/_build_unified_maps.py to fold into RECON_PARTNER_MAP + RECON_SKU_MAP.")
