"""
Build unified _PROD translation tables for all vendors, then run reports.
All SELECT columns have explicit AS aliases (required for CREATE TABLE AS in Snowflake).
"""
from __future__ import annotations
import re
import sys, time
from pathlib import Path

REPO = Path(r"C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline")
sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

USE = "USE ROLE DEVELOPER; USE WAREHOUSE REPORTING_WH; USE DATABASE ANALYTICS_DEV; USE SCHEMA DBT_NFOLD_TRANSFORMATION;"

# Keep vendor-specific recon tables ephemeral. The app reads only unified
# THIRD_PARTY_RECON_OUTPUT_PROD and THIRD_PARTY_RECON_SUMMARY.
VENDOR_SPRAWL_TABLES = [
    # Unsuffixed recon artifacts emitted by vendor SQL pipelines.
    "ACRONIS_RECON_DETAIL", "ACRONIS_RECON_SUMMARY",
    "AUVIK_RECON_DETAIL", "AUVIK_RECON_SUMMARY",
    "BITDEFENDER_RECON_DETAIL", "BITDEFENDER_RECON_SUMMARY",
    "ESET_RECON_DETAIL", "ESET_RECON_SUMMARY",
    "EXIUM_RECON_DETAIL", "EXIUM_RECON_SUMMARY",
    "KEEPIT_RECON_DETAIL", "KEEPIT_RECON_SUMMARY",
    "PROOFPOINT_RECON_DETAIL", "PROOFPOINT_RECON_SUMMARY",
    "SENTINELONE_RECON_DETAIL", "SENTINELONE_RECON_SUMMARY",
    "WEBROOT_RECON_DETAIL", "WEBROOT_RECON_SUMMARY",
    # Historical _PROD vendor recon outputs not needed by the app.
    "ACRONIS_RECON_DETAIL_PROD", "ACRONIS_RECON_SUMMARY_PROD",
    "AUVIK_RECON_DETAIL_PROD", "AUVIK_RECON_SUMMARY_PROD",
    "BITDEFENDER_RECON_DETAIL_PROD", "BITDEFENDER_RECON_SUMMARY_PROD",
    "ESET_RECON_DETAIL_PROD", "ESET_RECON_SUMMARY_PROD",
    "EXIUM_RECON_DETAIL_PROD", "EXIUM_RECON_SUMMARY_PROD",
    "KEEPIT_RECON_DETAIL_PROD", "KEEPIT_RECON_SUMMARY_PROD",
    "PROOFPOINT_RECON_DETAIL_PROD", "PROOFPOINT_RECON_SUMMARY_PROD",
    "SENTINELONE_RECON_DETAIL_PROD", "SENTINELONE_RECON_SUMMARY_PROD",
    "WEBROOT_RECON_DETAIL_PROD", "WEBROOT_RECON_SUMMARY_PROD",
]

# ─────────────────────────────────────────────────────────────────────────────
# Helper: INSERT the standard 33-column detail SELECT from a source table into
# unified THIRD_PARTY_RECON_DETAIL_PROD with VENDOR column.
# Caller supplies:
#   vendor      - vendor name (string) to populate VENDOR column
#   src         - source table name
#   inv_expr    - SQL expression for INV_ID  (e.g. "NULL::VARCHAR" or "ZUORA_INV")
#   sku_expr    - SQL expression for SKU_MATCH_GROUP (e.g. "VENDOR_PRODUCT")
#   vendor_prod - SQL expression for VENDOR_PRODUCT  (e.g. "VENDOR_PRODUCT" or "EXIUM_PRODUCT")
#   cw_expr     - SQL expression for CW_SKUS
#   zuora_sku   - SQL expression for ZUORA_SKUS
#   mp_sku      - SQL expression for MARKETPLACE_SKUS
#   v_amount    - SQL expression for VENDOR_AMOUNT   (e.g. "COALESCE(VENDOR_AMOUNT,0)::FLOAT" or "NULL::FLOAT")
#   v_uprice    - SQL expression for VENDOR_UNIT_PRICE
#   has_disc    - SQL expression for HAS_DISCOUNT
#   dup_flag    - SQL expression for DUPLICATE_BILLING_FLAG
#   outcome_map - CASE expression string for OUTCOME_FLAG
#   extra_where - optional WHERE clause (empty string = no filter)
# ─────────────────────────────────────────────────────────────────────────────
def make_translation(vendor: str, src: str, *, inv_expr: str, sku_expr: str,
                     vendor_prod: str = "VENDOR_PRODUCT",
                     cw_expr: str, zuora_sku: str, mp_sku: str,
                     v_uprice: str = "VENDOR_UNIT_PRICE::FLOAT",
                     v_amount: str = "COALESCE(VENDOR_AMOUNT,0)::FLOAT",
                     has_disc: str = "'FALSE'::VARCHAR",
                     dup_flag: str, outcome_map: str,
                     extra_where: str = "") -> str:
    where = f"\n    WHERE {extra_where}" if extra_where else ""
    return f"""{USE}
INSERT INTO THIRD_PARTY_RECON_DETAIL_PROD
SELECT
    '{vendor}'::VARCHAR                                                         AS VENDOR,
    BILLING_MONTH::DATE                                                         AS BILLING_MONTH,
    SF_ID                                                                       AS SF_ID,
    {inv_expr}                                                                  AS INV_ID,
    NULL::VARCHAR                                                               AS BILLING_TYPE,
    VENDOR_PARTNER_NAME                                                         AS VENDOR_PARTNER_NAME,
    {vendor_prod}                                                               AS VENDOR_PRODUCT,
    {sku_expr}                                                                  AS SKU_MATCH_GROUP,
    {cw_expr}                                                                   AS CW_SKUS,
    {zuora_sku}                                                                 AS ZUORA_SKUS,
    {mp_sku}                                                                    AS MARKETPLACE_SKUS,
    BILLING_SOURCE_MIX                                                          AS BILLING_SOURCE_MIX,
    NULL::FLOAT                                                                 AS API_QUANTITY,
    NULL::FLOAT                                                                 AS AVG_API_QUANTITY,
    COALESCE(VENDOR_QUANTITY,0)::FLOAT                                          AS VENDOR_QUANTITY,
    {v_uprice}                                                                  AS VENDOR_UNIT_PRICE,
    {v_amount}                                                                  AS VENDOR_AMOUNT,
    COALESCE(ZUORA_QUANTITY,0)::FLOAT                                           AS ZUORA_QUANTITY,
    ZUORA_UNIT_PRICE::FLOAT                                                     AS ZUORA_UNIT_PRICE,
    COALESCE(ZUORA_AMOUNT,0)::FLOAT                                             AS ZUORA_AMOUNT,
    COALESCE(MARKETPLACE_QUANTITY,0)::FLOAT                                     AS MARKETPLACE_QUANTITY,
    CASE WHEN COALESCE(MARKETPLACE_QUANTITY,0)>0
         THEN MARKETPLACE_AMOUNT/MARKETPLACE_QUANTITY ELSE NULL END::FLOAT      AS MARKETPLACE_UNIT_PRICE,
    COALESCE(MARKETPLACE_AMOUNT,0)::FLOAT                                       AS MARKETPLACE_AMOUNT,
    COALESCE(TOTAL_BILLING_QUANTITY,0)::FLOAT                                   AS TOTAL_BILLING_QUANTITY,
    COALESCE(TOTAL_BILLING_AMOUNT,0)::FLOAT                                     AS TOTAL_BILLING_AMOUNT,
    QTY_DELTA::FLOAT                                                            AS QTY_DELTA,
    ABS_QTY_DELTA::FLOAT                                                        AS ABS_QTY_DELTA,
    AMOUNT_DELTA::FLOAT                                                         AS AMOUNT_DELTA,
    ABS_AMOUNT_DELTA::FLOAT                                                     AS ABS_AMOUNT_DELTA,
    CASE WHEN COALESCE(TOTAL_BILLING_AMOUNT,0)>0
         THEN ROUND((COALESCE(TOTAL_BILLING_AMOUNT,0)-COALESCE(VENDOR_AMOUNT,0))
              /TOTAL_BILLING_AMOUNT*100,1) ELSE NULL END::FLOAT                 AS CW_MARGIN_PCT,
    {has_disc}                                                                  AS HAS_DISCOUNT,
    {dup_flag}                                                                  AS DUPLICATE_BILLING_FLAG,
    {outcome_map}                                                               AS OUTCOME_FLAG,
    INVESTIGATION_REASON                                                        AS INVESTIGATION_REASON
FROM {src}{where};"""


