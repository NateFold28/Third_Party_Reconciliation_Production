"""
_run_skeleton_pipeline.py -- CLEAN end-to-end reconciliation skeleton.

Architecture (the "one big table" you asked for):

    ingestion (9 py scripts)  ->  THIRD_PARTY_RECON_VENDOR_USAGE_PROD
    billing sources           ->  THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
                                  THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
                                  THIRD_PARTY_RECON_SOURCE_TRT_PROD

    9 vendor emit blocks      ->  INSERT INTO THIRD_PARTY_RECON_DETAIL_PROD
                                  (34 canonical columns, 12 canonical OUTCOME_FLAG values)

    classifier                ->  THIRD_PARTY_RECON_OUTPUT_PROD (45 cols, adds
                                  EXCEPTION_TYPE + queue flags + CASE_ID)
                                  + THIRD_PARTY_RECON_SUMMARY_PROD

    app                       ->  reads OUTPUT_PROD + SUMMARY only

No STANDALONE reads in the app path. No TRANSLATIONS dict. No per-vendor
_RECON_DETAIL_PROD sprawl. No union step.

Tonight's source for every vendor's emit block is the frozen SNAPSHOT from
2026-08-23 (Phase 0 preservation). This is intentional: it lets us prove the
architecture works end-to-end without touching the 1000-line vendor SQL files.

Tomorrow's fine-tune: for each vendor, flip its emit block from
    SOURCE = "<VENDOR>_SNAPSHOT_20260823"
to
    1. run Vendor_Recon_Pipelines_Prod/<VENDOR>/<VENDOR>_Reconciliation_Script_Prod.sql
    2. SOURCE = "<VENDOR>_RECON_DETAIL" (the vendor SQL's own output table)
Everything downstream stays identical.
"""
from __future__ import annotations
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline")
sys.path.insert(0, r"C:\Users\Nate.Fold\projects")

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

USE = (
    "USE ROLE DEVELOPER; "
    "USE WAREHOUSE REPORTING_WH; "
    "USE DATABASE ANALYTICS_DEV; "
    "USE SCHEMA DBT_NFOLD_TRANSFORMATION;"
)

# ---------------------------------------------------------------------------
# VENDOR ROUTING
#
# For each vendor we choose one of two paths into THIRD_PARTY_RECON_DETAIL_PROD:
#
#   "live"      -> execute Vendor_Recon_Pipelines_Prod/<VENDOR>/<VENDOR>_Reconciliation_Script_Prod.sql
#                  (which rebuilds <VENDOR>_RECON_DETAIL) and emit from that
#                  table. This is what production will use for every vendor.
#
#   "snapshot"  -> emit from THIRD_PARTY_STANDALONE_RECON_DETAIL__<V>_SNAPSHOT_20260823
#                  (frozen 2026-08-23 Phase 0 backup). Used for vendors whose
#                  live SQL still needs calibration.
#
# Flip a vendor from "snapshot" to "live" when its numbers are cleaned up.
# ---------------------------------------------------------------------------
VENDOR_ROUTING: dict[str, tuple[str, str]] = {
    # vendor        : (mode, source)
    "Proofpoint":  ("live", "PROOFPOINT_RECON_DETAIL"),
    "Bitdefender": ("live", "BITDEFENDER_RECON_DETAIL"),
    "Acronis":     ("live", "ACRONIS_RECON_DETAIL"),
    "Auvik":       ("live", "AUVIK_RECON_DETAIL"),
    "ESET":        ("live", "ESET_RECON_DETAIL"),
    "Exium":       ("live", "EXIUM_RECON_DETAIL"),
    "KeepIT":      ("live", "KEEPIT_RECON_DETAIL"),
    "SentinelOne": ("live", "SENTINELONE_RECON_DETAIL"),
    "Webroot":     ("live", "WEBROOT_RECON_DETAIL"),
}

# If a vendor's live path fails, fall back to this snapshot so the app still
# has data for that vendor. Set to None to disable fallback (fail hard).
# 2026-08-23: All 9 vendors run live. Fallback disabled so any live-path
# regression surfaces loudly instead of being silently masked by snapshot data.
VENDOR_FALLBACK: dict[str, str] = {}

