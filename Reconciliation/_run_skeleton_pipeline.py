"""
_run_skeleton_pipeline.py -- CLEAN end-to-end reconciliation skeleton.

Architecture (the "one big table" you asked for):

    ingestion (9 py scripts)  ->  THIRD_PARTY_RECON_VENDOR_USAGE_PROD
    billing sources           ->  THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
                                  THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
                                  (BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE live)

    9 vendor emit blocks      ->  INSERT INTO THIRD_PARTY_RECON_DETAIL_PROD
                                  (34 canonical columns, 12 canonical OUTCOME_FLAG values)

    classifier                ->  THIRD_PARTY_RECON_OUTPUT_PROD (45 cols, adds
                                  EXCEPTION_TYPE + queue flags + CASE_ID)
                                  + THIRD_PARTY_RECON_SUMMARY_PROD

    app                       ->  reads OUTPUT_PROD + SUMMARY only

No STANDALONE reads in the app path. No TRANSLATIONS dict. No per-vendor
_RECON_DETAIL_PROD sprawl. No union step.

Current production routing: all 9 vendors execute their SQL scripts from
Reconciliation/ and emit directly from <VENDOR>_RECON_DETAIL.
Legacy snapshot fallback paths are intentionally disabled.
"""
from __future__ import annotations
import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline")
sys.path.insert(0, r"C:\Users\Nate.Fold\projects")

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402
from canonical_outcomes import strict_outcome_case, structural_evidence_case

USE = (
    "USE ROLE DEVELOPER; "
    "USE WAREHOUSE REPORTING_WH; "
    "USE DATABASE ANALYTICS_DEV; "
    "USE SCHEMA DBT_NFOLD_TRANSFORMATION;"
)

DETAIL_TABLE_PROD = "THIRD_PARTY_RECON_DETAIL_PROD"
DETAIL_TABLE_STAGE = "THIRD_PARTY_RECON_DETAIL_PROD_STAGING"

# ---------------------------------------------------------------------------
# VENDOR ROUTING
#
# Every vendor executes its live reconciliation SQL and emits from its live
# detail table into the shared production detail mart.
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

# ---------------------------------------------------------------------------
# CANONICAL EMIT: every vendor uses this exact shape. 34 columns, in the
# order that matches THIRD_PARTY_RECON_DETAIL_PROD. OUTCOME_FLAG values that
# aren't already canonical are folded into canonical here (SNAPSHOT tables
# already carry canonical values, but the CASE also handles vendor-slang
# left over from any prior run).
# ---------------------------------------------------------------------------
CANONICAL_OUTCOME_FLAG_NORMALIZATION = strict_outcome_case(
    structural_evidence_code="__STRUCTURAL_EVIDENCE_EXPR__",
    api_quantity="__API_QUANTITY_EXPR__",
    avg_api_quantity="__AVG_API_QUANTITY_EXPR__",
)