def make_summary(vendor: str, src_prod: str) -> str:
    return f"""{USE}
INSERT INTO THIRD_PARTY_RECON_SUMMARY_PROD
SELECT
    '{vendor}'::VARCHAR                                                              AS VENDOR,
    BILLING_MONTH,
    COUNT(*)                                                                         AS total_rows,
    COUNT_IF(OUTCOME_FLAG = 'Clear')                                                 AS clear_rows,
    ROUND(COUNT_IF(OUTCOME_FLAG = 'Clear')*100.0/NULLIF(COUNT(*),0),1)               AS clear_pct,
    COUNT_IF(OUTCOME_FLAG = 'Known Discount / Bundle')                               AS known_discount_rows,
    COUNT_IF(OUTCOME_FLAG = 'Vendor Billing, Insufficient CW Billing')               AS vendor_insuff_rows,
    COUNT_IF(OUTCOME_FLAG = 'CW Billing, No Vendor Billing')                         AS cw_no_vendor_rows,
    COUNT_IF(OUTCOME_FLAG = 'Vendor Billing, No CW Billing')                         AS vendor_no_cw_rows,
    COUNT_IF(OUTCOME_FLAG = 'Unmapped Partner')                                      AS unmapped_partner_rows,
    COUNT_IF(OUTCOME_FLAG = 'Duplicated CW Invoice')                                 AS duplicate_billing_rows,
    COUNT_IF(OUTCOME_FLAG = 'API Usage Recorded, No CW Billing')                     AS api_confirmed_rows,
    COUNT_IF(OUTCOME_FLAG = 'Marketplace Billing Delay')                             AS timing_rows,
    COUNT_IF(OUTCOME_FLAG = 'Vendor SKU, No CW SKU')                                 AS vendor_sku_no_cw_rows,
    COUNT_IF(OUTCOME_FLAG = 'CW SKU, No Vendor SKU')                                 AS cw_sku_no_vendor_rows,
    COUNT_IF(OUTCOME_FLAG = 'Other Issue')                                           AS other_issue_rows,
    SUM(COALESCE(VENDOR_QUANTITY,0))::NUMBER                                         AS total_vendor_seats,
    SUM(COALESCE(TOTAL_BILLING_QUANTITY,0))::NUMBER                                  AS total_billing_seats,
    ROUND(SUM(COALESCE(VENDOR_AMOUNT,0)),2)                                          AS total_vendor_amount,
    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT,0)),2)                                   AS total_billing_amount
FROM {src_prod} GROUP BY BILLING_MONTH ORDER BY BILLING_MONTH;"""


# ── Canonical OUTCOME_FLAG values (pipeline outputs only these 12 values) ────
# 1.  Clear
# 2.  Unmapped Partner
# 3.  Duplicated CW Invoice
# 4.  Marketplace Billing Delay
# 5.  Known Discount / Bundle
# 6.  API Usage Recorded, No CW Billing
# 7.  Vendor SKU, No CW SKU
# 8.  CW SKU, No Vendor SKU
# 9.  Vendor Billing, No CW Billing       (vendor_amount > 0, cw_amount = 0)
# 10. CW Billing, No Vendor Billing       (cw_amount > 0, vendor_amount = 0)
# 11. Vendor Billing, Insufficient CW Billing  (vendor > CW by >25%, both > 0)
# 12. Other Issue
#
# Rules are mutually exclusive. CW >= vendor → Clear (absorbs old Overage).
# "CW Billing, Insufficient Vendor Billing" is merged into Clear.
# ── Outcome maps ─────────────────────────────────────────────────────────────
ACRONIS_OUTCOME = """CASE
    WHEN OUTCOME_FLAG IN ('CLEAR','MARKETPLACE_ONLY_CLEAR','MINOR_DRIFT',
                          'NEGLIGIBLE_DOLLAR_EXPOSURE','NO_ACTIVITY',
                          'OVERAGE_EXPECTED','MATERIAL_OVER_VENDOR',
                          'BILLING_DIFFERENTIAL_OVER','MARKETPLACE_OVERAGE')
                                                             THEN 'Clear'
    WHEN OUTCOME_FLAG = 'MARKETPLACE_TIMING'                 THEN 'Marketplace Billing Delay'
    WHEN OUTCOME_FLAG = 'PARTNER_MAPPING_REQUIRED'           THEN 'Unmapped Partner'
    WHEN OUTCOME_FLAG IN ('VENDOR_PRODUCT_NO_CW_SKU',
                          'VENDOR_SKU_NO_CW_SKU',
                          'VENDOR_ADDON_NO_CW_SKU')          THEN 'Vendor SKU, No CW SKU'
    WHEN OUTCOME_FLAG IN ('CW_ONLY_ADDON_NO_VENDOR',
                          'CW_SKU_NO_VENDOR_SKU')            THEN 'CW SKU, No Vendor SKU'
    WHEN OUTCOME_FLAG = 'DUPLICATE_BILLING'                  THEN 'Duplicated CW Invoice'
    WHEN OUTCOME_FLAG IN ('RMM_DISCOUNTED','KNOWN_DISCOUNT_BUNDLE',
                          'MDR_BUNDLE','CW_INCLUDED_ZERO_DOLLAR',
                          'INTENTIONAL_DISCOUNT')            THEN 'Known Discount / Bundle'
    -- CW has billing but vendor amount = 0
    WHEN OUTCOME_FLAG IN ('STRUCTURAL_BILLING_ONLY',
                          'MARKETPLACE_BILLING_NO_VENDOR')
         AND COALESCE(VENDOR_AMOUNT,0) = 0                   THEN 'CW Billing, No Vendor Billing'
    -- Vendor has billing but CW amount = 0
    WHEN OUTCOME_FLAG = 'STRUCTURAL_VENDOR_ONLY_NO_CONTRACT'
         AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0            THEN 'Vendor Billing, No CW Billing'
    -- Vendor > CW by >25%, both sides have amounts
    WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER')
         AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0            THEN 'Vendor Billing, No CW Billing'
    WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER')
         AND COALESCE(VENDOR_AMOUNT,0) > COALESCE(TOTAL_BILLING_AMOUNT,0) * 1.25
                                                             THEN 'Vendor Billing, Insufficient CW Billing'
    WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER')
                                                             THEN 'Clear'
    ELSE 'Other Issue' END"""

# ESET uses direct INSERT from ESET_RECON_DETAIL_PROD; ESET_OUTCOME defined here
# for documentation only. Normalization is applied in the post-process UPDATE.
ESSET_OUTCOME_DOC = """-- ESET internal flags: CLEAR, PARTNER_MAPPING_REQUIRED, NO_BILLING_NO_HISTORY,
-- BILLING_OVER_VENDOR, VENDOR_OVER_BILLING. All normalized in post-process."""

EXIUM_OUTCOME = """CASE
    WHEN OUTCOME_FLAG = 'CLEAR'                        THEN 'Clear'
    WHEN OUTCOME_FLAG = 'PARTNER_MAPPING_REQUIRED'     THEN 'Unmapped Partner'
    WHEN OUTCOME_FLAG = 'DUPLICATE_BILLING'            THEN 'Duplicated CW Invoice'
    WHEN OUTCOME_FLAG = 'BILLING_TIMING_ADJACENT_MONTH' THEN 'Marketplace Billing Delay'
    WHEN OUTCOME_FLAG = 'SKU_MISMATCH_BILLING_ON_OTHER_SKU' THEN 'Vendor SKU, No CW SKU'
    WHEN OUTCOME_FLAG = 'NO_BILLING_NO_HISTORY'
         AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0      THEN 'Vendor Billing, No CW Billing'
    WHEN OUTCOME_FLAG = 'BILLING_OVER_VENDOR'
         AND COALESCE(VENDOR_AMOUNT,0) = 0             THEN 'CW Billing, No Vendor Billing'
    WHEN OUTCOME_FLAG = 'BILLING_OVER_VENDOR'          THEN 'Clear'
    WHEN OUTCOME_FLAG = 'VENDOR_OVER_BILLING'
         AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0      THEN 'Vendor Billing, No CW Billing'
    WHEN OUTCOME_FLAG = 'VENDOR_OVER_BILLING'
         AND COALESCE(VENDOR_AMOUNT,0) > COALESCE(TOTAL_BILLING_AMOUNT,0) * 1.25
                                                       THEN 'Vendor Billing, Insufficient CW Billing'
    WHEN OUTCOME_FLAG = 'VENDOR_OVER_BILLING'          THEN 'Clear'
    ELSE 'Other Issue' END"""