# ---------------------------------------------------------------------------
# CANONICAL EMIT: every vendor uses this exact shape. 34 columns, in the
# order that matches THIRD_PARTY_RECON_DETAIL_PROD. OUTCOME_FLAG values that
# aren't already canonical are folded into canonical here (SNAPSHOT tables
# already carry canonical values, but the CASE also handles vendor-slang
# left over from any prior run).
# ---------------------------------------------------------------------------
CANONICAL_OUTCOME_FLAG_NORMALIZATION = """
    CASE
        WHEN OUTCOME_FLAG IN (
            'Clear', 'Unmapped Partner', 'Duplicated CW Invoice',
            'Marketplace Billing Delay', 'Known Discount / Bundle',
            'API Usage Recorded, No CW Billing', 'Vendor SKU, No CW SKU',
            'CW SKU, No Vendor SKU', 'Vendor Billing, No CW Billing',
            'CW Billing, No Vendor Billing',
            'Vendor Billing, Insufficient CW Billing', 'Other Issue'
        ) THEN OUTCOME_FLAG
        WHEN OUTCOME_FLAG IN ('CLEAR','MATCHED','MINOR_DRIFT',
                              'NEGLIGIBLE_DOLLAR_EXPOSURE','MARKETPLACE_ONLY_CLEAR',
                              'NO_ACTIVITY','OVERAGE_EXPECTED','MATERIAL_OVER_VENDOR',
                              'BILLING_DIFFERENTIAL_OVER','MARKETPLACE_OVERAGE',
                              'BILLING_OVER_VENDOR','Overage',
                              'Clear - Discounted / Bundled') THEN 'Clear'
        WHEN OUTCOME_FLAG IN ('MARKETPLACE_TIMING','BILLING_TIMING_ADJACENT_MONTH')
            THEN 'Marketplace Billing Delay'
        WHEN OUTCOME_FLAG IN ('PARTNER_MAPPING_REQUIRED','Unmapped SKU')
             AND (SF_ID IS NULL OR UPPER(TRIM(COALESCE(SF_ID,''))) IN ('','UNKNOWN','NONE','UNMAPPED','NULL'))
            THEN 'Unmapped Partner'
        WHEN OUTCOME_FLAG IN ('VENDOR_ADDON_NO_CW_SKU','VENDOR_PRODUCT_NO_CW_SKU',
                              'VENDOR_SKU_NO_CW_SKU','SKU_MISMATCH_BILLING_ON_OTHER_SKU',
                              'Unmapped SKU','PARTNER_MAPPING_REQUIRED')
            THEN 'Vendor SKU, No CW SKU'
        WHEN OUTCOME_FLAG IN ('CW_ONLY_ADDON_NO_VENDOR','CW_SKU_NO_VENDOR_SKU')
            THEN 'CW SKU, No Vendor SKU'
        WHEN OUTCOME_FLAG IN ('DUPLICATE_BILLING','Duplicate Billing')
            THEN 'Duplicated CW Invoice'
        WHEN OUTCOME_FLAG IN ('RMM_DISCOUNTED','KNOWN_DISCOUNT_BUNDLE','MDR_BUNDLE',
                              'CW_INCLUDED_ZERO_DOLLAR','INTENTIONAL_DISCOUNT')
            THEN 'Known Discount / Bundle'
        WHEN OUTCOME_FLAG IN ('TRT_VENDOR_USAGE_NOT_BILLED',
                              'STRUCTURAL_VENDOR_ONLY_TRT_CONFIRMED',
                              'Missing CW Billing - API Confirmed')
            THEN 'API Usage Recorded, No CW Billing'
        WHEN OUTCOME_FLAG IN ('STRUCTURAL_BILLING_ONLY','BILLING_ONLY_NO_VENDOR_USAGE',
                              'STRUCTURAL_BILLING_ONLY_TRT_CONFIRMED',
                              'MARKETPLACE_BILLING_NO_VENDOR',
                              'Billed by CW, Missing Vendor Billing')
             AND COALESCE(VENDOR_AMOUNT,0) = 0
            THEN 'CW Billing, No Vendor Billing'
        WHEN OUTCOME_FLAG IN ('STRUCTURAL_VENDOR_ONLY_NO_CONTRACT','NO_BILLING_NO_HISTORY',
                              'MAPPED_ADDON_NO_CURRENT_BILLING',
                              'KNOWN_GOOD_MAPPING_MISSING_CURRENT_BILLING',
                              'CONTRACT_TIMING_OR_INACTIVE',
                              'TAKEOUT_SUPPORT_FILE_NO_DIRECT_BILLING',
                              'CARR_SECONDARY_CHECK_ONLY',
                              'Billed by Vendor, Missing CW Billing')
             AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0
            THEN 'Vendor Billing, No CW Billing'
        WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER',
                              'VENDOR_OVER_BILLING','ACCOUNT_HAS_OTHER_S1_BILLING_NO_MATCH',
                              'Vendor Billing > CW Billing')
             AND COALESCE(VENDOR_AMOUNT,0) > COALESCE(TOTAL_BILLING_AMOUNT,0) * 1.25
            THEN 'Vendor Billing, Insufficient CW Billing'
        WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER',
                              'VENDOR_OVER_BILLING','ACCOUNT_HAS_OTHER_S1_BILLING_NO_MATCH',
                              'Vendor Billing > CW Billing')
             AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0
            THEN 'Vendor Billing, No CW Billing'
        WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER',
                              'VENDOR_OVER_BILLING','ACCOUNT_HAS_OTHER_S1_BILLING_NO_MATCH',
                              'Vendor Billing > CW Billing')
            THEN 'Clear'
        ELSE 'Other Issue'
    END
""".strip()