def live_emit_block(vendor: str, live_table: str, target_table: str = DETAIL_TABLE_STAGE) -> str:
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
            - KeepIT's live table emits CARR_SKUS instead of MARKETPLACE_SKUS.
                Map that into the shared marketplace-sku slot.
      - Webroot's live table does not emit a separate CW_SKUS array; CW SKUs
        are carried in ZUORA_SKUS since Webroot bills directly via CW SKU.
    """
    vendor_product_expr = {
        "Auvik": "AUVIK_PRODUCT",
        "Exium": "EXIUM_PRODUCT",
    }.get(vendor, "VENDOR_PRODUCT")
    marketplace_skus_expr = {
        "ESET": "MARKETPLACE_SKUS",
        "KeepIT": "ARRAY_TO_STRING(CARR_SKUS, ',')",
    }.get(vendor, "ARRAY_TO_STRING(MARKETPLACE_SKUS, ',')")
    cw_skus_expr = {
        "ESET": "CW_SKUS",
        "Webroot": "NULL::VARCHAR",
    }.get(vendor, "ARRAY_TO_STRING(CW_SKUS, ',')")
    zuora_skus_expr = {
        "ESET": "ZUORA_SKUS",
        "KeepIT": "ARRAY_TO_STRING(ARRAY_DISTINCT(ARRAY_CAT(COALESCE(ZUORA_SKUS, ARRAY_CONSTRUCT()), COALESCE(ANCILLARY_ZUORA_SKUS, ARRAY_CONSTRUCT()))), ',')",
    }.get(vendor, "ARRAY_TO_STRING(ZUORA_SKUS, ',')")
    zuora_amount_expr = {
        "KeepIT": "COALESCE(ZUORA_AMOUNT, 0) + COALESCE(ANCILLARY_ZUORA_AMOUNT, 0)",
    }.get(vendor, "COALESCE(ZUORA_AMOUNT, 0)")
    sku_match_group_expr = {
        "ESET": "SKU_MATCH_GROUP",
        "Exium": "SKU_MATCH_GROUP",
        "SentinelOne": "SKU_MATCH_GROUP",
        "Webroot": "SKU_MATCH_GROUP",
        "Auvik": "SKU_MATCH_GROUP",
    }.get(vendor, "NULL::VARCHAR")
    recon_subgrain_expr = {
        "KeepIT": "SOURCE_FAMILY",
        "Webroot": "RECON_STREAM",
    }.get(vendor, "NULL::VARCHAR")
    api_quantity_expr = {
        "Bitdefender": "API_QUANTITY",
        "SentinelOne": "API_QUANTITY",
        "Auvik": "API_QUANTITY",
        "Acronis": "API_QUANTITY",
        "Proofpoint": "API_QUANTITY",
        "Exium": "API_QUANTITY",
        "KeepIT": "API_QUANTITY",
    }.get(vendor, "NULL::FLOAT")
    avg_api_quantity_expr = {
        "Bitdefender": "AVG_API_QUANTITY",
        "SentinelOne": "AVG_API_QUANTITY",
        "Auvik": "AVG_API_QUANTITY",
        "Acronis": "AVG_API_QUANTITY",
        "Proofpoint": "AVG_API_QUANTITY",
        "Exium": "AVG_API_QUANTITY",
        "KeepIT": "AVG_API_QUANTITY",
    }.get(vendor, "NULL::FLOAT")
    vendor_unit_price_expr = "VENDOR_UNIT_PRICE"
    vendor_amount_expr = "VENDOR_AMOUNT"
    amount_delta_expr = "AMOUNT_DELTA"
    abs_amount_delta_expr = "ABS_AMOUNT_DELTA"
    structural_evidence_expr = structural_evidence_case("OUTCOME_FLAG")
    outcome_flag_expr = (
        CANONICAL_OUTCOME_FLAG_NORMALIZATION
        .replace("__STRUCTURAL_EVIDENCE_EXPR__", structural_evidence_expr)
        .replace("__API_QUANTITY_EXPR__", api_quantity_expr)
        .replace("__AVG_API_QUANTITY_EXPR__", avg_api_quantity_expr)
    )
    return f"""{USE}

-- Idempotent: remove any prior rows for this vendor.
DELETE FROM {target_table} WHERE VENDOR = '{vendor}';