PP_OUTCOME = """CASE
    WHEN OUTCOME_FLAG IN ('CLEAR','MINOR_DRIFT',
                          'MATERIAL_OVER_VENDOR','BILLING_DIFFERENTIAL_OVER')
                                                                THEN 'Clear'
    WHEN OUTCOME_FLAG = 'MARKETPLACE_TIMING'                    THEN 'Marketplace Billing Delay'
    WHEN OUTCOME_FLAG = 'PARTNER_MAPPING_REQUIRED'              THEN 'Unmapped Partner'
    WHEN OUTCOME_FLAG = 'DUPLICATE_BILLING'                     THEN 'Duplicated CW Invoice'
    WHEN OUTCOME_FLAG = 'SKU_MISMATCH_BILLING_ON_OTHER_SKU'     THEN 'Vendor SKU, No CW SKU'
    WHEN OUTCOME_FLAG IN ('KNOWN_GOOD_MAPPING_MISSING_CURRENT_BILLING',
                          'STRUCTURAL_VENDOR_ONLY_NO_CONTRACT',
                          'NO_BILLING_NO_HISTORY')
         AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0               THEN 'Vendor Billing, No CW Billing'
    WHEN OUTCOME_FLAG = 'CONTRACT_TIMING_OR_INACTIVE'
         AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0               THEN 'Vendor Billing, No CW Billing'
    WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER')
         AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0               THEN 'Vendor Billing, No CW Billing'
    WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER')
         AND COALESCE(VENDOR_AMOUNT,0) > COALESCE(TOTAL_BILLING_AMOUNT,0) * 1.25
                                                                THEN 'Vendor Billing, Insufficient CW Billing'
    WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER')
                                                                THEN 'Clear'
    ELSE 'Other Issue' END"""

S1_OUTCOME = """CASE
    WHEN OUTCOME_FLAG IN ('CLEAR','MINOR_DRIFT',
                          'MATERIAL_OVER_VENDOR','BILLING_DIFFERENTIAL_OVER')
                                                              THEN 'Clear'
    WHEN OUTCOME_FLAG = 'PARTNER_MAPPING_REQUIRED'            THEN 'Unmapped Partner'
    WHEN OUTCOME_FLAG IN ('VENDOR_ADDON_NO_CW_SKU',
                          'VENDOR_PRODUCT_NO_CW_SKU',
                          'VENDOR_SKU_NO_CW_SKU',
                          'SKU_MISMATCH_BILLING_ON_OTHER_SKU') THEN 'Vendor SKU, No CW SKU'
    WHEN OUTCOME_FLAG IN ('CW_ONLY_ADDON_NO_VENDOR',
                          'CW_SKU_NO_VENDOR_SKU')             THEN 'CW SKU, No Vendor SKU'
    WHEN OUTCOME_FLAG = 'DUPLICATE_BILLING'                   THEN 'Duplicated CW Invoice'
    WHEN OUTCOME_FLAG = 'MDR_BUNDLE'                          THEN 'Known Discount / Bundle'
    WHEN OUTCOME_FLAG IN ('STRUCTURAL_VENDOR_ONLY_TRT_CONFIRMED',
                          'TRT_VENDOR_USAGE_NOT_BILLED')      THEN 'API Usage Recorded, No CW Billing'
    WHEN OUTCOME_FLAG IN ('STRUCTURAL_VENDOR_ONLY_NO_CONTRACT',
                          'NO_BILLING_NO_HISTORY',
                          'MAPPED_ADDON_NO_CURRENT_BILLING',
                          'KNOWN_GOOD_MAPPING_MISSING_CURRENT_BILLING',
                          'CONTRACT_TIMING_OR_INACTIVE')
         AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0             THEN 'Vendor Billing, No CW Billing'
    WHEN OUTCOME_FLAG IN ('STRUCTURAL_BILLING_ONLY',
                          'BILLING_ONLY_NO_VENDOR_USAGE',
                          'STRUCTURAL_BILLING_ONLY_TRT_CONFIRMED')
         AND COALESCE(VENDOR_AMOUNT,0) = 0                    THEN 'CW Billing, No Vendor Billing'
    WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER',
                          'ACCOUNT_HAS_OTHER_S1_BILLING_NO_MATCH')
         AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0             THEN 'Vendor Billing, No CW Billing'
    WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER',
                          'ACCOUNT_HAS_OTHER_S1_BILLING_NO_MATCH')
         AND COALESCE(VENDOR_AMOUNT,0) > COALESCE(TOTAL_BILLING_AMOUNT,0) * 1.25
                                                              THEN 'Vendor Billing, Insufficient CW Billing'
    WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER',
                          'ACCOUNT_HAS_OTHER_S1_BILLING_NO_MATCH')
                                                              THEN 'Clear'
    ELSE 'Other Issue' END"""

# ── Build translation SQL blocks ──────────────────────────────────────────────
STANDALONE_VENDOR_TABLES = {
    "Acronis":     "THIRD_PARTY_STANDALONE_RECON_DETAIL__ACRONIS",
    "Auvik":       "THIRD_PARTY_STANDALONE_RECON_DETAIL__AUVIK",
    "Bitdefender": "THIRD_PARTY_STANDALONE_RECON_DETAIL__BITDEFENDER",
    "ESET":        "THIRD_PARTY_STANDALONE_RECON_DETAIL__ESET",
    "Exium":       "THIRD_PARTY_STANDALONE_RECON_DETAIL__EXIUM",
    "KeepIT":      "THIRD_PARTY_STANDALONE_RECON_DETAIL__KEEPIT",
    "Proofpoint":  "THIRD_PARTY_STANDALONE_RECON_DETAIL__PROOFPOINT",
    "SentinelOne": "THIRD_PARTY_STANDALONE_RECON_DETAIL__SENTINELONE",
    "Webroot":     "THIRD_PARTY_STANDALONE_RECON_DETAIL__WEBROOT",
}


def standalone_insert(vendor: str, src_table: str) -> str:
    """Insert a vendor's standalone detail into unified THIRD_PARTY_RECON_DETAIL_PROD.

    Standalone vendor tables share a canonical schema (61 columns) that already
    matches the unified schema on all required fields. Missing unified columns
    are populated with NULLs of the correct type.
    """
    return f"""{USE}
INSERT INTO THIRD_PARTY_RECON_DETAIL_PROD
SELECT
    '{vendor}'::VARCHAR                                                         AS VENDOR,
    BILLING_MONTH::DATE                                                         AS BILLING_MONTH,
    SF_ID                                                                       AS SF_ID,
    COALESCE(ZUORA_INV, MP_INV)                                                 AS INV_ID,
    NULL::VARCHAR                                                               AS BILLING_TYPE,
    VENDOR_PARTNER_NAME                                                         AS VENDOR_PARTNER_NAME,
    VENDOR_PRODUCT                                                              AS VENDOR_PRODUCT,
    SKU_MATCH_GROUP                                                             AS SKU_MATCH_GROUP,
    CW_SKUS                                                                     AS CW_SKUS,
    ZUORA_SKUS                                                                  AS ZUORA_SKUS,
    MARKETPLACE_SKUS                                                            AS MARKETPLACE_SKUS,
    BILLING_SOURCE_MIX                                                          AS BILLING_SOURCE_MIX,
    NULL::FLOAT                                                                 AS API_QUANTITY,
    NULL::FLOAT                                                                 AS AVG_API_QUANTITY,
    COALESCE(VENDOR_QUANTITY,0)::FLOAT                                          AS VENDOR_QUANTITY,
    VENDOR_UNIT_PRICE::FLOAT                                                    AS VENDOR_UNIT_PRICE,
    COALESCE(VENDOR_AMOUNT,0)::FLOAT                                            AS VENDOR_AMOUNT,
    COALESCE(ZUORA_QUANTITY,0)::FLOAT                                           AS ZUORA_QUANTITY,
    ZUORA_UNIT_PRICE::FLOAT                                                     AS ZUORA_UNIT_PRICE,
    COALESCE(ZUORA_AMOUNT,0)::FLOAT                                             AS ZUORA_AMOUNT,
    COALESCE(MARKETPLACE_QUANTITY,0)::FLOAT                                     AS MARKETPLACE_QUANTITY,
    CASE WHEN COALESCE(MARKETPLACE_QUANTITY,0)>0
         THEN MARKETPLACE_AMOUNT/MARKETPLACE_QUANTITY ELSE NULL END::FLOAT      AS MARKETPLACE_UNIT_PRICE,
    COALESCE(MARKETPLACE_AMOUNT,0)::FLOAT                                       AS MARKETPLACE_AMOUNT,
    COALESCE(TOTAL_BILLING_QUANTITY,0)::FLOAT                                   AS TOTAL_BILLING_QUANTITY,
    COALESCE(TOTAL_BILLING_AMOUNT,0)::FLOAT                                     AS TOTAL_BILLING_AMOUNT,
    QTY_DELTA::FLOAT                                                            AS QTY_DELTA,
    ABS_QTY_DELTA::FLOAT                                                        AS ABS_QTY_DELTA,
    AMOUNT_DELTA::FLOAT                                                         AS AMOUNT_DELTA,
    ABS_AMOUNT_DELTA::FLOAT                                                     AS ABS_AMOUNT_DELTA,
    CASE WHEN COALESCE(TOTAL_BILLING_AMOUNT,0)>0
         THEN ROUND((COALESCE(TOTAL_BILLING_AMOUNT,0)-COALESCE(VENDOR_AMOUNT,0))
              /TOTAL_BILLING_AMOUNT*100,1) ELSE NULL END::FLOAT                 AS CW_MARGIN_PCT,
    'FALSE'::VARCHAR                                                            AS HAS_DISCOUNT,
    IFF(DUPLICATE_BILLING_FLAG,'TRUE','FALSE')::VARCHAR                         AS DUPLICATE_BILLING_FLAG,
    OUTCOME_FLAG                                                                AS OUTCOME_FLAG,
    INVESTIGATION_REASON                                                        AS INVESTIGATION_REASON
FROM {src_table};
"""