def emit_vendor_block(vendor: str, source_table: str) -> str:
    """Return the SQL that publishes one vendor's rows into DETAIL_PROD.

    Contract: source_table must have the STANDALONE-shape columns:
      BILLING_MONTH, SF_ID, ZUORA_INV/MP_INV, VENDOR_PARTNER_NAME, VENDOR_PRODUCT,
      SKU_MATCH_GROUP, CW_SKUS, ZUORA_SKUS, MARKETPLACE_SKUS, BILLING_SOURCE_MIX,
      VENDOR_QUANTITY, VENDOR_UNIT_PRICE, VENDOR_AMOUNT,
      ZUORA_QUANTITY, ZUORA_UNIT_PRICE, ZUORA_AMOUNT,
      MARKETPLACE_QUANTITY, MARKETPLACE_AMOUNT,
      TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT,
      QTY_DELTA, ABS_QTY_DELTA, AMOUNT_DELTA, ABS_AMOUNT_DELTA,
      DUPLICATE_BILLING_FLAG (bool), OUTCOME_FLAG, INVESTIGATION_REASON.

    Both the frozen SNAPSHOT tables and any future vendor's own <V>_RECON_DETAIL
    that follows this contract can be plugged in with no other changes.
    """
    return f"""{USE}

-- Wipe any prior rows for this vendor so this run is idempotent.
DELETE FROM THIRD_PARTY_RECON_DETAIL_PROD WHERE VENDOR = '{vendor}';

INSERT INTO THIRD_PARTY_RECON_DETAIL_PROD (
    VENDOR, BILLING_MONTH, SF_ID, INV_ID, BILLING_TYPE,
    VENDOR_PARTNER_NAME, VENDOR_PRODUCT, SKU_MATCH_GROUP,
    CW_SKUS, ZUORA_SKUS, MARKETPLACE_SKUS, BILLING_SOURCE_MIX,
    API_QUANTITY, AVG_API_QUANTITY,
    VENDOR_QUANTITY, VENDOR_UNIT_PRICE, VENDOR_AMOUNT,
    ZUORA_QUANTITY, ZUORA_UNIT_PRICE, ZUORA_AMOUNT,
    MARKETPLACE_QUANTITY, MARKETPLACE_UNIT_PRICE, MARKETPLACE_AMOUNT,
    TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT,
    QTY_DELTA, ABS_QTY_DELTA, AMOUNT_DELTA, ABS_AMOUNT_DELTA,
    CW_MARGIN_PCT, HAS_DISCOUNT, DUPLICATE_BILLING_FLAG,
    OUTCOME_FLAG, INVESTIGATION_REASON
)
SELECT
    '{vendor}'::VARCHAR                                                       AS VENDOR,
    BILLING_MONTH::DATE                                                       AS BILLING_MONTH,
    SF_ID                                                                     AS SF_ID,
    COALESCE(ZUORA_INV, MP_INV)                                               AS INV_ID,
    NULL::VARCHAR                                                             AS BILLING_TYPE,
    VENDOR_PARTNER_NAME                                                       AS VENDOR_PARTNER_NAME,
    VENDOR_PRODUCT                                                            AS VENDOR_PRODUCT,
    SKU_MATCH_GROUP                                                           AS SKU_MATCH_GROUP,
    CW_SKUS                                                                   AS CW_SKUS,
    ZUORA_SKUS                                                                AS ZUORA_SKUS,
    MARKETPLACE_SKUS                                                          AS MARKETPLACE_SKUS,
    BILLING_SOURCE_MIX                                                        AS BILLING_SOURCE_MIX,
    NULL::FLOAT                                                               AS API_QUANTITY,        -- backfilled below
    NULL::FLOAT                                                               AS AVG_API_QUANTITY,    -- backfilled below
    COALESCE(VENDOR_QUANTITY, 0)::FLOAT                                       AS VENDOR_QUANTITY,
    VENDOR_UNIT_PRICE::FLOAT                                                  AS VENDOR_UNIT_PRICE,
    COALESCE(VENDOR_AMOUNT, 0)::FLOAT                                         AS VENDOR_AMOUNT,
    COALESCE(ZUORA_QUANTITY, 0)::FLOAT                                        AS ZUORA_QUANTITY,
    ZUORA_UNIT_PRICE::FLOAT                                                   AS ZUORA_UNIT_PRICE,
    COALESCE(ZUORA_AMOUNT, 0)::FLOAT                                          AS ZUORA_AMOUNT,
    COALESCE(MARKETPLACE_QUANTITY, 0)::FLOAT                                  AS MARKETPLACE_QUANTITY,
    CASE WHEN COALESCE(MARKETPLACE_QUANTITY, 0) > 0
         THEN MARKETPLACE_AMOUNT / MARKETPLACE_QUANTITY ELSE NULL END::FLOAT  AS MARKETPLACE_UNIT_PRICE,
    COALESCE(MARKETPLACE_AMOUNT, 0)::FLOAT                                    AS MARKETPLACE_AMOUNT,
    COALESCE(TOTAL_BILLING_QUANTITY, 0)::FLOAT                                AS TOTAL_BILLING_QUANTITY,
    COALESCE(TOTAL_BILLING_AMOUNT, 0)::FLOAT                                  AS TOTAL_BILLING_AMOUNT,
    QTY_DELTA::FLOAT                                                          AS QTY_DELTA,
    ABS_QTY_DELTA::FLOAT                                                      AS ABS_QTY_DELTA,
    AMOUNT_DELTA::FLOAT                                                       AS AMOUNT_DELTA,
    ABS_AMOUNT_DELTA::FLOAT                                                   AS ABS_AMOUNT_DELTA,
    CASE WHEN COALESCE(TOTAL_BILLING_AMOUNT, 0) > 0
         THEN ROUND((COALESCE(TOTAL_BILLING_AMOUNT, 0) - COALESCE(VENDOR_AMOUNT, 0))
                    / TOTAL_BILLING_AMOUNT * 100, 1)
         ELSE NULL END::FLOAT                                                 AS CW_MARGIN_PCT,
    'FALSE'::VARCHAR                                                          AS HAS_DISCOUNT,        -- flipped below for Webroot/BD
    IFF(DUPLICATE_BILLING_FLAG, 'TRUE', 'FALSE')::VARCHAR                     AS DUPLICATE_BILLING_FLAG,
    ({CANONICAL_OUTCOME_FLAG_NORMALIZATION})                                  AS OUTCOME_FLAG,
    INVESTIGATION_REASON                                                      AS INVESTIGATION_REASON
FROM {source_table};
"""