INSERT INTO {target_table} (
    VENDOR, BILLING_MONTH, SF_ID, INV_ID, BILLING_TYPE, RECON_SUBGRAIN,
    VENDOR_PARTNER_NAME, VENDOR_PRODUCT, SKU_MATCH_GROUP,
    CW_SKUS, ZUORA_SKUS, MARKETPLACE_SKUS, BILLING_SOURCE_MIX,
    API_QUANTITY, AVG_API_QUANTITY,
    VENDOR_QUANTITY, VENDOR_UNIT_PRICE, VENDOR_AMOUNT,
    ZUORA_QUANTITY, ZUORA_UNIT_PRICE, ZUORA_AMOUNT,
    MARKETPLACE_QUANTITY, MARKETPLACE_UNIT_PRICE, MARKETPLACE_AMOUNT,
    TOTAL_BILLING_QUANTITY, TOTAL_BILLING_AMOUNT,
    QTY_DELTA, ABS_QTY_DELTA, AMOUNT_DELTA, ABS_AMOUNT_DELTA,
    CW_MARGIN_PCT, HAS_DISCOUNT, DUPLICATE_BILLING_FLAG,
    OUTCOME_FLAG, INVESTIGATION_REASON,
    NATIVE_OUTCOME_EVIDENCE, STRUCTURAL_EVIDENCE_CODE
)
SELECT
    '{vendor}'::VARCHAR                                                        AS VENDOR,
    BILLING_MONTH::DATE                                                        AS BILLING_MONTH,
    SF_ID                                                                      AS SF_ID,
    NULL::VARCHAR                                                              AS INV_ID,
    NULL::VARCHAR                                                              AS BILLING_TYPE,
    {recon_subgrain_expr}::VARCHAR                                             AS RECON_SUBGRAIN,
    VENDOR_PARTNER_NAME                                                        AS VENDOR_PARTNER_NAME,
    {vendor_product_expr}                                                     AS VENDOR_PRODUCT,
    {sku_match_group_expr}                                                     AS SKU_MATCH_GROUP,
    {cw_skus_expr}                                                             AS CW_SKUS,
    {zuora_skus_expr}                                                          AS ZUORA_SKUS,
    {marketplace_skus_expr}                                                    AS MARKETPLACE_SKUS,
    BILLING_SOURCE_MIX                                                         AS BILLING_SOURCE_MIX,
    {api_quantity_expr}::FLOAT                                                 AS API_QUANTITY,
    {avg_api_quantity_expr}::FLOAT                                             AS AVG_API_QUANTITY,
    COALESCE(VENDOR_QUANTITY, 0)::FLOAT                                        AS VENDOR_QUANTITY,
    {vendor_unit_price_expr}::FLOAT                                            AS VENDOR_UNIT_PRICE,
    COALESCE({vendor_amount_expr}, 0)::FLOAT                                   AS VENDOR_AMOUNT,
    COALESCE(ZUORA_QUANTITY, 0)::FLOAT                                         AS ZUORA_QUANTITY,
    ZUORA_UNIT_PRICE::FLOAT                                                    AS ZUORA_UNIT_PRICE,
    ({zuora_amount_expr})::FLOAT                                               AS ZUORA_AMOUNT,
    COALESCE(MARKETPLACE_QUANTITY, 0)::FLOAT                                   AS MARKETPLACE_QUANTITY,
    CASE WHEN COALESCE(MARKETPLACE_QUANTITY, 0) > 0
         THEN MARKETPLACE_AMOUNT / MARKETPLACE_QUANTITY ELSE NULL END::FLOAT   AS MARKETPLACE_UNIT_PRICE,
    COALESCE(MARKETPLACE_AMOUNT, 0)::FLOAT                                     AS MARKETPLACE_AMOUNT,
    COALESCE(TOTAL_BILLING_QUANTITY, 0)::FLOAT                                 AS TOTAL_BILLING_QUANTITY,
    COALESCE(TOTAL_BILLING_AMOUNT, 0)::FLOAT                                   AS TOTAL_BILLING_AMOUNT,
    QTY_DELTA::FLOAT                                                           AS QTY_DELTA,
    ABS_QTY_DELTA::FLOAT                                                       AS ABS_QTY_DELTA,
    {amount_delta_expr}::FLOAT                                                 AS AMOUNT_DELTA,
    {abs_amount_delta_expr}::FLOAT                                             AS ABS_AMOUNT_DELTA,
    CASE WHEN COALESCE(TOTAL_BILLING_AMOUNT, 0) > 0
         THEN ROUND((COALESCE(TOTAL_BILLING_AMOUNT, 0) - COALESCE(VENDOR_AMOUNT, 0))
                    / TOTAL_BILLING_AMOUNT * 100, 1)
         ELSE NULL END::FLOAT                                                  AS CW_MARGIN_PCT,
    'FALSE'::VARCHAR                                                           AS HAS_DISCOUNT,
    IFF(
        DUPLICATE_BILLING_FLAG
        OR (({zuora_amount_expr}) > 0 AND COALESCE(MARKETPLACE_AMOUNT, 0) > 0),
        'TRUE',
        'FALSE'
    )::VARCHAR                                                                  AS DUPLICATE_BILLING_FLAG,
    ({outcome_flag_expr})                                                      AS OUTCOME_FLAG,
    INVESTIGATION_REASON                                                       AS INVESTIGATION_REASON,
    OUTCOME_FLAG::VARCHAR                                                      AS NATIVE_OUTCOME_EVIDENCE,
    ({structural_evidence_expr})::VARCHAR                                      AS STRUCTURAL_EVIDENCE_CODE