TRANSLATIONS = {
    vendor: standalone_insert(vendor, table) + "\n" + make_summary(vendor, "THIRD_PARTY_RECON_DETAIL_PROD")
    for vendor, table in STANDALONE_VENDOR_TABLES.items()
}

# Legacy per-vendor translation blocks retained for reference only (unused).
LEGACY_TRANSLATIONS = {
    "Acronis": (
        make_translation("Acronis", "ACRONIS_RECON_DETAIL",
            inv_expr="NULL::VARCHAR",
            sku_expr="VENDOR_PRODUCT",
            cw_expr="ARRAY_TO_STRING(CW_SKUS, ' | ')",
            zuora_sku="ARRAY_TO_STRING(ZUORA_SKUS, ' | ')",
            mp_sku="ARRAY_TO_STRING(MARKETPLACE_SKUS, ' | ')",
            dup_flag="IFF(DUPLICATE_BILLING_FLAG,'TRUE','FALSE')::VARCHAR",
            outcome_map=ACRONIS_OUTCOME)
        + "\n" + make_summary("Acronis", "THIRD_PARTY_RECON_DETAIL_PROD")
    ),
    "Auvik": (
        make_translation("Auvik", "AUVIK_RECON_DETAIL",
            inv_expr="NULL::VARCHAR",
            sku_expr="SKU_MATCH_GROUP",
            vendor_prod="AUVIK_PRODUCT",
            cw_expr="IFF(TYPEOF(CW_SKUS)='ARRAY', ARRAY_TO_STRING(CW_SKUS, ' | '), TO_VARCHAR(CW_SKUS))",
            zuora_sku="IFF(TYPEOF(ZUORA_SKUS)='ARRAY', ARRAY_TO_STRING(ZUORA_SKUS, ' | '), TO_VARCHAR(ZUORA_SKUS))",
            mp_sku="IFF(TYPEOF(MARKETPLACE_SKUS)='ARRAY', ARRAY_TO_STRING(MARKETPLACE_SKUS, ' | '), TO_VARCHAR(MARKETPLACE_SKUS))",
            v_uprice="VENDOR_UNIT_PRICE::FLOAT",
            v_amount="COALESCE(VENDOR_AMOUNT,0)::FLOAT",
            has_disc="'FALSE'::VARCHAR",
            dup_flag="IFF(UPPER(COALESCE(TO_VARCHAR(DUPLICATE_BILLING_FLAG),'FALSE'))='TRUE','TRUE','FALSE')::VARCHAR",
            outcome_map="OUTCOME_FLAG")
        + "\n" + make_summary("Auvik", "THIRD_PARTY_RECON_DETAIL_PROD")
    ),
    "Bitdefender": (
        make_translation("Bitdefender", "BITDEFENDER_RECON_DETAIL",
            inv_expr="NULL::VARCHAR",
            sku_expr="SKU_MATCH_GROUP",
            cw_expr="IFF(TYPEOF(CW_SKUS)='ARRAY', ARRAY_TO_STRING(CW_SKUS, ' | '), TO_VARCHAR(CW_SKUS))",
            zuora_sku="IFF(TYPEOF(ZUORA_SKUS)='ARRAY', ARRAY_TO_STRING(ZUORA_SKUS, ' | '), TO_VARCHAR(ZUORA_SKUS))",
            mp_sku="IFF(TYPEOF(MARKETPLACE_SKUS)='ARRAY', ARRAY_TO_STRING(MARKETPLACE_SKUS, ' | '), TO_VARCHAR(MARKETPLACE_SKUS))",
            v_uprice="VENDOR_UNIT_PRICE::FLOAT",
            v_amount="COALESCE(VENDOR_AMOUNT,0)::FLOAT",
            has_disc="'FALSE'::VARCHAR",
            dup_flag="IFF(UPPER(COALESCE(TO_VARCHAR(DUPLICATE_BILLING_FLAG),'FALSE'))='TRUE','TRUE','FALSE')::VARCHAR",
            outcome_map="OUTCOME_FLAG")
        + "\n" + make_summary("Bitdefender", "THIRD_PARTY_RECON_DETAIL_PROD")
    ),
    "ESET": (
        make_translation("ESET", "ESET_RECON_DETAIL",
            inv_expr="NULL::VARCHAR",
            sku_expr="VENDOR_PRODUCT",
            cw_expr="IFF(TYPEOF(CW_SKUS)='ARRAY', ARRAY_TO_STRING(CW_SKUS, ' | '), TO_VARCHAR(CW_SKUS))",
            zuora_sku="IFF(TYPEOF(ZUORA_SKUS)='ARRAY', ARRAY_TO_STRING(ZUORA_SKUS, ' | '), TO_VARCHAR(ZUORA_SKUS))",
            mp_sku="IFF(TYPEOF(MARKETPLACE_SKUS)='ARRAY', ARRAY_TO_STRING(MARKETPLACE_SKUS, ' | '), TO_VARCHAR(MARKETPLACE_SKUS))",
            v_uprice="VENDOR_UNIT_PRICE::FLOAT",
            v_amount="COALESCE(VENDOR_AMOUNT,0)::FLOAT",
            has_disc="'FALSE'::VARCHAR",
            dup_flag="IFF(UPPER(COALESCE(TO_VARCHAR(DUPLICATE_BILLING_FLAG),'FALSE'))='TRUE','TRUE','FALSE')::VARCHAR",
            outcome_map="BASE_OUTCOME_FLAG")
        + "\n" + make_summary("ESET", "THIRD_PARTY_RECON_DETAIL_PROD")
    ),
    "Exium": (
        make_translation("Exium", "EXIUM_RECON_DETAIL",
            inv_expr="NULL::VARCHAR",
            sku_expr="SKU_MATCH_GROUP",
            vendor_prod="EXIUM_PRODUCT",
            cw_expr="ARRAY_TO_STRING(CW_SKUS, ' | ')",
            zuora_sku="ARRAY_TO_STRING(ZUORA_SKUS, ' | ')",
            mp_sku="ARRAY_TO_STRING(MARKETPLACE_SKUS, ' | ')",
            dup_flag="IFF(DUPLICATE_BILLING_FLAG=TRUE,'TRUE','FALSE')::VARCHAR",
            outcome_map=EXIUM_OUTCOME)
        + "\n" + make_summary("Exium", "THIRD_PARTY_RECON_DETAIL_PROD")
    ),
    "KeepIT": (
        make_translation("KeepIT", "KEEPIT_RECON_DETAIL",
            inv_expr="NULL::VARCHAR",
            sku_expr="VENDOR_PRODUCT",
            cw_expr="IFF(TYPEOF(CW_SKUS)='ARRAY', ARRAY_TO_STRING(CW_SKUS, ' | '), TO_VARCHAR(CW_SKUS))",
            zuora_sku="IFF(TYPEOF(ZUORA_SKUS)='ARRAY', ARRAY_TO_STRING(ZUORA_SKUS, ' | '), TO_VARCHAR(ZUORA_SKUS))",
            mp_sku="IFF(TYPEOF(MARKETPLACE_SKUS)='ARRAY', ARRAY_TO_STRING(MARKETPLACE_SKUS, ' | '), TO_VARCHAR(MARKETPLACE_SKUS))",
            v_uprice="VENDOR_UNIT_PRICE::FLOAT",
            v_amount="COALESCE(VENDOR_AMOUNT,0)::FLOAT",
            has_disc="'FALSE'::VARCHAR",
            dup_flag="IFF(UPPER(COALESCE(TO_VARCHAR(DUPLICATE_BILLING_FLAG),'FALSE'))='TRUE','TRUE','FALSE')::VARCHAR",
            outcome_map="OUTCOME_FLAG")
        + "\n" + make_summary("KeepIT", "THIRD_PARTY_RECON_DETAIL_PROD")
    ),
    "Proofpoint": (
        make_translation("Proofpoint", "PROOFPOINT_RECON_DETAIL",
            inv_expr="NULL::VARCHAR",
            sku_expr="VENDOR_PRODUCT",
            cw_expr="ARRAY_TO_STRING(CW_SKUS, ' | ')",
            zuora_sku="ARRAY_TO_STRING(ZUORA_SKUS, ' | ')",
            mp_sku="ARRAY_TO_STRING(MARKETPLACE_SKUS, ' | ')",
            dup_flag="IFF(DUPLICATE_BILLING_FLAG,'TRUE','FALSE')::VARCHAR",
            outcome_map=PP_OUTCOME)
        + "\n" + make_summary("Proofpoint", "THIRD_PARTY_RECON_DETAIL_PROD")
    ),
    "SentinelOne": (
        make_translation("SentinelOne", "SENTINELONE_RECON_DETAIL",
            inv_expr="ZUORA_INV",
            sku_expr="SKU_MATCH_GROUP",
            cw_expr="ARRAY_TO_STRING(CW_SKUS, ' | ')",
            zuora_sku="ARRAY_TO_STRING(ZUORA_SKUS, ' | ')",
            mp_sku="ARRAY_TO_STRING(MARKETPLACE_SKUS, ' | ')",
            has_disc="IFF(COALESCE(MDR_BUNDLE_AMOUNT,0)>0,'TRUE','FALSE')::VARCHAR",
            dup_flag="IFF(DUPLICATE_BILLING_FLAG,'TRUE','FALSE')::VARCHAR",
            outcome_map=S1_OUTCOME)
        + "\n" + make_summary("SentinelOne", "THIRD_PARTY_RECON_DETAIL_PROD")
    ),
    "Webroot": (
        make_translation("Webroot", "WEBROOT_RECON_DETAIL",
            inv_expr="NULL::VARCHAR",
            sku_expr="SKU_MATCH_GROUP",
            cw_expr="IFF(TYPEOF(CW_SKUS)='ARRAY', ARRAY_TO_STRING(CW_SKUS, ' | '), TO_VARCHAR(CW_SKUS))",
            zuora_sku="IFF(TYPEOF(ZUORA_SKUS)='ARRAY', ARRAY_TO_STRING(ZUORA_SKUS, ' | '), TO_VARCHAR(ZUORA_SKUS))",
            mp_sku="IFF(TYPEOF(MARKETPLACE_SKUS)='ARRAY', ARRAY_TO_STRING(MARKETPLACE_SKUS, ' | '), TO_VARCHAR(MARKETPLACE_SKUS))",
            v_uprice="VENDOR_UNIT_PRICE::FLOAT",
            v_amount="COALESCE(VENDOR_AMOUNT,0)::FLOAT",
            has_disc="'FALSE'::VARCHAR",
            dup_flag="IFF(UPPER(COALESCE(TO_VARCHAR(DUPLICATE_BILLING_FLAG),'FALSE'))='TRUE','TRUE','FALSE')::VARCHAR",
            outcome_map="OUTCOME_FLAG")
        + "\n" + make_summary("Webroot", "THIRD_PARTY_RECON_DETAIL_PROD")
    ),
}