def live_emit_block(vendor: str, live_table: str) -> str:
    """Emit from a vendor's live <VENDOR>_RECON_DETAIL into DETAIL_PROD.

    Live tables share a common shape but differ from SNAPSHOT tables in
    small ways:
      - no INV_ID column (invoice id not carried through)
      - no SKU_MATCH_GROUP column
      - no MARKETPLACE_UNIT_PRICE column (compute from AMOUNT/QUANTITY)
      - Bitdefender has no VENDOR column (hardcode); Proofpoint/Acronis have it
      - OUTCOME_FLAG carries vendor-slang and is normalized by the same CASE

    This is intentionally kept in one function so every "live" vendor uses
    the same mapping. When new vendors go live, they only need to expose the
    columns referenced in this SELECT.

    Per-vendor overrides:
      - Auvik's live table names the vendor product column AUVIK_PRODUCT
        (not VENDOR_PRODUCT). Alias it.
      - KeepIT's live table does not emit a MARKETPLACE_SKUS array (KeepIT
        marketplace billing is folded into ZUORA billing). Substitute NULL.
      - Webroot's live table does not emit a separate CW_SKUS array; CW SKUs
        are carried in ZUORA_SKUS since Webroot bills directly via CW SKU.
    """
    vendor_product_expr = {
        "Auvik": "AUVIK_PRODUCT",
        "Exium": "EXIUM_PRODUCT",
    }.get(vendor, "VENDOR_PRODUCT")
    marketplace_skus_expr = {
        "KeepIT": "NULL::VARCHAR",
    }.get(vendor, "ARRAY_TO_STRING(MARKETPLACE_SKUS, ',')")
    cw_skus_expr = {
        "Webroot": "NULL::VARCHAR",
    }.get(vendor, "ARRAY_TO_STRING(CW_SKUS, ',')")
    sku_match_group_expr = {
        "ESET": "SKU_MATCH_GROUP",
    }.get(vendor, "NULL::VARCHAR")
    return f"""{USE}

-- Idempotent: remove any prior rows for this vendor.
DELETE FROM THIRD_PARTY_RECON_DETAIL_PROD WHERE VENDOR = '{vendor}';

INSERT INTO THIRD_PARTY_RECON_DETAIL_PROD (
    VENDOR, BILLING_MONTH, SF_ID, INV_ID, BILLING_TYPE,
    VENDOR_PARTNER_NAME, VENDOR_PRODUCT, SKU_MATCH_GROUP,
    CW_SKUS, ZUORA_SKUS, MARKETPLACE_SKUS, BILLING_SOURCE_MIX,
    API_QUANTITY, AVG_API_QUANTITY,
    VENDOR_QUANTITY, VENDOR_UNIT_PRICE, VENDOR_AMOUNT,
    ZUORA_QUANTITY, ZUORA_UNIT_PRICE, ZUORA_AMOUNT,
    MARKETPLACE_QUANTITY, MARKETPLACE_UNIT_PRICE, MARKETPLACE_AMOUNT,
    TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT,
    QTY_DELTA, ABS_QTY_DELTA, AMOUNT_DELTA, ABS_AMOUNT_DELTA,
    CW_MARGIN_PCT, HAS_DISCOUNT, DUPLICATE_BILLING_FLAG,
    OUTCOME_FLAG, INVESTIGATION_REASON
)
SELECT
    '{vendor}'::VARCHAR                                                        AS VENDOR,
    BILLING_MONTH::DATE                                                        AS BILLING_MONTH,
    SF_ID                                                                      AS SF_ID,
    NULL::VARCHAR                                                              AS INV_ID,
    NULL::VARCHAR                                                              AS BILLING_TYPE,
    VENDOR_PARTNER_NAME                                                        AS VENDOR_PARTNER_NAME,
    {vendor_product_expr}                                                     AS VENDOR_PRODUCT,
    {sku_match_group_expr}                                                     AS SKU_MATCH_GROUP,
    {cw_skus_expr}                                                             AS CW_SKUS,
    ARRAY_TO_STRING(ZUORA_SKUS, ',')                                           AS ZUORA_SKUS,
    {marketplace_skus_expr}                                                    AS MARKETPLACE_SKUS,
    BILLING_SOURCE_MIX                                                         AS BILLING_SOURCE_MIX,
    NULL::FLOAT                                                                AS API_QUANTITY,
    NULL::FLOAT                                                                AS AVG_API_QUANTITY,
    COALESCE(VENDOR_QUANTITY, 0)::FLOAT                                        AS VENDOR_QUANTITY,
    VENDOR_UNIT_PRICE::FLOAT                                                   AS VENDOR_UNIT_PRICE,
    COALESCE(VENDOR_AMOUNT, 0)::FLOAT                                          AS VENDOR_AMOUNT,
    COALESCE(ZUORA_QUANTITY, 0)::FLOAT                                         AS ZUORA_QUANTITY,
    ZUORA_UNIT_PRICE::FLOAT                                                    AS ZUORA_UNIT_PRICE,
    COALESCE(ZUORA_AMOUNT, 0)::FLOAT                                           AS ZUORA_AMOUNT,
    COALESCE(MARKETPLACE_QUANTITY, 0)::FLOAT                                   AS MARKETPLACE_QUANTITY,
    CASE WHEN COALESCE(MARKETPLACE_QUANTITY, 0) > 0
         THEN MARKETPLACE_AMOUNT / MARKETPLACE_QUANTITY ELSE NULL END::FLOAT   AS MARKETPLACE_UNIT_PRICE,
    COALESCE(MARKETPLACE_AMOUNT, 0)::FLOAT                                     AS MARKETPLACE_AMOUNT,
    COALESCE(TOTAL_BILLING_QUANTITY, 0)::FLOAT                                 AS TOTAL_BILLING_QUANTITY,
    COALESCE(TOTAL_BILLING_AMOUNT, 0)::FLOAT                                   AS TOTAL_BILLING_AMOUNT,
    QTY_DELTA::FLOAT                                                           AS QTY_DELTA,
    ABS_QTY_DELTA::FLOAT                                                       AS ABS_QTY_DELTA,
    AMOUNT_DELTA::FLOAT                                                        AS AMOUNT_DELTA,
    ABS_AMOUNT_DELTA::FLOAT                                                    AS ABS_AMOUNT_DELTA,
    CASE WHEN COALESCE(TOTAL_BILLING_AMOUNT, 0) > 0
         THEN ROUND((COALESCE(TOTAL_BILLING_AMOUNT, 0) - COALESCE(VENDOR_AMOUNT, 0))
                    / TOTAL_BILLING_AMOUNT * 100, 1)
         ELSE NULL END::FLOAT                                                  AS CW_MARGIN_PCT,
    'FALSE'::VARCHAR                                                           AS HAS_DISCOUNT,
    IFF(DUPLICATE_BILLING_FLAG, 'TRUE', 'FALSE')::VARCHAR                      AS DUPLICATE_BILLING_FLAG,
    ({CANONICAL_OUTCOME_FLAG_NORMALIZATION})                                   AS OUTCOME_FLAG,
    INVESTIGATION_REASON                                                       AS INVESTIGATION_REASON
FROM {live_table};
"""