FROM {live_table};
"""


def run_vendor_sql_file(conn, vendor: str) -> bool:
    """Execute a vendor's Reconciliation_Script_Prod.sql end-to-end.

    Each vendor SQL rebuilds its own <VENDOR>_RECON_DETAIL (and _SUMMARY)
    tables. That table is what live_emit_block() then reads.
    """
    path = REPO / "Reconciliation" / f"{vendor}_Reconciliation_Script_Prod.sql"
    sql = USE + "\n" + path.read_text(encoding="utf-8")
    return run_sql(conn, sql, f"execute {vendor} SQL file")


def run_repo_sql_file(conn, relative_path: str, label: str) -> bool:
    """Execute a repository SQL script with the standard Snowflake context."""
    path = REPO / relative_path
    sql = USE + "\n" + path.read_text(encoding="utf-8")
    return run_sql(conn, sql, label)


# ---------------------------------------------------------------------------
# Per-vendor DETAIL_PROD overlays: things we know how to add once rows are
# in the shared table. These stay identical to the current pipeline.
# ---------------------------------------------------------------------------
API_BACKFILL_SQL = f"""{USE}
UPDATE {DETAIL_TABLE_STAGE} d
SET API_QUANTITY     = a.trt_quantity,
    AVG_API_QUANTITY = a.avg_api_quantity
FROM (
    WITH vendor_cycle AS (
        SELECT 'Webroot'::VARCHAR AS vendor, 21::INT AS cycle_day
    ),
    webroot_sku_universe AS (
        SELECT DISTINCT
            UPPER(TRIM(cw_sku)) AS cw_sku,
            UPPER(TRIM(sku_match_key)) AS sku_match_group
        FROM RECON_SKU_MAP
        WHERE vendor = 'Webroot'
          AND cw_sku IS NOT NULL
          AND sku_match_key IN ('GSM', 'DNS', 'SAT')
    ),
    webroot_partner_bridge AS (
        SELECT
            'Webroot'::VARCHAR AS vendor,
            z.sf_id,
            z.billing_month::DATE AS billing_month,
            z.cms_id
        FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD z
        WHERE z.vendor = 'Webroot'
          AND z.sf_id IS NOT NULL
          AND z.cms_id IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY z.sf_id, z.billing_month
            ORDER BY z.invoice_number DESC NULLS LAST, z.cms_id
        ) = 1
    ),
    vendor_detail AS (
        SELECT DISTINCT
            d.vendor,
            d.sf_id,
            d.billing_month,
            d.sku_match_group,
            b.cms_id,
            DATEADD('day', vc.cycle_day - 1, d.billing_month)::DATE AS snapshot_date,
            DATEADD('day', vc.cycle_day - 1, DATEADD('month', -1, d.billing_month))::DATE AS prev_snapshot_date,
            su.cw_sku AS cw_sku_token
        FROM {DETAIL_TABLE_STAGE} d
        JOIN vendor_cycle vc
          ON vc.vendor = d.vendor
        JOIN webroot_partner_bridge b
          ON b.vendor = d.vendor
         AND b.sf_id = d.sf_id
         AND b.billing_month = d.billing_month
        JOIN webroot_sku_universe su
          ON su.sku_match_group = UPPER(TRIM(d.sku_match_group))
        WHERE d.vendor = 'Webroot'
          AND d.sf_id IS NOT NULL
          AND d.sku_match_group IS NOT NULL
    ),
    vendor_daily AS (
        SELECT
            p.vendor,
            p.sf_id,
            p.billing_month,
            p.sku_match_group,
            p.snapshot_date,
            u.on_date::DATE AS on_date,
            SUM(COALESCE(u.agent_cnt, 0)) AS day_quantity
        FROM vendor_detail p
        JOIN ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
          ON u.partner_id::VARCHAR = p.cms_id
         AND UPPER(TRIM(u.product_sku)) = p.cw_sku_token
         AND u.on_date::DATE > p.prev_snapshot_date
         AND u.on_date::DATE <= p.snapshot_date
         AND (
             (
                 UPPER(TRIM(u.product_description)) = 'DNS-SAT'
                 AND UPPER(TRIM(p.sku_match_group)) IN ('DNS', 'SAT')
             )
             OR
             (
                 UPPER(TRIM(u.product_sku)) IN (
                     'CMS-IH-CYBR-SOLP-SAAS-MDRSERVR',
                     'CMS-IH-CYBR-SOLP-SAAS-MDRDSKTP'
                 )
                 AND COALESCE(TRIM(u.product_description), '') <> ''
                 AND UPPER(TRIM(p.sku_match_group)) = 'GSM'
             )
         )
        GROUP BY 1, 2, 3, 4, 5, 6
    )
    SELECT
        vendor,
        sf_id,
        billing_month,
        sku_match_group,
        MAX(IFF(on_date = snapshot_date, day_quantity, NULL)) AS trt_quantity,
        AVG(day_quantity) AS avg_api_quantity
    FROM vendor_daily
    GROUP BY 1, 2, 3, 4
) a
WHERE d.vendor = a.vendor
  AND d.sf_id = a.sf_id
  AND d.billing_month = a.billing_month
    AND UPPER(TRIM(d.sku_match_group)) = UPPER(TRIM(a.sku_match_group));