# ── Helpers ───────────────────────────────────────────────────────────────────
def run_sql(conn, sql: str, label: str) -> bool:
    t = time.perf_counter()
    print(f"  {label} ...", flush=True)
    try:
        for cur in conn.execute_string(sql, return_cursors=True):
            try: cur.fetchall()
            except Exception: pass
        conn.commit()
        print(f"    OK ({time.perf_counter()-t:.1f}s)", flush=True)
        return True
    except Exception as exc:
        print(f"    ERROR: {exc}", flush=True)
        return False


def run_file(conn, path: Path, label: str) -> bool:
    return run_sql(conn, path.read_text(encoding="utf-8"), label)


def fetch(conn, q: str):
    cur = conn.cursor()
    cur.execute(q)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    return cols, rows


def table_has_column(conn, table_name: str, column_name: str) -> bool:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1
            FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'DBT_NFOLD_TRANSFORMATION'
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            LIMIT 1
            """,
            (table_name.upper(), column_name.upper()),
        )
        return cur.fetchone() is not None
    finally:
        cur.close()


def tbl(cols, rows, title: str) -> None:
    W = 160
    print(f"\n{'=' * W}")
    print(f"  {title}")
    print(f"{'=' * W}")
    if not rows:
        print("  (no rows)")
        return
    wid = [max(len(str(c)), max((len(str(r[i]) if r[i] is not None else "NULL") for r in rows), default=0))
           for i, c in enumerate(cols)]
    print("  " + " | ".join(str(c).ljust(w) for c, w in zip(cols, wid)))
    print("  " + "-+-".join("-" * w for w in wid))
    for row in rows:
        print("  " + " | ".join((str(v) if v is not None else "NULL").ljust(w) for v, w in zip(row, wid)))
    print(f"\n  {len(rows)} rows")


def run_vendor_pipeline(conn, vendor: str, label: str, extra_sql: str = "") -> bool:
    """Run a vendor's full pipeline: 00_reference_maps + 01_billing_sources + 02_final_reconciliation.

    Concatenated into one execute_string call so TEMPORARY / intermediate tables
    stay in scope across steps.
    """
    base = REPO / f"Vendor_Recon_Pipelines_Prod/{vendor}/Prod_Pipeline/sql"
    ref_path   = base / "00_reference_maps.sql"
    bill_path  = base / "01_billing_sources.sql"
    recon_path = base / "02_final_reconciliation.sql"
    if not recon_path.exists():
        print(f"  SKIP {label}: SQL file not found. Using existing {vendor.upper()}_RECON_DETAIL_PROD table.")
        return True
    ref_sql   = ref_path.read_text(encoding="utf-8") if ref_path.exists() else ""
    bill_sql  = bill_path.read_text(encoding="utf-8") if bill_path.exists() else ""
    recon_sql = recon_path.read_text(encoding="utf-8")
    # Some vendor refmap scripts create optional backup clones that fail when
    # the source object is absent or has changed type (table/view). Strip these
    # backup-only CLONE statements for idempotent orchestrated runs.
    ref_sql = re.sub(
        r"(?im)^\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+\S+\s+CLONE\s+\S+\s*;\s*$",
        "",
        ref_sql,
    )
    _use_block = "USE ROLE DEVELOPER;\nUSE WAREHOUSE REPORTING_WH;\nUSE DATABASE ANALYTICS_DEV;\nUSE SCHEMA DBT_NFOLD_TRANSFORMATION;"
    bill_clean  = bill_sql.replace(_use_block, "").lstrip()
    recon_clean = recon_sql.replace(_use_block, "").lstrip()
    parts = []
    if ref_sql:
        parts.append(ref_sql)
    if bill_clean:
        parts.append("\n\n-- ── billing sources ───────────────────────────\n\n" + bill_clean)
    parts.append("\n\n-- ── reconciliation ────────────────────────────\n\n" + recon_clean)
    # After vendor SQL builds <VENDOR>_RECON_DETAIL / _SUMMARY (unsuffixed), promote
    # to *_PROD via zero-copy clone so the downstream unified-insert logic works.
    parts.append(
        f"\n\n-- ── promote to _PROD ────────────────────────────\n\n"
        f"USE ROLE DEVELOPER; USE WAREHOUSE REPORTING_WH;\n"
        f"USE DATABASE ANALYTICS_DEV; USE SCHEMA DBT_NFOLD_TRANSFORMATION;\n"
        f"CREATE OR REPLACE TABLE {vendor.upper()}_RECON_DETAIL_PROD  AS SELECT * FROM {vendor.upper()}_RECON_DETAIL;\n"
        f"CREATE OR REPLACE TABLE {vendor.upper()}_RECON_SUMMARY_PROD AS SELECT * FROM {vendor.upper()}_RECON_SUMMARY;\n"
    )
    combined = "".join(parts)
    if extra_sql:
        combined += "\n\n" + extra_sql
    return run_sql(conn, combined, label)


# ── Main ─────────────────────────────────────────────────────────────────────
conn = get_snowflake_connection(
    role="DEVELOPER", warehouse="REPORTING_WH",
    database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
)
try:
    print("\n=== STEP 0: Initialize unified recon tables ===")
    init_sql = f"""{USE}
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

    CREATE OR REPLACE TABLE THIRD_PARTY_RECON_SUMMARY_PROD (
        VENDOR VARCHAR, BILLING_MONTH DATE, total_rows NUMBER, clear_rows NUMBER, clear_pct FLOAT,
        known_discount_rows NUMBER, vendor_insuff_rows NUMBER, cw_no_vendor_rows NUMBER,
        vendor_no_cw_rows NUMBER, unmapped_partner_rows NUMBER, duplicate_billing_rows NUMBER,
        api_confirmed_rows NUMBER, timing_rows NUMBER,
        vendor_sku_no_cw_rows NUMBER, cw_sku_no_vendor_rows NUMBER, other_issue_rows NUMBER,
        total_vendor_seats NUMBER, total_billing_seats NUMBER, total_vendor_amount NUMBER, total_billing_amount NUMBER
    );
    """
    run_sql(conn, init_sql, "Initialize unified tables")

    print("\n=== STEP 0b: Backfill missing invoice prices in VENDOR_USAGE_PROD ===")
    run_file(conn, REPO / "sql/00b_backfill_invoice_prices.sql", "price backfill")

    print("\n=== STEP 0c: Refresh vendor-specific USAGE views (shim over unified table) ===")
    run_file(conn, REPO / "sql/00c_vendor_usage_views.sql", "vendor USAGE views")

    print("\n=== STEP 1a: Auvik + KeepIT ===")
    run_vendor_pipeline(conn, "Auvik",  "Auvik")
    run_vendor_pipeline(conn, "KeepIT", "KeepIT")

    print("\n=== STEP 1b: Bitdefender ===")
    run_vendor_pipeline(conn, "Bitdefender", "Bitdefender")

    print("\n=== STEP 1b2: ESET (rebuilds ESET_RECON_DETAIL with MODIFIER-based seats) ===")
    run_vendor_pipeline(conn, "ESET", "ESET")

    print("\n=== STEP 1c: Webroot already _PROD — verifying ===")
    try:
        _, r0 = fetch(conn, "SELECT COUNT(*) FROM WEBROOT_RECON_DETAIL_PROD")
        print(f"  WEBROOT_RECON_DETAIL_PROD rows: {r0[0][0]}")
    except Exception:
        print("  WARNING: WEBROOT_RECON_DETAIL_PROD not found in Snowflake")
        run_vendor_pipeline(conn, "Webroot", "Webroot")

    print("\n=== STEP 1c2: Vendor source rebuilds (Acronis, Exium, Proofpoint, SentinelOne) ===")
    # run_vendor_pipeline concatenates 00_reference_maps + 02_final_reconciliation into one
    # execute_string call so TEMPORARY tables from refmaps are always in scope for recon.
    run_vendor_pipeline(conn, "Acronis",    "Acronis")
    run_vendor_pipeline(conn, "Exium",      "Exium")
    run_vendor_pipeline(conn, "Proofpoint", "Proofpoint")
    # SentinelOne has an optional TRT crosscheck step; skip if table doesn't exist.
    # Ensure SENTINELONE_TRT_USAGE_MONTHLY exists as an empty stub so the
    # 02_final_reconciliation.sql CTE doesn't fail at compile time.
    _s1_trt_stub = (
        "USE ROLE DEVELOPER; USE WAREHOUSE REPORTING_WH; "
        "USE DATABASE ANALYTICS_DEV; USE SCHEMA DBT_NFOLD_TRANSFORMATION;\n"
        "CREATE TABLE IF NOT EXISTS SENTINELONE_TRT_USAGE_MONTHLY ("
        "  SF_ID VARCHAR, BILLING_MONTH DATE, TRT_AGENTS_AVG NUMBER(18,6),"
        "  S1_GROUP VARCHAR, SITE_NAME VARCHAR);"
    )
    run_sql(conn, _s1_trt_stub, "SentinelOne TRT stub (if not exists)")
    run_vendor_pipeline(conn, "SentinelOne", "SentinelOne")

    print("\n=== STEP 1c3: Populate API_QUANTITY / AVG_API_QUANTITY on vendor recon tables ===")
    # Cycle-billed vendors expose 3 measurements to the reconciliation UI:
    #   VENDOR_QUANTITY  = source-of-truth (curated Excel / vendor UI)
    #   TOTAL_BILLING_QUANTITY = what CW actually billed (Zuora + Marketplace)
    #   API_QUANTITY / AVG_API_QUANTITY = what the vendor's API reports on the
    #     cycle snapshot day (SentinelOne=21, Bitdefender=21, Webroot=19,
    #     Auvik=21) and the trailing-cycle daily average.
    # These are NOT sources of truth — they show whether the feed into Zuora
    # is aligned. Populated directly on the vendor detail tables so drilldown
    # views inherit them without a downstream backfill.
    # First clear stale values so a rerun doesn't preserve a prior mapping.
    api_reset_statements = []
    for t in ("AUVIK_RECON_DETAIL_PROD", "BITDEFENDER_RECON_DETAIL_PROD", "WEBROOT_RECON_DETAIL_PROD", "SENTINELONE_RECON_DETAIL_PROD"):
        if table_has_column(conn, t, "API_QUANTITY") and table_has_column(conn, t, "AVG_API_QUANTITY"):
            api_reset_statements.append(f"UPDATE {t} SET API_QUANTITY = NULL, AVG_API_QUANTITY = NULL;")
    if api_reset_statements:
        run_sql(conn, USE + "\n" + "\n".join(api_reset_statements), "Reset API metrics on cycle vendors")
    else:
        print("  Skip API metric reset: API columns not present on cycle vendor detail tables")

    api_backfill_statements = []
    vendor_table_pairs = [
        ("AUVIK_RECON_DETAIL_PROD", "Auvik"),
        ("BITDEFENDER_RECON_DETAIL_PROD", "Bitdefender"),
        ("WEBROOT_RECON_DETAIL_PROD", "Webroot"),
        ("SENTINELONE_RECON_DETAIL_PROD", "SentinelOne"),
    ]
    for t, vendor_name in vendor_table_pairs:
        if table_has_column(conn, t, "API_QUANTITY") and table_has_column(conn, t, "AVG_API_QUANTITY"):
            api_backfill_statements.append(
                f"UPDATE {t} d SET API_QUANTITY = t2.trt_quantity, AVG_API_QUANTITY = t2.avg_api_quantity "
                f"FROM THIRD_PARTY_RECON_SOURCE_TRT_PROD t2 "
                f"WHERE t2.VENDOR='{vendor_name}' AND d.SF_ID=t2.SF_ID AND d.BILLING_MONTH=t2.BILLING_MONTH "
                f"AND t2.SF_ID IS NOT NULL;"
            )
    if api_backfill_statements:
        run_sql(conn, USE + "\n" + "\n".join(api_backfill_statements), "Vendor-level API metrics backfill (4 cycle vendors)")
    else:
        print("  Skip vendor-level API backfill: API columns not present on cycle vendor detail tables")

    print("\n=== STEP 1c3b: Reset HAS_DISCOUNT on vendor detail tables (idempotent) ===")
    # HAS_DISCOUNT is set by per-vendor logic in STEPS 1c4 (Webroot) and 1c5 (BD).
    # Reset all four cycle-vendor detail tables to 'FALSE' so a rerun doesn't
    # inherit stale TRUE flags from prior executions or from the static seed
    # tables (Auvik/SentinelOne have no discount rules, so they stay 'FALSE').
    discount_reset_statements = []
    for t in ("AUVIK_RECON_DETAIL_PROD", "BITDEFENDER_RECON_DETAIL_PROD", "WEBROOT_RECON_DETAIL_PROD", "SENTINELONE_RECON_DETAIL_PROD"):
        if table_has_column(conn, t, "HAS_DISCOUNT"):
            discount_reset_statements.append(
                f"UPDATE {t} SET HAS_DISCOUNT = 'FALSE' WHERE COALESCE(UPPER(TO_VARCHAR(HAS_DISCOUNT)),'FALSE') <> 'FALSE';"
            )
    if discount_reset_statements:
        run_sql(conn, USE + "\n" + "\n".join(discount_reset_statements), "Reset HAS_DISCOUNT flags on cycle vendors")
    else:
        print("  Skip HAS_DISCOUNT reset: column not present on cycle vendor detail tables")

    print("\n=== STEP 1c4: Webroot RMM discount flag (per-vendor logic) ===")
    # Per Recon Team (Amit Mehta): free GSM licenses are granted based on
    # Command Desktop usage, while Command Server usage is chargeable.
    #   Free GSM entitlement = (CW-RMM Desktop + CW-RMM Server) * 1.10
    # "OpenText Core Endpoint Protection" == SAEP == GSM == BEP — these are
    # the same Webroot product family. If the partner's Webroot GSM count
    # (from TRT) is <= the RMM entitlement threshold, then the GSM charge
    # is bundled into the RMM offering and no separate CW invoice is expected.
    # This flag drives the Rule 3 "Known Discount / Bundle" bucket downstream
    # and prevents Rule 5 (API Usage, No CW Billing) from misfiring on RMM
    # customers.
    #
    # Grain: (SF_ID, BILLING_MONTH). Snapshot day for Webroot = 19th.
    # Applied to WEBROOT_RECON_DETAIL_PROD only (per-vendor scope).
    _webroot_rmm_sql = f"""{USE}