def run_vendor_sql_file(conn, vendor: str) -> bool:
    """Execute a vendor's Reconciliation_Script_Prod.sql end-to-end.

    Each vendor SQL rebuilds its own <VENDOR>_RECON_DETAIL (and _SUMMARY)
    tables. That table is what live_emit_block() then reads.
    """
    path = REPO / "Vendor_Recon_Pipelines_Prod" / vendor / f"{vendor}_Reconciliation_Script_Prod.sql"
    sql = USE + "\n" + path.read_text(encoding="utf-8")
    return run_sql(conn, sql, f"execute {vendor} SQL file")


# ---------------------------------------------------------------------------
# Per-vendor DETAIL_PROD overlays: things we know how to add once rows are
# in the shared table. These stay identical to the current pipeline.
# ---------------------------------------------------------------------------
API_BACKFILL_SQL = f"""{USE}
UPDATE THIRD_PARTY_RECON_DETAIL_PROD d
SET API_QUANTITY     = t.trt_quantity,
    AVG_API_QUANTITY = t.avg_api_quantity
FROM THIRD_PARTY_RECON_SOURCE_TRT_PROD t
WHERE d.VENDOR         = t.VENDOR
  AND d.SF_ID          = t.SF_ID
  AND d.BILLING_MONTH  = t.BILLING_MONTH
  AND t.SF_ID IS NOT NULL
  AND d.VENDOR IN ('SentinelOne', 'Bitdefender', 'Webroot', 'Auvik');
"""