"""

POST_OVERLAY_STRICT_RECLASS_SQL = f"""{USE}
UPDATE {DETAIL_TABLE_STAGE}
SET OUTCOME_FLAG = ({strict_outcome_case()});
"""

BITDEFENDER_MDR_BUNDLE_SQL = ""  # retired 2026-08-29: bundle overlay disabled.

WEBROOT_RMM_DISCOUNT_SQL = ""  # retired 2026-08-29: relied on THIRD_PARTY_RECON_SOURCE_TRT_PROD (dropped).


INV_ID_BACKFILL_SQL = f"""{USE}
UPDATE {DETAIL_TABLE_STAGE} d
SET INV_ID = z.inv_id
FROM (
        SELECT
                vendor,
                sf_id,
                billing_month,
            LISTAGG(DISTINCT invoice_number, ' | ')
                WITHIN GROUP (ORDER BY invoice_number) AS inv_id
        FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
        WHERE invoice_number IS NOT NULL
            AND sf_id IS NOT NULL
        GROUP BY 1,2,3
) z
WHERE d.INV_ID IS NULL
    AND d.VENDOR = z.vendor
    AND d.SF_ID = z.sf_id
    AND d.BILLING_MONTH = z.billing_month
    AND COALESCE(d.ZUORA_AMOUNT, 0) <> 0;

UPDATE {DETAIL_TABLE_STAGE} d
SET INV_ID = m.inv_id
FROM (
        SELECT
                vendor,
                sf_id,
                billing_month,
            LISTAGG(DISTINCT marketplace_invoice_id, ' | ')
                WITHIN GROUP (ORDER BY marketplace_invoice_id) AS inv_id
        FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
        WHERE marketplace_invoice_id IS NOT NULL
            AND sf_id IS NOT NULL
        GROUP BY 1,2,3
) m
WHERE d.INV_ID IS NULL
    AND d.VENDOR = m.vendor
    AND d.SF_ID = m.sf_id
    AND d.BILLING_MONTH = m.billing_month
    AND COALESCE(d.MARKETPLACE_AMOUNT, 0) <> 0;