UPDATE WEBROOT_RECON_DETAIL_PROD d
SET HAS_DISCOUNT = 'TRUE'
FROM (
    WITH rmm_daily AS (
        -- Aggregate CW-RMM Desktop (is_server='N') + Server (is_server='Y')
        -- device counts on the Webroot cycle snapshot day (19th).
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
        SELECT
            partner_id,
            -- Webroot bills for month X on the 19th of month X's cycle window
            -- so snapshot_date's month maps to billing_month = same month.
            billing_month_snapshot AS billing_month,
            (rmm_desktop + rmm_server) * 1.10 AS free_gsm_entitlement
        FROM rmm_daily
        WHERE (rmm_desktop + rmm_server) > 0
    )
    SELECT
        z.SFDC_ACCOUNT_NUMBER AS sf_id,
        t.billing_month
    FROM rmm_entitlement t
    JOIN THIRD_PARTY_RECON_SOURCE_TRT_PROD wgsm
      ON wgsm.VENDOR = 'Webroot'
     AND wgsm.CMS_ID = t.partner_id
     AND wgsm.BILLING_MONTH = t.billing_month
    JOIN (
        SELECT DISTINCT ACCOUNT_CONTINUUM_ID::VARCHAR AS partner_id, SFDC_ACCOUNT_NUMBER
        FROM ANALYTICS_DEV.DBT_NFOLD.ZUORA_THIRD_PARTY_RECON_BASE
        WHERE INVOICE_STATUS='Posted' AND SFDC_ACCOUNT_NUMBER ILIKE 'ACT-%'
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ACCOUNT_CONTINUUM_ID
                                    ORDER BY BILLING_MONTH DESC)=1
    ) z ON z.partner_id = t.partner_id
    WHERE wgsm.trt_quantity <= t.free_gsm_entitlement
    GROUP BY 1, 2
) e
WHERE d.SF_ID = e.sf_id AND d.BILLING_MONTH = e.billing_month;"""
    if table_has_column(conn, "WEBROOT_RECON_DETAIL_PROD", "HAS_DISCOUNT"):
        run_sql(conn, _webroot_rmm_sql, "Webroot RMM discount flag")
    else:
        print("  Skip Webroot RMM discount flag: HAS_DISCOUNT column not present")

    print("\n=== STEP 1c5: Bitdefender MDR bundle flag (per-vendor logic) ===")
    # Per Recon Team: MDR Bitdefender is a bundle that appears as one product
    # in Zuora billing but splits into three components (Gravity Zone, ATS, EDR)
    # in the vendor royalty report. Any partner-month that has an "MDR" charge
    # in Zuora BD billing indicates the partner is on the MDR bundle, so all
    # BD line items for that partner-month should carry HAS_DISCOUNT=TRUE.
    # This routes them into Rule 3 "Known Discount / Bundle" instead of
    # firing false-positive Rule 5 / Rule 6 flags for the component products.
    #
    # Grain: (SF_ID, BILLING_MONTH). Applied to BITDEFENDER_RECON_DETAIL_PROD only.
    _bd_mdr_sql = f"""{USE}