BITDEFENDER_MDR_BUNDLE_SQL = f"""{USE}
UPDATE THIRD_PARTY_RECON_DETAIL_PROD d
SET HAS_DISCOUNT = 'TRUE'
FROM (
    SELECT DISTINCT
        SFDC_ACCOUNT_NUMBER AS sf_id,
        BILLING_MONTH::DATE AS billing_month
    FROM ANALYTICS_DEV.DBT_NFOLD.ZUORA_THIRD_PARTY_RECON_BASE
    WHERE VENDOR_NAME = 'Bitdefender'
      AND INVOICE_STATUS = 'Posted'
      AND INVOICE_SOURCE = 'BillRun'
      AND (
        UPPER(PRODUCT_NAME) LIKE '%MDR%'
        OR UPPER(CHARGE_NAME) LIKE '%MDR%'
        OR UPPER(PRODUCT_SKU) LIKE '%MDR%'
      )
      AND BILLING_MONTH >= '2026-01-01'
) e
WHERE d.VENDOR = 'Bitdefender'
  AND d.SF_ID = e.sf_id
  AND d.BILLING_MONTH = e.billing_month;
"""

WEBROOT_RMM_DISCOUNT_SQL = f"""{USE}
UPDATE THIRD_PARTY_RECON_DETAIL_PROD d
SET HAS_DISCOUNT = 'TRUE'
FROM (
    WITH rmm_daily AS (
        SELECT
            u.partner_id::VARCHAR                  AS partner_id,
            DATE_TRUNC('month', u.on_date)::DATE   AS billing_month_snapshot,
            SUM(CASE WHEN u.is_server = 'N' THEN u.agent_cnt ELSE 0 END) AS rmm_desktop,
            SUM(CASE WHEN u.is_server = 'Y' THEN u.agent_cnt ELSE 0 END) AS rmm_server
        FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
        WHERE u.product_sku ILIKE 'CW-RMM%'
          AND u.on_date >= '2025-12-01'
          AND EXTRACT(DAY FROM u.on_date) = 19
        GROUP BY 1, 2
    ),
    rmm_entitlement AS (
        SELECT partner_id, billing_month_snapshot AS billing_month,
               (rmm_desktop + rmm_server) * 1.10 AS free_gsm_entitlement
        FROM rmm_daily
        WHERE (rmm_desktop + rmm_server) > 0
    )
    SELECT z.SFDC_ACCOUNT_NUMBER AS sf_id, t.billing_month
    FROM rmm_entitlement t
    JOIN THIRD_PARTY_RECON_SOURCE_TRT_PROD wgsm
      ON wgsm.VENDOR = 'Webroot'
     AND wgsm.CMS_ID = t.partner_id
     AND wgsm.BILLING_MONTH = t.billing_month
    JOIN (
        SELECT DISTINCT ACCOUNT_CONTINUUM_ID::VARCHAR AS partner_id, SFDC_ACCOUNT_NUMBER
        FROM ANALYTICS_DEV.DBT_NFOLD.ZUORA_THIRD_PARTY_RECON_BASE
        WHERE INVOICE_STATUS = 'Posted' AND SFDC_ACCOUNT_NUMBER ILIKE 'ACT-%'
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ACCOUNT_CONTINUUM_ID
                                    ORDER BY BILLING_MONTH DESC) = 1
    ) z ON z.partner_id = t.partner_id
    WHERE wgsm.trt_quantity <= t.free_gsm_entitlement
    GROUP BY 1, 2
) e
WHERE d.VENDOR = 'Webroot'
  AND d.SF_ID = e.sf_id
  AND d.BILLING_MONTH = e.billing_month;
"""