"""

INIT_SQL = f"""{USE}
CREATE TABLE IF NOT EXISTS {DETAIL_TABLE_PROD} (
    VENDOR VARCHAR, BILLING_MONTH DATE, SF_ID VARCHAR, INV_ID VARCHAR, BILLING_TYPE VARCHAR,
    VENDOR_PARTNER_NAME VARCHAR, VENDOR_PRODUCT VARCHAR, SKU_MATCH_GROUP VARCHAR,
    CW_SKUS VARCHAR, ZUORA_SKUS VARCHAR, MARKETPLACE_SKUS VARCHAR, BILLING_SOURCE_MIX VARCHAR,
    API_QUANTITY FLOAT, AVG_API_QUANTITY FLOAT, VENDOR_QUANTITY FLOAT, VENDOR_UNIT_PRICE FLOAT,
    VENDOR_AMOUNT FLOAT, ZUORA_QUANTITY FLOAT, ZUORA_UNIT_PRICE FLOAT, ZUORA_AMOUNT FLOAT,
    MARKETPLACE_QUANTITY FLOAT, MARKETPLACE_UNIT_PRICE FLOAT, MARKETPLACE_AMOUNT FLOAT,
    TOTAL_BILLING_QUANTITY FLOAT, TOTAL_BILLING_AMOUNT FLOAT, QTY_DELTA FLOAT,
    ABS_QTY_DELTA FLOAT, AMOUNT_DELTA FLOAT, ABS_AMOUNT_DELTA FLOAT, CW_MARGIN_PCT FLOAT,
    HAS_DISCOUNT VARCHAR, DUPLICATE_BILLING_FLAG VARCHAR, OUTCOME_FLAG VARCHAR, INVESTIGATION_REASON VARCHAR,
    RECON_SUBGRAIN VARCHAR, NATIVE_OUTCOME_EVIDENCE VARCHAR, STRUCTURAL_EVIDENCE_CODE VARCHAR
);
ALTER TABLE {DETAIL_TABLE_PROD} ADD COLUMN IF NOT EXISTS RECON_SUBGRAIN VARCHAR;
ALTER TABLE {DETAIL_TABLE_PROD} ADD COLUMN IF NOT EXISTS NATIVE_OUTCOME_EVIDENCE VARCHAR;
ALTER TABLE {DETAIL_TABLE_PROD} ADD COLUMN IF NOT EXISTS STRUCTURAL_EVIDENCE_CODE VARCHAR;
CREATE OR REPLACE TABLE {DETAIL_TABLE_STAGE} LIKE {DETAIL_TABLE_PROD};
TRUNCATE TABLE {DETAIL_TABLE_STAGE};
"""

PUBLISH_DETAIL_SQL = f"""{USE}
-- Atomic publish: app keeps seeing the last good detail snapshot until
-- the staged rebuild is complete, then swaps to the new snapshot instantly.
ALTER TABLE {DETAIL_TABLE_PROD} SWAP WITH {DETAIL_TABLE_STAGE};
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