UPDATE BITDEFENDER_RECON_DETAIL_PROD d
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
WHERE d.SF_ID = e.sf_id AND d.BILLING_MONTH = e.billing_month;"""
    if table_has_column(conn, "BITDEFENDER_RECON_DETAIL_PROD", "HAS_DISCOUNT"):
        run_sql(conn, _bd_mdr_sql, "Bitdefender MDR bundle flag")
    else:
        print("  Skip Bitdefender MDR bundle flag: HAS_DISCOUNT column not present")

    print("\n=== STEP 1d: Complex vendor translations ===")
    for vendor, sql in TRANSLATIONS.items():
        run_sql(conn, sql, vendor)

    print("\n=== STEP 1d2: Backfill cycle-aware API_QUANTITY / AVG_API_QUANTITY ===")
    # Vendor-level populate ran in STEP 1c3 (Auvik/BD/Webroot/SentinelOne).
    # For Auvik/BD/Webroot the values flow into the unified table automatically
    # via SELECT * in STEP 1d. SentinelOne uses make_translation() which writes
    # NULL::FLOAT explicitly, so this UPDATE is the SentinelOne bridge — and
    # also a defense-in-depth catch-all for any vendor whose translation path
    # doesn't preserve the columns.
    # THIRD_PARTY_RECON_SOURCE_TRT_PROD is built by sql/01_unified_billing_sources.sql
    # from ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE.
    # Filter shape matches the manual recon Excel files exactly:
    #   product_sku IN (seed__product_categorization WHERE vendor ILIKE %vendor%)
    #   [+ is_server='' for Webroot DNS/SAT]
    # Cycle snapshot days:
    #   SentinelOne=21, Bitdefender=21, Webroot=19, Auvik=21
    #   trt_quantity     = point-in-time agent_cnt on the snapshot day
    #   avg_api_quantity = daily average across (prev_snapshot, snapshot]
    # Both metrics are (VENDOR, SF_ID, BILLING_MONTH) grain and are repeated
    # onto every product row for that partner-month so drilldown can display
    # them as context. Rule 5 (API Usage Recorded, No CW Billing) fires at
    # partner-month grain via a window function in CANONICAL_EXCEPTION_TYPE.
    api_backfill_sql = f"""{USE}
UPDATE THIRD_PARTY_RECON_DETAIL_PROD d
SET API_QUANTITY     = t.trt_quantity,
    AVG_API_QUANTITY = t.avg_api_quantity
FROM THIRD_PARTY_RECON_SOURCE_TRT_PROD t
WHERE d.VENDOR         = t.VENDOR
  AND d.SF_ID          = t.SF_ID
  AND d.BILLING_MONTH  = t.BILLING_MONTH
  AND t.SF_ID IS NOT NULL
  AND d.VENDOR IN ('SentinelOne', 'Bitdefender', 'Webroot', 'Auvik');"""
    run_sql(conn, api_backfill_sql, "Backfill API_QUANTITY / AVG_API_QUANTITY")

    print("\n=== STEP 1e: Normalize OUTCOME_FLAG to canonical 12-bucket taxonomy ===")
    # Vendors inserted via direct SELECT * (Auvik, Bitdefender, ESET, KeepIT, Webroot)
    # may carry old internal flag values. This UPDATE normalizes them to the same
    # canonical names the translation vendors now output.
    normalize_sql = f"""{USE}
UPDATE THIRD_PARTY_RECON_DETAIL_PROD
SET OUTCOME_FLAG = CASE
    -- Clear variants
    WHEN OUTCOME_FLAG IN ('CLEAR','MATCHED','MINOR_DRIFT','NEGLIGIBLE_DOLLAR_EXPOSURE',
                          'MARKETPLACE_ONLY_CLEAR','NO_ACTIVITY','OVERAGE_EXPECTED',
                          'MATERIAL_OVER_VENDOR','BILLING_DIFFERENTIAL_OVER',
                          'MARKETPLACE_OVERAGE','BILLING_OVER_VENDOR',
                          'Overage','Clear - Discounted / Bundled')
         AND NOT (OUTCOME_FLAG = 'BILLING_OVER_VENDOR' AND COALESCE(VENDOR_AMOUNT,0) > 0
                  AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0)
                                                                              THEN 'Clear'
    -- Timing
    WHEN OUTCOME_FLAG IN ('MARKETPLACE_TIMING','BILLING_TIMING_ADJACENT_MONTH')
                                                                              THEN 'Marketplace Billing Delay'
    -- Partner mapping gap
    WHEN OUTCOME_FLAG IN ('PARTNER_MAPPING_REQUIRED','Unmapped SKU')
         AND (SF_ID IS NULL OR UPPER(TRIM(COALESCE(SF_ID,'')))
              IN ('','UNKNOWN','NONE','UNMAPPED','NULL'))                      THEN 'Unmapped Partner'
    -- SKU catalog gap (valid SF_ID but unmapped product)
    WHEN OUTCOME_FLAG IN ('PARTNER_MAPPING_REQUIRED','Unmapped SKU')
         AND SF_ID IS NOT NULL
         AND UPPER(TRIM(COALESCE(SF_ID,''))) NOT IN ('','UNKNOWN','NONE','UNMAPPED','NULL')
                                                                              THEN 'Vendor SKU, No CW SKU'
    WHEN OUTCOME_FLAG IN ('VENDOR_ADDON_NO_CW_SKU','VENDOR_PRODUCT_NO_CW_SKU',
                          'VENDOR_SKU_NO_CW_SKU','SKU_MISMATCH_BILLING_ON_OTHER_SKU')
                                                                              THEN 'Vendor SKU, No CW SKU'
    WHEN OUTCOME_FLAG IN ('CW_ONLY_ADDON_NO_VENDOR','CW_SKU_NO_VENDOR_SKU')  THEN 'CW SKU, No Vendor SKU'
    -- Duplicate billing
    WHEN OUTCOME_FLAG IN ('DUPLICATE_BILLING','Duplicate Billing')            THEN 'Duplicated CW Invoice'
    -- Discounts / bundles
    WHEN OUTCOME_FLAG IN ('RMM_DISCOUNTED','KNOWN_DISCOUNT_BUNDLE','MDR_BUNDLE',
                          'CW_INCLUDED_ZERO_DOLLAR','INTENTIONAL_DISCOUNT')   THEN 'Known Discount / Bundle'
    -- TRT / API confirmed usage
    WHEN OUTCOME_FLAG IN ('TRT_VENDOR_USAGE_NOT_BILLED',
                          'STRUCTURAL_VENDOR_ONLY_TRT_CONFIRMED',
                          'Missing CW Billing - API Confirmed')               THEN 'API Usage Recorded, No CW Billing'
    -- CW has billing but vendor amount = 0
    WHEN OUTCOME_FLAG IN ('STRUCTURAL_BILLING_ONLY','BILLING_ONLY_NO_VENDOR_USAGE',
                          'STRUCTURAL_BILLING_ONLY_TRT_CONFIRMED',
                          'MARKETPLACE_BILLING_NO_VENDOR',
                          'Billed by CW, Missing Vendor Billing')
         AND COALESCE(VENDOR_AMOUNT,0) = 0                                    THEN 'CW Billing, No Vendor Billing'
    -- Vendor has billing but CW amount = 0
    WHEN OUTCOME_FLAG IN ('STRUCTURAL_VENDOR_ONLY_NO_CONTRACT','NO_BILLING_NO_HISTORY',
                          'MAPPED_ADDON_NO_CURRENT_BILLING',
                          'KNOWN_GOOD_MAPPING_MISSING_CURRENT_BILLING',
                          'CONTRACT_TIMING_OR_INACTIVE','TAKEOUT_SUPPORT_FILE_NO_DIRECT_BILLING',
                          'CARR_SECONDARY_CHECK_ONLY',
                          'Billed by Vendor, Missing CW Billing')
         AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0                             THEN 'Vendor Billing, No CW Billing'
    -- Vendor > CW by >25%, both have billing
    WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER',
                          'VENDOR_OVER_BILLING','ACCOUNT_HAS_OTHER_S1_BILLING_NO_MATCH',
                          'Vendor Billing > CW Billing')
         AND COALESCE(TOTAL_BILLING_AMOUNT,0) = 0                             THEN 'Vendor Billing, No CW Billing'
    WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER',
                          'VENDOR_OVER_BILLING','ACCOUNT_HAS_OTHER_S1_BILLING_NO_MATCH',
                          'Vendor Billing > CW Billing')
         AND COALESCE(VENDOR_AMOUNT,0) > COALESCE(TOTAL_BILLING_AMOUNT,0) * 1.25
                                                                              THEN 'Vendor Billing, Insufficient CW Billing'
    WHEN OUTCOME_FLAG IN ('MATERIAL_UNDER_VENDOR','BILLING_DIFFERENTIAL_UNDER',
                          'VENDOR_OVER_BILLING','ACCOUNT_HAS_OTHER_S1_BILLING_NO_MATCH',
                          'Vendor Billing > CW Billing')                      THEN 'Clear'
    -- Already canonical — pass through unchanged
    WHEN OUTCOME_FLAG IN ('Clear','Unmapped Partner','Duplicated CW Invoice',
                          'Marketplace Billing Delay','Known Discount / Bundle',
                          'API Usage Recorded, No CW Billing','Vendor SKU, No CW SKU',
                          'CW SKU, No Vendor SKU','Vendor Billing, No CW Billing',
                          'CW Billing, No Vendor Billing',
                          'Vendor Billing, Insufficient CW Billing','Other Issue')
                                                                              THEN OUTCOME_FLAG
    ELSE 'Other Issue'