INIT_SQL = f"""{USE}
CREATE TABLE IF NOT EXISTS THIRD_PARTY_RECON_DETAIL_PROD (
    VENDOR VARCHAR, BILLING_MONTH DATE, SF_ID VARCHAR, INV_ID VARCHAR, BILLING_TYPE VARCHAR,
    VENDOR_PARTNER_NAME VARCHAR, VENDOR_PRODUCT VARCHAR, SKU_MATCH_GROUP VARCHAR,
    CW_SKUS VARCHAR, ZUORA_SKUS VARCHAR, MARKETPLACE_SKUS VARCHAR, BILLING_SOURCE_MIX VARCHAR,
    API_QUANTITY FLOAT, AVG_API_QUANTITY FLOAT, VENDOR_QUANTITY FLOAT, VENDOR_UNIT_PRICE FLOAT,
    VENDOR_AMOUNT FLOAT, ZUORA_QUANTITY FLOAT, ZUORA_UNIT_PRICE FLOAT, ZUORA_AMOUNT FLOAT,
    MARKETPLACE_QUANTITY FLOAT, MARKETPLACE_UNIT_PRICE FLOAT, MARKETPLACE_AMOUNT FLOAT,
    TOTAL_BILLING_QUANTITY FLOAT, TOTAL_BILLING_AMOUNT FLOAT, QTY_DELTA FLOAT,
    ABS_QTY_DELTA FLOAT, AMOUNT_DELTA FLOAT, ABS_AMOUNT_DELTA FLOAT, CW_MARGIN_PCT FLOAT,
    HAS_DISCOUNT VARCHAR, DUPLICATE_BILLING_FLAG VARCHAR, OUTCOME_FLAG VARCHAR, INVESTIGATION_REASON VARCHAR
);
TRUNCATE TABLE THIRD_PARTY_RECON_DETAIL_PROD;
"""