def main(*, staged_only: bool = False) -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        print("\n=== STEP 0: initialize staging detail table ===")
        if not run_sql(conn, INIT_SQL, f"init + truncate {DETAIL_TABLE_STAGE}"):
            return 1

        # 2026-08-31 architecture — governed layer auto-rebuilds every run:
        #
        # Source of truth for partner mapping is manually maintained directly in Snowflake:
        #   - THIRD_PARTY_RECON_PARTNER_MAP_PROD   (partner -> SF_ID / CMS / Zuora)
        #     Edit this table in Snowsight and it flows through automatically on
        #     the next pipeline run. No extra rebuild step required.
        #   - THIRD_PARTY_RECON_SKU_MAP_PROD       (vendor SKU -> CW SKU)
        #   - RECON_VENDOR_PARTNER_MANUAL_MAP      (vendor alias overrides)
        #   - RECON_PRICEBOOK                      (list price by SKU / tier)
        #
        # Downstream derived governed layer (rebuilt here every run, ~9s):
        #   - RECON_ACCOUNT_MERGE_RESOLVER, RECON_PARTNER_MAP, RECON_PARTNER_MAP_MONTHLY
        #     -> auto-rebuilt from THIRD_PARTY_RECON_PARTNER_MAP_PROD so that any
        #        new partner entry added in Snowflake is immediately live.
        #   - RECON_SKU_MAP, V_RECON_PARTNER_MAP_MONTHLY_NORM,
        #     V_RECON_PRICEBOOK_TIER_LOOKUP -> live views (always auto-fresh).
        #
        # NOTE: STEP 0a ONLY reads from the mapping tables and writes to the
        # derived governed layer. It never modifies THIRD_PARTY_RECON_PARTNER_MAP_PROD
        # or any other manually-maintained table.
        print("\n=== STEP 0a: rebuild governed partner map from THIRD_PARTY_RECON_PARTNER_MAP_PROD ===")
        if not run_repo_sql_file(
            conn,
            r"Maps\sql\02_unified_reference_maps.sql",
            "rebuild RECON_PARTNER_MAP + RECON_PARTNER_MAP_MONTHLY (picks up new partner entries)",
        ):
            return 1

        print("\n=== STEP 0b: rebuild Bitdefender vendor usage from PRODUCT_MANAGEMENT__ROYALTIES ===")
        # Native replacement for the deprecated Excel-based ingestion. Populates
        # THIRD_PARTY_RECON_VENDOR_USAGE_PROD Bitdefender rows directly from
        # ANALYTICS.DBO.PRODUCT_MANAGEMENT__ROYALTIES (Contract + Usage + prior-month
        # Marketplace + CW MDR bundle split into ATS_EDR + GRAVITYZONE rows).
        if not run_repo_sql_file(
            conn,
            r"Reconciliation\00_bitdefender_vendor_usage_rebuild.sql",
            "rebuild THIRD_PARTY_RECON_VENDOR_USAGE_PROD Bitdefender rows (native royalties)",
        ):
            return 1

        print("\n=== STEP 0c: enrich vendor usage with canonical invoice rates ===")
        # This must run after vendor usage and invoice parsing, but before any
        # vendor reconciliation SQL consumes UNIT_PRICE and AMOUNT.
        if not run_repo_sql_file(
            conn,
            r"Maps\sql\00b_backfill_invoice_prices.sql",
            "backfill vendor usage UNIT_PRICE + AMOUNT from canonical invoices",
        ):
            return 1

        print("\n=== STEP 1a: run live vendor SQL files (rebuild <VENDOR>_RECON_DETAIL) ===")
        sql_fail: dict[str, str] = {}
        live_vendors = [v for v, (m, _) in VENDOR_ROUTING.items() if m == "live"]
        for vendor in live_vendors:
            ok = run_vendor_sql_file(conn, vendor)
            if not ok:
                sql_fail[vendor] = "vendor SQL raised an error"

        print("\n=== STEP 1b: emit each vendor into DETAIL_PROD ===")
        emit_live: list[str] = []
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
            if not emitted:
                emit_fail.append(vendor)

        print("\n  live vendors:     " + (", ".join(emit_live) or "(none)"))
        if emit_fail:
            print("  FAILED vendors:   " + ", ".join(emit_fail))
            print("  ABORTING: staged detail will not be published.")
            return 1

        print("\n=== STEP 2: overlays on the shared table (per-vendor) ===")
        if not run_sql(conn, API_BACKFILL_SQL, "backfill API_QUANTITY / AVG_API_QUANTITY (Webroot product-scoped cycle snapshots)"):
            return 1
        if not run_sql(conn, POST_OVERLAY_STRICT_RECLASS_SQL, "recompute canonical OUTCOME_FLAG after API overlays"):
            return 1
        # Proofpoint API_QUANTITY / AVG_API_QUANTITY is now sourced inline
        # inside Proofpoint_Reconciliation_Script_Prod.sql (proofpoint_api_usage
        # CTE, direct-from-raw architecture 2026-08-28). The old backfill step
        # against THIRD_PARTY_RECON_SOURCE_TRT_PROD is retired.
        if not run_sql(conn, INV_ID_BACKFILL_SQL, "backfill INV_ID from Zuora billing source"):
            return 1

        print("\n=== STEP 2b: build vendor invoice vs raw usage control (invoice gate) ===")
        if not run_repo_sql_file(
            conn,
            r"Reconciliation\10_vendor_invoice_usage_intra_prod.sql",
            "build THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD",
        ):
            return 1

        if staged_only:
            print("\n=== STAGED-ONLY MODE: shared production publication skipped ===")
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT VENDOR) FROM {DETAIL_TABLE_STAGE}")
            staged_result = cur.fetchone()
            staged_rows, staged_vendors = staged_result if staged_result is not None else (0, 0)
            print(f"  {DETAIL_TABLE_STAGE}: {staged_rows:>7,} rows across {staged_vendors} vendors")
            print(f"  {DETAIL_TABLE_PROD} and THIRD_PARTY_RECON_OUTPUT_PROD remain unchanged.")
            return 0

        print("\n=== STEP 2c: atomically publish staged detail table ===")
        if not run_sql(conn, PUBLISH_DETAIL_SQL, f"publish {DETAIL_TABLE_PROD} from {DETAIL_TABLE_STAGE}"):
            return 1

        print("\n=== STEP 3: build THIRD_PARTY_RECON_OUTPUT_PROD (classifier) ===")
        # The single strict classifier turns DETAIL_PROD into the app-facing
        # OUTPUT_PROD shape and builds THIRD_PARTY_RECON_SUMMARY_PROD.
        result = subprocess.run(
            [sys.executable, str(REPO / "Reconciliation" / "build_third_party_recon_output_prod.py")],
            capture_output=True, text=True, cwd=str(REPO / "Reconciliation"),
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
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT VENDOR) FROM THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD")
        intra_rows, intra_vendors = cur.fetchone()
        print(f"  DETAIL_PROD:  {det_rows:>7,} rows across {det_vendors} vendors")
        print(f"  OUTPUT_PROD:  {out_rows:>7,} rows across {out_vendors} vendors")
        print(f"  INTRA_PROD:   {intra_rows:>7,} rows across {intra_vendors} vendors")

        print("\n  Publication link integrity:")
        cur.execute("""
            SELECT COUNT_IF(
                       COALESCE(TRIM(INVOICE_ID), '') <> ''
                       AND COALESCE(TRIM(NETSUITE_URL), '') = ''
                   )
            FROM THIRD_PARTY_RECON_VENDOR_INVOICES
        """)
        missing_invoice_links = int(cur.fetchone()[0] or 0)
        cur.execute("""
            SELECT COUNT_IF(
                       COALESCE(TRIM(SF_ID), '') <> ''
                       AND COALESCE(TRIM(SALESFORCE_ACCOUNT_URL), '') = ''
                   )
            FROM THIRD_PARTY_RECON_OUTPUT_PROD
        """)
        missing_salesforce_links = int(cur.fetchone()[0] or 0)
        print(f"    invoice rows missing NetSuite URL : {missing_invoice_links:>7,}")
        print(f"    mapped output rows missing SF URL : {missing_salesforce_links:>7,}")
        if missing_invoice_links or missing_salesforce_links:
            print("    LINK INTEGRITY GATE: FAIL")
            return 1
        print("    LINK INTEGRITY GATE: PASS")

        print("\n  OUTCOME_FLAG in DETAIL_PROD (strict canonical taxonomy):")
        cur.execute("""
            SELECT OUTCOME_FLAG, COUNT(*) FROM THIRD_PARTY_RECON_DETAIL_PROD
            GROUP BY 1 ORDER BY 2 DESC
        """)
        for f, n in cur.fetchall():
            print(f"    {str(f):<50} {n:>8,}")

        print("\n  EXCEPTION_TYPE in OUTPUT_PROD (same strict canonical taxonomy):")
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
        pp_total = int(pp_total or 0)
        pp_clear = int(pp_clear or 0)
        pp_pct_display = "n/a" if pp_pct is None else f"{float(pp_pct):.1f}%"
        print(f"    total rows           : {pp_total:>7,}")
        print(f"    clear rows           : {pp_clear:>7,}   ({pp_pct_display})")
        print(f"    vendor $             : ${(pp_v or 0):>14,.0f}")
        print(f"    billing $            : ${(pp_b or 0):>14,.0f}")
        parity_ok = pp_pct is not None and 90.0 <= float(pp_pct) <= 97.0
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
    parser = argparse.ArgumentParser(description="Run the nine-vendor reconciliation skeleton.")
    parser.add_argument(
        "--staged-only",
        action="store_true",
        help="Build and validate staging tables without publishing shared production detail/output.",
    )
    args = parser.parse_args()
    raise SystemExit(main(staged_only=args.staged_only))