END
WHERE OUTCOME_FLAG NOT IN (
    'Clear','Unmapped Partner','Duplicated CW Invoice','Marketplace Billing Delay',
    'Known Discount / Bundle','API Usage Recorded, No CW Billing',
    'Vendor SKU, No CW SKU','CW SKU, No Vendor SKU',
    'Vendor Billing, No CW Billing','CW Billing, No Vendor Billing',
    'Vendor Billing, Insufficient CW Billing','Other Issue'
);
"""
    run_sql(conn, normalize_sql, "Normalize OUTCOME_FLAG to canonical taxonomy")

    print("\n=== STEP 3: Rebuild THIRD_PARTY_RECON_OUTPUT_PROD (app table) ===")
    try:
        import subprocess, sys as _sys
        result = subprocess.run(
            [_sys.executable, str(REPO / "scripts/build_third_party_recon_output_prod.py")],
            capture_output=True, text=True,
            cwd=str(REPO / "scripts"),
        )
        if result.returncode == 0:
            print(f"  {result.stdout.strip()}")
        else:
            print(f"  WARNING: output builder exit {result.returncode}: {result.stderr.strip()[:300]}")
    except Exception as exc:
        print(f"  WARNING: could not run output builder: {exc}")

    print("\n=== STEP 4: Report tables ===")
    run_file(conn, REPO / "sql/03_flag_distribution_report.sql", "flag_distribution_report")
    run_file(conn, REPO / "sql/04_manual_recon_gap_audit.sql",   "manual_recon_gap_audit")

    # ── Report 1 ─────────────────────────────────────────────────────────────
    c, r = fetch(conn, """
        SELECT vendor,
               TO_CHAR(BILLING_MONTH,'YYYY-MM')   AS month,
               total_rows,
               combined_clear_rows                AS clear_n,
               combined_clear_pct                 AS clear_pct,
               unmapped_sku_rows                  AS unmapped,
               vendor_no_cw_rows                  AS v_no_cw,
               cw_no_vendor_rows                  AS cw_no_v,
               api_confirmed_rows                 AS api_conf,
               vendor_over_cw_rows                AS neg_margin,
               overage_rows                       AS overage,
               duplicate_billing_rows             AS dup,
               other_issue_rows                   AS other,
               total_vendor_seats                 AS v_seats,
               total_billing_seats                AS b_seats,
               ROUND(total_abs_qty_delta,0)::NUMBER AS abs_qty,
               ROUND(total_vendor_amount,0)::NUMBER  AS v_amt,
               ROUND(total_billing_amount,0)::NUMBER AS b_amt
        FROM FLAG_DISTRIBUTION_BY_VENDOR_MONTH
        ORDER BY vendor, month
    """)
    tbl(c, r, "FLAG DISTRIBUTION BY VENDOR x MONTH")

    # ── Report 2 ─────────────────────────────────────────────────────────────
    c2, r2 = fetch(conn, """
        SELECT vendor,
               TO_CHAR(BILLING_MONTH,'YYYY-MM')   AS month,
               total_pipeline_rows                AS total,
               pipeline_clear_rows                AS clear_n,
               pipeline_clear_pct                 AS clear_pct,
               clear_rows_with_qty_gap            AS clr_qty_gap,
               unmapped_rows,
               vendor_no_cw_rows,
               cw_no_vendor_rows,
               variance_rows,
               other_rows,
               total_vendor_seats                 AS v_seats,
               total_billing_seats                AS b_seats,
               ROUND(total_abs_qty_delta,0)::NUMBER AS abs_qty,
               ROUND(total_vendor_amount,0)::NUMBER  AS v_amt,
               ROUND(total_billing_amount,0)::NUMBER AS b_amt
        FROM FLAG_GAP_AUDIT
        ORDER BY vendor, month
    """)
    tbl(c2, r2, "FLAG GAP AUDIT — pipeline vs manual recon baseline")

    # ── Report 3 ─────────────────────────────────────────────────────────────
    try:
        c3, r3 = fetch(conn, """
            SELECT vendor, TO_CHAR(BILLING_MONTH,'YYYY-MM') AS month,
                   COUNT(*) AS unmapped_rows,
                   COUNT(DISTINCT VENDOR_PARTNER_NAME) AS distinct_partners,
                   SUM(VENDOR_QUANTITY)::NUMBER AS v_seats,
                   ROUND(SUM(VENDOR_AMOUNT),0)  AS v_amt
            FROM GAP_A_UNMAPPED_PARTNERS
            GROUP BY 1,2 ORDER BY 1,2
        """)
        tbl(c3, r3, "GAP A — UNMAPPED PARTNERS by vendor/month")
    except Exception as exc:
        print(f"\n  GAP_A query skipped: {exc}")

    # ── Report 4 ─────────────────────────────────────────────────────────────
    c4, r4 = fetch(conn, """
        SELECT vendor, TO_CHAR(BILLING_MONTH,'YYYY-MM') AS month,
               vendor_no_cw_rows    AS billed_v_missing_cw,
               cw_no_vendor_rows    AS billed_cw_missing_v,
               api_confirmed_rows   AS api_conf_missing,
               vendor_over_cw_rows  AS neg_margin,
               overage_rows         AS overage,
               duplicate_billing_rows AS duplicate
        FROM FLAG_DISTRIBUTION_BY_VENDOR_MONTH
        WHERE vendor_no_cw_rows > 0 OR vendor_over_cw_rows > 0
           OR overage_rows > 0 OR api_confirmed_rows > 0
        ORDER BY vendor, month
    """)
    tbl(c4, r4, "ACTION FLAGS — rows requiring investigation by vendor/month")

    print("\n=== STEP 5: Cleanup vendor-specific recon table sprawl ===")
    cleanup_sql = USE + "\n" + "\n".join(
        f"DROP TABLE IF EXISTS {table_name};" for table_name in VENDOR_SPRAWL_TABLES
    )
    run_sql(conn, cleanup_sql, "Drop vendor recon tables")

finally:
    conn.close()