def run_sql(conn, sql: str, label: str) -> bool:
    t = time.perf_counter()
    print(f"  {label} ...", flush=True)
    try:
        for cur in conn.execute_string(sql, return_cursors=True):
            try:
                cur.fetchall()
            except Exception:
                pass
        conn.commit()
        print(f"    OK ({time.perf_counter() - t:.1f}s)", flush=True)
        return True
    except Exception as exc:
        print(f"    ERROR: {exc}", flush=True)
        return False


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        print("\n=== STEP 0: initialize THIRD_PARTY_RECON_DETAIL_PROD ===")
        run_sql(conn, INIT_SQL, "init + truncate DETAIL_PROD")

        print("\n=== STEP 1a: run live vendor SQL files (rebuild <VENDOR>_RECON_DETAIL) ===")
        sql_fail: dict[str, str] = {}
        live_vendors = [v for v, (m, _) in VENDOR_ROUTING.items() if m == "live"]
        for vendor in live_vendors:
            ok = run_vendor_sql_file(conn, vendor)
            if not ok:
                sql_fail[vendor] = "vendor SQL raised an error"

        print("\n=== STEP 1b: emit each vendor into DETAIL_PROD ===")
        emit_live: list[str] = []
        emit_snap: list[str] = []
        emit_fail: list[str] = []
        for vendor, (mode, src) in VENDOR_ROUTING.items():
            emitted = False
            if mode == "live" and vendor not in sql_fail:
                emitted = run_sql(
                    conn, live_emit_block(vendor, src),
                    f"emit {vendor:<12} LIVE     <- {src}",
                )
                if emitted:
                    emit_live.append(vendor)
            fallback = VENDOR_FALLBACK.get(vendor)
            if not emitted and fallback:
                print(f"    ({vendor} live path failed -- falling back to snapshot)")
                emitted = run_sql(
                    conn, emit_vendor_block(vendor, fallback),
                    f"emit {vendor:<12} snapshot <- {fallback}",
                )
                if emitted:
                    emit_snap.append(vendor)
            if not emitted:
                emit_fail.append(vendor)

        print("\n  live vendors:     " + (", ".join(emit_live) or "(none)"))
        print("  snapshot vendors: " + (", ".join(emit_snap) or "(none)"))
        if emit_fail:
            print("  FAILED vendors:   " + ", ".join(emit_fail))

        print("\n=== STEP 2: overlays on the shared table (per-vendor) ===")
        run_sql(conn, API_BACKFILL_SQL, "backfill API_QUANTITY / AVG_API_QUANTITY (S1/BD/Webroot/Auvik)")
        run_sql(conn, BITDEFENDER_MDR_BUNDLE_SQL, "Bitdefender MDR bundle flag")
        run_sql(conn, WEBROOT_RMM_DISCOUNT_SQL, "Webroot RMM discount flag")

        print("\n=== STEP 3: build THIRD_PARTY_RECON_OUTPUT_PROD (classifier) ===")
        # The classifier already knows how to turn DETAIL_PROD into the 45-column
        # OUTPUT_PROD shape the app reads. It also builds THIRD_PARTY_RECON_SUMMARY_PROD.
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "build_third_party_recon_output_prod.py")],
            capture_output=True, text=True, cwd=str(REPO / "scripts"),
        )
        if result.returncode == 0:
            print(result.stdout.rstrip())
        else:
            print(f"  classifier exit {result.returncode}: {result.stderr[:500]}")
            return 1

        print("\n=== STEP 4: verification ===")
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*), COUNT(DISTINCT VENDOR) FROM THIRD_PARTY_RECON_DETAIL_PROD")
        det_rows, det_vendors = cur.fetchone()
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT VENDOR) FROM THIRD_PARTY_RECON_OUTPUT_PROD")
        out_rows, out_vendors = cur.fetchone()
        print(f"  DETAIL_PROD:  {det_rows:>7,} rows across {det_vendors} vendors")
        print(f"  OUTPUT_PROD:  {out_rows:>7,} rows across {out_vendors} vendors")

        print("\n  OUTCOME_FLAG in DETAIL_PROD (only canonical 12 should appear):")
        cur.execute("""
            SELECT OUTCOME_FLAG, COUNT(*) FROM THIRD_PARTY_RECON_DETAIL_PROD
            GROUP BY 1 ORDER BY 2 DESC
        """)
        for f, n in cur.fetchall():
            print(f"    {str(f):<50} {n:>8,}")

        print("\n  EXCEPTION_TYPE in OUTPUT_PROD (14 app buckets):")
        cur.execute("""
            SELECT EXCEPTION_TYPE, COUNT(*), ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA,0))),0)
            FROM THIRD_PARTY_RECON_OUTPUT_PROD GROUP BY 1 ORDER BY 2 DESC
        """)
        for f, n, d in cur.fetchall():
            print(f"    {str(f):<50} {n:>8,}  ${(d or 0):>14,.0f}")

        print("\n  Proofpoint parity check:")
        cur.execute("""
            SELECT COUNT(*)                                            AS total,
                   COUNT_IF(EXCEPTION_TYPE = 'Clear')                  AS clear_n,
                   ROUND(COUNT_IF(EXCEPTION_TYPE = 'Clear')*100.0 / COUNT(*), 1) AS clear_pct,
                   ROUND(SUM(VENDOR_AMOUNT), 0)                        AS vendor_amt,
                   ROUND(SUM(TOTAL_BILLING_AMOUNT), 0)                 AS billing_amt
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            WHERE VENDOR = 'Proofpoint'
        """)
        pp_total, pp_clear, pp_pct, pp_v, pp_b = cur.fetchone()
        print(f"    total rows           : {pp_total:>7,}")
        print(f"    clear rows           : {pp_clear:>7,}   ({pp_pct}%)")
        print(f"    vendor $             : ${pp_v:>14,.0f}")
        print(f"    billing $            : ${pp_b:>14,.0f}")
        parity_ok = 90.0 <= float(pp_pct) <= 97.0
        print(f"    parity gate (90-97%) : {'PASS' if parity_ok else 'FAIL'}")

        print("\n  Per-vendor clear rate:")
        cur.execute("""
            SELECT VENDOR,
                   COUNT(*)                                    AS total,
                   COUNT_IF(EXCEPTION_TYPE = 'Clear')          AS clear_n,
                   ROUND(COUNT_IF(EXCEPTION_TYPE = 'Clear')*100.0 / COUNT(*), 1) AS clear_pct
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
            GROUP BY 1 ORDER BY 4 DESC
        """)
        print(f"    {'VENDOR':<15} {'TOTAL':>8} {'CLEAR':>8} {'CLEAR %':>10}")
        print(f"    {'-'*15} {'-'*8} {'-'*8} {'-'*10}")
        for v, t, c, p in cur.fetchall():
            print(f"    {v:<15} {t:>8,} {c:>8,} {p:>9}%")

        print("\n  Sample OUTPUT_PROD row (proves 45-col shape matches app expectation):")
        cur.execute("""
            SELECT * FROM THIRD_PARTY_RECON_OUTPUT_PROD
            WHERE OUTCOME_FLAG = 'Clear'
              AND BILLING_MONTH = '2026-06-01'
              AND VENDOR = 'Bitdefender'
            LIMIT 1
        """)
        row = cur.fetchone()
        cols = [d[0] for d in cur.description]
        if row:
            print(f"    columns present: {len(cols)}")
            print(f"    sample: {dict(zip(cols, row))}")
        else:
            print("    (no matching row - this is a data question, not architecture)")

        return 0 if parity_ok else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
