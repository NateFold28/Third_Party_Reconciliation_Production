"""
Build THIRD_PARTY_RECON_OUTPUT_PROD and THIRD_PARTY_RECON_SUMMARY.

Reads from THIRD_PARTY_RECON_DETAIL_PROD (all vendors, canonical OUTCOME_FLAG)
and produces:

  THIRD_PARTY_RECON_OUTPUT_PROD   - full detail + EXCEPTION_TYPE + EST_DOLLAR_IMPACT
  THIRD_PARTY_RECON_SUMMARY       - per-vendor-month KPI rollup for the app

Canonical EXCEPTION_TYPE taxonomy (mutually exclusive buckets, priority order):
  1.  Unmapped Partner                     — no valid SF_ID
  2.  Clear                                — CW amount >= vendor amount (always clear regardless of bundle flag)
  2b. Clear (partner-month rollup)         — CW/vendor totals reconcile at partner-month grain
  3.  Duplicated CW Invoice                — DUPLICATE_BILLING_FLAG=TRUE (vendor-specific detector)
  4.  Known Discount / Bundle              — HAS_DISCOUNT=TRUE AND amounts DON'T already reconcile
                                             (Amit-defined Clear Internal: variance is intentional bundle/discount)
  5.  Marketplace Billing Delay            — prior-period timing artifact
  6.  API Usage Recorded, No CW Billing    — API qty > 0 at partner-month grain, CW = 0
  7.  Vendor SKU, No CW SKU                — vendor product has no CW rebill SKU
  8.  CW SKU, No Vendor SKU                — CW subscription has no vendor counterpart
  9.  Vendor Billing, No CW Billing        — vendor_amount > 0, cw_amount = 0
  10. CW Billing, No Vendor Billing        — cw_amount > 0, vendor_amount = 0
  11. Vendor Billing, Insufficient CW Billing  — vendor > CW by >25%, both have billing
  12. Clear (minor drift)                  — vendor > CW by 0-25%, both > 0 (Proofpoint tolerance band)
  13. Clear (both-zero)                    — both sides $0 (audit-trail rows with no exposure)
  14. Other Issue                          — catch-all (should be empty after 12/13)

Design rules:
  - Clear takes precedence over Discount (audit 2026-08-20). Prior version fired
    Known Discount / Bundle BEFORE Clear whenever HAS_DISCOUNT=TRUE, reclassifying
    ~1,900 already-reconciled rows.
  - Minor drift (rule 12) and both-zero (rule 13) added 2026-08-20b to empty
    the Other Issue bucket. Manual recon treats 0-25% variance as Clear.
  - SF_ID resolved via CW_DW__MERGED_ACCOUNT_MAP so deprecated account ids
    roll up to the surviving SF_ID (Proofpoint-style unification, now global).
  - FX rates and currency conversion applied globally at THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
    build time in sql/01_unified_billing_sources.sql (fpa_budget_exchange_rates).

"CW Billing, Insufficient Vendor Billing" is intentionally REMOVED from the taxonomy
and folded into "Clear" — if CW is collecting more than vendor charges, margin is positive.
"""
from __future__ import annotations
import sys, time
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
# Global internal / test partner exclusion — Proofpoint mechanism, globalized
# ---------------------------------------------------------------------------
# Proofpoint_Vendor_Usage_Ingestion_Prod.py drops rows at ingest whose partner
# name matches ConnectWise's own accounts or Proofpoint's internal accounts.
# We apply the same idea globally at OUTPUT_PROD build time — any partner
# whose name matches one of these patterns is filtered out entirely (they
# would otherwise land in Unmapped Partner and inflate the exception bucket).
#
# Patterns are Snowflake RLIKE (POSIX ERE) — full-string match. Use ".*" for
# starts-with / contains semantics. Keep tight — only obvious internal
# and test accounts. Real customers named 'Sedona Technologies', 'DuraVent',
# etc. stay in Unmapped Partner so the operator can chase the KeepIT / vendor
# partner-map gap upstream.
#
# Impact (Jun 2026):
#   KeepIT ConnectWise (Continuum) - Consolidated : 6 rows, $329,031
#   KeepIT PM Continuum / PM ConnectWise           : 2 rows, $20
#   KeepIT DevQAPune / Test123 / RecoverProd*      : 3 rows, $109
#   KeepIT Recover Continuum                        : 1 row,  $36
# ---------------------------------------------------------------------------
INTERNAL_TEST_PARTNER_PATTERNS = [
    r"connectwise",                            # ConnectWise exact
    r"connectwise[ ,\-].*",                    # ConnectWise itself (any variant)
    r".*connectwise.*consolidat.*",            # "ConnectWise (Continuum) - Consolidated"
    r".*connectwise[- ]corporate.*",           # "CONNECTWISE-CORPORATE" bundles
    r"cw[ \-].*",                              # "cw-*" internal SKU accounts
    r"cw dev .*",                              # "CW DEV Account N"
    r"pm (continuum|connectwise).*",           # Internal PM test accounts
    r"dev[ _\-]?qa.*",                         # DevQA*, DevQAPune
    r"recoverprod[0-9]+",                      # RecoverProd01, RecoverProd02, ...
    r"recover continuum",                      # Internal continuity testing
    r"test[0-9]+",                             # Test123, Test01, ...
    r"co-managed backup testing",              # Acronis internal
    r".*(exium|s1|sentinelone) test account.*",  # Vendor test accounts
    r"(acronis|auvik|bitdefender|eset|exium|keepit|proofpoint|sentinelone|webroot)[ ,\-].*",  # vendor billing itself
]
_EXCLUSION_REGEX = "(" + "|".join(INTERNAL_TEST_PARTNER_PATTERNS) + ")"

# ---------------------------------------------------------------------------
# Canonical EXCEPTION_TYPE CASE expression
# ---------------------------------------------------------------------------
# Evaluated in the context of THIRD_PARTY_RECON_DETAIL_PROD.
# OUTCOME_FLAG has already been normalized to canonical values by Step 1e
# in _run_reports.py, but this CASE also handles any residual old values
# for backward compatibility with data built before this pipeline version.
# ---------------------------------------------------------------------------
CANONICAL_EXCEPTION_TYPE = """
CASE
    -- ── 1. Unmapped Partner ────────────────────────────────────────────────
    -- No valid SF_ID means the account can't be linked to any CW billing row.
    -- KeepIT and Auvik pre-write synthetic 'UNMAPPED_<name>_...' ids for
    -- vendor rows they can't resolve — treat those as Unmapped Partner too
    -- (they are the primary driver of KeepIT's "Vendor SKU, No CW SKU" and
    -- similar false-mapping buckets).
    WHEN (SF_ID IS NULL
          OR UPPER(TRIM(COALESCE(SF_ID, ''))) IN ('', 'UNKNOWN', 'NONE', 'UNMAPPED', 'NULL')
          OR STARTSWITH(UPPER(TRIM(COALESCE(SF_ID, ''))), 'UNMAPPED_')
          OR STARTSWITH(UPPER(TRIM(COALESCE(SF_ID, ''))), 'UNMAPPED-')
          OR STARTSWITH(UPPER(TRIM(COALESCE(SF_ID, ''))), 'UNMAPPED '))
         OR OUTCOME_FLAG IN ('Unmapped Partner', 'Unmapped SKU', 'PARTNER_MAPPING_REQUIRED')
    THEN 'Unmapped Partner'

    -- ── 2. Clear (checked BEFORE Discount) ─────────────────────────────────
    -- CW amount >= vendor amount is always "Clear" regardless of bundle flag.
    -- The Known Discount / Bundle bucket only exists to EXPLAIN variance — if
    -- amounts already reconcile there is nothing to explain. Both sides must
    -- have real billing; zero-side cases are handled by the vendor-only /
    -- CW-only buckets further down. This ordering matches manual recon's
    -- treatment where any row with matching amounts is filed as Clear
    -- (and Clear Internal only picks up unfavorable-variance-under-bundle rows).
    WHEN COALESCE(TOTAL_BILLING_AMOUNT, 0) >= COALESCE(VENDOR_AMOUNT, 0)
         AND COALESCE(VENDOR_AMOUNT, 0) > 0
    THEN 'Clear'

    -- ── 2a. Clear — vendor credit (VENDOR_AMOUNT < 0) ────────────────────
    -- Vendor issued a genuine credit (not zero — CW-only rows go to Rule 10).
    -- Auvik occasionally posts negative overage rows for consumption reversals.
    -- If vendor is negative there is no leakage regardless of CW side.
    WHEN COALESCE(VENDOR_AMOUNT, 0) < 0
    THEN 'Clear'

    -- ── 2b. Clear at partner-month grain (SKU-mismatch rollup) ────────────
    -- Row-level shows CW-only OR Vendor-only but the partner-month totals
    -- reconcile: vendor charged us on SKU A, CW rebilled the partner on SKU B,
    -- and the partner-month CW total >= partner-month vendor total. Manual
    -- recon treats these as Clear at the account level, and per user directive
    -- these should not fire Vendor-only / CW-only / Insufficient CW flags.
    WHEN SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0))
              OVER (PARTITION BY VENDOR, SF_ID, BILLING_MONTH)
         >= SUM(COALESCE(VENDOR_AMOUNT, 0))
              OVER (PARTITION BY VENDOR, SF_ID, BILLING_MONTH)
         AND SUM(COALESCE(VENDOR_AMOUNT, 0))
              OVER (PARTITION BY VENDOR, SF_ID, BILLING_MONTH) > 0
    THEN 'Clear'

    -- ── 3. Duplicated CW Invoice ───────────────────────────────────────────
    -- The pipeline-set DUPLICATE_BILLING_FLAG is the authoritative signal.
    -- Do NOT use the raw ZUORA_AMOUNT > 0 AND MARKETPLACE_AMOUNT > 0 check:
    -- many vendors legitimately bill through both channels for different SKUs
    -- (S1 license + MDR bundle, Webroot license + RMM bundle). Using amount-based
    -- logic would incorrectly classify hundreds of valid rows as duplicates.
    WHEN COALESCE(DUPLICATE_BILLING_FLAG, 'FALSE') = 'TRUE'
         OR OUTCOME_FLAG IN ('Duplicated CW Invoice', 'Duplicate Billing', 'DUPLICATE_BILLING')
    THEN 'Duplicated CW Invoice'

    -- ── 4. Known Discount / Bundle (Amit "Clear Internal") ─────────────────
    -- Intentional pricing — MDR bundle, RMM bundle discount, CW-included zero-dollar
    -- line, etc. Only fires when amounts DON'T already reconcile (Clear caught those
    -- above). This is the manual recon team's "Clear Internal" bucket.
    --
    -- Explicit bundle-SKU detection (added 2026-08-20c): when CW_SKUS contains
    -- known bundle markers and vendor didn't bill separately, this is a bundled
    -- entitlement (CW is charging the partner for a service included in the
    -- RMM SuperBundle or 3-year promo bundle — vendor is paid via bundle
    -- economics, not a separate line). Catches KeepIT ~$472K M365/Google/Azure
    -- backup rides inside CW-RMM-SB / M2M-RMM-SB / *-3P-UMM / *-EG-UMM bundles.
    WHEN COALESCE(HAS_DISCOUNT, 'FALSE') = 'TRUE'
         OR OUTCOME_FLAG IN (
             'Known Discount / Bundle', 'Clear - Discounted / Bundled',
             'RMM_DISCOUNTED', 'KNOWN_DISCOUNT_BUNDLE', 'MDR_BUNDLE',
             'CW_INCLUDED_ZERO_DOLLAR', 'INTENTIONAL_DISCOUNT'
         )
         OR (COALESCE(VENDOR_AMOUNT, 0) = 0
             AND COALESCE(TOTAL_BILLING_AMOUNT, 0) > 0
             AND (
                 UPPER(COALESCE(CW_SKUS, '')) LIKE '%RMM-SB-%'
              OR UPPER(COALESCE(CW_SKUS, '')) LIKE '%-3Y-PROMO-%'
              OR UPPER(COALESCE(CW_SKUS, '')) LIKE '%PROMO-BUNDLE%'
              OR UPPER(COALESCE(CW_SKUS, '')) LIKE '%3P-UMM-BCDR-SAAS%'
              OR UPPER(COALESCE(CW_SKUS, '')) LIKE '%EG-UMM-SOLP-SAAS%'
              OR UPPER(COALESCE(CW_SKUS, '')) LIKE '%EG-BDR-SOLP-SAAS%'
             ))
    THEN 'Known Discount / Bundle'

    -- ── 5. Marketplace Billing Delay ──────────────────────────────────────
    -- Prior-period Marketplace invoice timing artifact; will self-resolve.
    WHEN OUTCOME_FLAG IN (
        'Marketplace Billing Delay', 'MARKETPLACE_TIMING', 'BILLING_TIMING_ADJACENT_MONTH'
    )
    THEN 'Marketplace Billing Delay'

    -- ── 6. API Usage Recorded, No CW Billing ──────────────────────────────
    -- TRT / API confirms active usage but THIS ROW has no CW billing.
    -- Check at row level: if API_QUANTITY > 0 (vendor has API activity),
    -- and this specific product row has ZERO CW billing, then it's an API-driven
    -- gap (vendor's product is active but CW hasn't billed it yet).
    --
    -- NOTE: API_QUANTITY is backfilled to all rows for a partner-month, so
    -- a partner-month with API activity on Product A will have that same
    -- API_QUANTITY copied to Product B rows (even if B has no usage).
    -- To avoid false positives, ONLY fire this rule if THIS ROW's vendor
    -- activity matches the API activity (i.e., VENDOR_SEATS > 0 at row grain).
    -- If VENDOR_SEATS = 0 but API_QUANTITY > 0, the usage is NOT on this product,
    -- so it falls to Rule 9 (Vendor Billing / CW Billing mismatch).
    WHEN (COALESCE(VENDOR_SEATS, 0) > 0
          AND COALESCE(API_QUANTITY, 0) > 0
          AND COALESCE(TOTAL_BILLING_QUANTITY, 0) = 0
          AND COALESCE(TOTAL_BILLING_AMOUNT, 0) = 0)
         OR OUTCOME_FLAG IN (
             'API Usage Recorded, No CW Billing',
             'Missing CW Billing - API Confirmed',
             'TRT_VENDOR_USAGE_NOT_BILLED',
             'STRUCTURAL_VENDOR_ONLY_TRT_CONFIRMED'
         )
    THEN 'API Usage Recorded, No CW Billing'

    -- ── 7. Vendor SKU, No CW SKU ──────────────────────────────────────────
    -- Vendor is charging CW for a product that has no CW rebill SKU.
    -- Includes old 'Unmapped SKU' rows where the partner IS mapped (SF_ID valid)
    -- but the product/SKU is missing from the catalog.
    WHEN OUTCOME_FLAG IN (
        'Vendor SKU, No CW SKU', 'VENDOR_ADDON_NO_CW_SKU',
        'VENDOR_PRODUCT_NO_CW_SKU', 'VENDOR_SKU_NO_CW_SKU',
        'SKU_MISMATCH_BILLING_ON_OTHER_SKU'
    )
    THEN 'Vendor SKU, No CW SKU'

    -- ── 8. CW SKU, No Vendor SKU ──────────────────────────────────────────
    -- CW billed the partner on a SKU the vendor has no counterpart for.
    WHEN OUTCOME_FLAG IN (
        'CW SKU, No Vendor SKU', 'CW_ONLY_ADDON_NO_VENDOR', 'CW_SKU_NO_VENDOR_SKU'
    )
    THEN 'CW SKU, No Vendor SKU'

    -- ── 9. Vendor Billing, No CW Billing ──────────────────────────────────
    -- Vendor is charging CW for this account/product; CW has no positive
    -- billing at the row level (CW=0 OR CW<0 credit balance). If the
    -- partner-month has CW billing on OTHER rows, this becomes a SKU-mismatch
    -- scenario handled by Rule 2b at partner-month grain.
    WHEN COALESCE(VENDOR_AMOUNT, 0) > 0 AND COALESCE(TOTAL_BILLING_AMOUNT, 0) <= 0
    THEN 'Vendor Billing, No CW Billing'

    -- ── 10. CW Billing, No Vendor Billing ──────────────────────────────────
    -- CW has billing for this account/product; vendor charges CW nothing.
    WHEN COALESCE(TOTAL_BILLING_AMOUNT, 0) > 0 AND COALESCE(VENDOR_AMOUNT, 0) = 0
    THEN 'CW Billing, No Vendor Billing'

    -- ── 11. Vendor Billing, Insufficient CW Billing ───────────────────────
    -- Vendor charges CW materially more than CW bills the partner (>25% gap).
    -- Both sides must have real billing — zero on either side is handled above.
    -- Guaranteed mutually exclusive with "API Usage Recorded, No CW Billing"
    -- because that rule requires partner-month CW = 0.
    WHEN COALESCE(VENDOR_AMOUNT, 0) > 0
         AND COALESCE(TOTAL_BILLING_AMOUNT, 0) > 0
         AND COALESCE(VENDOR_AMOUNT, 0) > COALESCE(TOTAL_BILLING_AMOUNT, 0) * 1.25
    THEN 'Vendor Billing, Insufficient CW Billing'

    -- ── 12. Clear — minor drift within tolerance ───────────────────────────
    -- Vendor > CW by 0-25% AND both sides have real billing. Manual recon
    -- treats this as Clear (matches Proofpoint "CLEAR: billing quantity
    -- within 2% (or 5 units)" and Amit's "minor drift" band). Prior version
    -- fell through to Other Issue, absorbing ~150 rows across Auvik, Webroot,
    -- KeepIT, ESET, Proofpoint that all had OUTCOME_FLAG='Clear' internally.
    WHEN COALESCE(VENDOR_AMOUNT, 0) > 0
         AND COALESCE(TOTAL_BILLING_AMOUNT, 0) > 0
         AND COALESCE(VENDOR_AMOUNT, 0) <= COALESCE(TOTAL_BILLING_AMOUNT, 0) * 1.25
    THEN 'Clear'

    -- ── 13. Clear — both sides zero at row level, partner-month rolls up ──
    -- Row-level V=0 and CW=0 rows exist because the recon detail table carries
    -- one row per (partner, product) even for zero-amount lines (audit trail).
    -- If there is real activity elsewhere for the partner-month, absorbing
    -- these into Clear is correct; if the entire partner-month is zero, this
    -- classifies as Clear (no reconciliation exposure). Cannot fire when the
    -- prior rules found any variance, so this is a safe catch-all for zero-noise.
    WHEN COALESCE(VENDOR_AMOUNT, 0) = 0
         AND COALESCE(TOTAL_BILLING_AMOUNT, 0) = 0
    THEN 'Clear'

    -- ── 14. Other Issue ───────────────────────────────────────────────────
    ELSE 'Other Issue'
END
""".strip()


# ---------------------------------------------------------------------------
# Pre-computed helper columns (2026-08-21 latency pass)
# ---------------------------------------------------------------------------
# These columns move classification / labeling / grouping work from the app
# into the pipeline so the Streamlit dashboard skips per-row Python logic
# on every filter change. All of them are pure functions of EXCEPTION_TYPE
# and existing IDs — cheap to compute in SQL and cached at read time.
#
# ACTION_NEEDED       — plain-English recon-team next step per bucket
# IS_LEAKAGE          — Finance Queue leakage (buckets 6/7/9/11)
# IS_FINANCE_QUEUE    — Finance Queue tile cohort
# IS_OPS_QUEUE        — Ops Review tile cohort
# IS_TIMING_QUEUE     — Timing-only tile cohort
# IS_CLEAR            — Clear rows (skip in exception views)
# CASE_ID             — stable id used by the Recon Team Queue tab
# ---------------------------------------------------------------------------
ACTION_NEEDED_CASE = """
CASE EXCEPTION_TYPE
    WHEN 'Clear'                                        THEN 'None'
    WHEN 'Unmapped Partner'                             THEN 'Data team: update partner mapping'
    WHEN 'Duplicated CW Invoice'                        THEN 'Billing Ops: cancel duplicate invoice line'
    WHEN 'Marketplace Billing Delay'                    THEN 'No action - prior-month invoice expected next cycle'
    WHEN 'Known Discount / Bundle'                      THEN 'No action - intentional discount or bundle pricing'
    WHEN 'Vendor SKU, No CW SKU'                        THEN 'Product / Catalog: add a CW rebill SKU for this vendor product'
    WHEN 'CW SKU, No Vendor SKU'                        THEN 'Ops: verify whether this CW rebill SKU should still be active'
    WHEN 'API Usage Recorded, No CW Billing'            THEN 'Finance: create billing for TRT-confirmed endpoint usage'
    WHEN 'Vendor Billing, No CW Billing'                THEN 'Finance / Sales: onboard billing - vendor charged CW with no CW rebill to partner'
    WHEN 'CW Billing, No Vendor Billing'                THEN 'Ops: verify vendor-side attribution or retire the stale CW subscription'
    WHEN 'Vendor Billing, Insufficient CW Billing'      THEN 'Finance / Sales: close billing gap - vendor materially ahead of CW'
    ELSE 'Review required'
END
""".strip()

FINANCE_QUEUE_BUCKETS_SQL = (
    "'Vendor Billing, No CW Billing', "
    "'Vendor Billing, Insufficient CW Billing', "
    "'API Usage Recorded, No CW Billing', "
    "'Vendor SKU, No CW SKU'"
)
OPS_QUEUE_BUCKETS_SQL = (
    "'CW Billing, No Vendor Billing', "
    "'CW SKU, No Vendor SKU', "
    "'Duplicated CW Invoice', "
    "'Vendor SKU, No CW SKU', "
    "'Unmapped Partner'"
)


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


conn = get_snowflake_connection(
    role="DEVELOPER", warehouse="REPORTING_WH",
    database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
)
try:
    # ── Build THIRD_PARTY_RECON_OUTPUT_PROD ───────────────────────────────
    # SELECT * from the unified detail table and add:
    #   EXCEPTION_TYPE  — canonical 14-bucket classification (2 new Clear rules)
    #   EST_DOLLAR_IMPACT — ABS(amount_delta) precomputed for dashboard efficiency
    #   SF_ID_RESOLVED — surviving SFDC id after merged-account rollup
    # VENDOR_SOURCE_ROW_COUNT defaults to 1 so ghost-month logic works correctly
    # (all rows in THIRD_PARTY_RECON_DETAIL_PROD came from a real vendor file).
    #
    # SF_ID_RESOLVED: applied globally per Proofpoint mechanism. When SFDC merges
    # two accounts the old id is deprecated and vendor files may still reference
    # the old id — LEFT JOIN CW_DW__MERGED_ACCOUNT_MAP swaps old_account for
    # new_account so downstream rules see the canonical SF_ID. The original SF_ID
    # is preserved as SF_ID_ORIGINAL for audit trail.
    output_sql = f"""{USE}
CREATE OR REPLACE TABLE THIRD_PARTY_RECON_OUTPUT_PROD AS
WITH filtered AS (
    -- Global internal / test partner exclusion (Proofpoint mechanism).
    -- Drop rows whose VENDOR_PARTNER_NAME matches the internal/test regex.
    -- Preserved in THIRD_PARTY_RECON_DETAIL_PROD if audit needed.
    -- Snowflake RLIKE requires implicit anchoring and does NOT support (?i);
    -- lowercase the input and use case-insensitive patterns.
    SELECT *
    FROM THIRD_PARTY_RECON_DETAIL_PROD
    WHERE NOT RLIKE(LOWER(COALESCE(VENDOR_PARTNER_NAME, '')),
                    '{_EXCLUSION_REGEX}')
), resolved AS (
    SELECT
        d.* EXCLUDE (SF_ID),
        d.SF_ID                              AS SF_ID_ORIGINAL,
        COALESCE(m.NEW_ACCOUNT, d.SF_ID)     AS SF_ID
    FROM filtered d
    LEFT JOIN ANALYTICS.DBO.CW_DW__MERGED_ACCOUNT_MAP m
        ON UPPER(TRIM(d.SF_ID)) = UPPER(TRIM(m.OLD_ACCOUNT))
       AND m.NEW_ACCOUNT IS NOT NULL
), classified AS (
    SELECT
        *,
        {CANONICAL_EXCEPTION_TYPE}                         AS EXCEPTION_TYPE,
        ABS(COALESCE(AMOUNT_DELTA, 0))                     AS EST_DOLLAR_IMPACT,
        1::NUMBER                                          AS VENDOR_SOURCE_ROW_COUNT
    FROM resolved
)
-- App-facing precomputed columns (2026-08-21 latency pass): these move the
-- per-row classification / label / group-id work out of the Streamlit
-- app and into Snowflake so tab / filter changes stay O(1) in Python.
SELECT
    *,
    {ACTION_NEEDED_CASE}                                                                AS ACTION_NEEDED,
    CASE WHEN EXCEPTION_TYPE IN ({FINANCE_QUEUE_BUCKETS_SQL}) THEN TRUE ELSE FALSE END  AS IS_LEAKAGE,
    CASE WHEN EXCEPTION_TYPE IN ({FINANCE_QUEUE_BUCKETS_SQL}) THEN TRUE ELSE FALSE END  AS IS_FINANCE_QUEUE,
    CASE WHEN EXCEPTION_TYPE IN ({OPS_QUEUE_BUCKETS_SQL})     THEN TRUE ELSE FALSE END  AS IS_OPS_QUEUE,
    CASE WHEN EXCEPTION_TYPE = 'Marketplace Billing Delay'    THEN TRUE ELSE FALSE END  AS IS_TIMING_QUEUE,
    CASE WHEN EXCEPTION_TYPE = 'Clear'                        THEN TRUE ELSE FALSE END  AS IS_CLEAR,
    -- Stable Case ID matches the app's Recon Team Queue key so team edits
    -- persist across filter changes without an app-side apply() loop.
    CONCAT_WS(
        '|',
        COALESCE(VENDOR, ''),
        COALESCE(SF_ID, ''),
        COALESCE(VENDOR_PRODUCT, ''),
        TO_CHAR(BILLING_MONTH, 'YYYY-MM'),
        COALESCE(EXCEPTION_TYPE, '')
    )                                                                                    AS CASE_ID
FROM classified;
"""
    run_sql(conn, output_sql, "THIRD_PARTY_RECON_OUTPUT_PROD")

    # ── Build THIRD_PARTY_RECON_SUMMARY ───────────────────────────────────
    # App reads this table for the per-vendor-month KPI tiles.
    # PERFECT_MATCH_ROWS = rows classified as 'Clear'.
    summary_sql = f"""{USE}
CREATE OR REPLACE TABLE THIRD_PARTY_RECON_SUMMARY AS
SELECT
    VENDOR,
    BILLING_MONTH,
    COUNT(*)                                                                          AS TOTAL_ROWS,
    COUNT_IF(EXCEPTION_TYPE = 'Clear')                                                AS PERFECT_MATCH_ROWS,
    SUM(COALESCE(VENDOR_QUANTITY, 0))                                                 AS TOTAL_VENDOR_SEATS,
    SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0))                                          AS TOTAL_BILLING_SEATS,
    ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2)                                         AS TOTAL_VENDOR_AMOUNT,
    ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2)                                  AS TOTAL_BILLING_AMOUNT,
    ROUND(COUNT_IF(EXCEPTION_TYPE = 'Clear') * 100.0 / NULLIF(COUNT(*), 0), 1)       AS CLEAR_PCT,
    -- Exception counts for drill-down reference
    COUNT_IF(EXCEPTION_TYPE = 'Unmapped Partner')                                     AS UNMAPPED_PARTNER_ROWS,
    COUNT_IF(EXCEPTION_TYPE = 'Duplicated CW Invoice')                                AS DUPLICATE_INVOICE_ROWS,
    COUNT_IF(EXCEPTION_TYPE = 'Known Discount / Bundle')                              AS KNOWN_DISCOUNT_ROWS,
    COUNT_IF(EXCEPTION_TYPE = 'Marketplace Billing Delay')                            AS TIMING_ROWS,
    COUNT_IF(EXCEPTION_TYPE = 'API Usage Recorded, No CW Billing')                    AS API_NO_CW_ROWS,
    COUNT_IF(EXCEPTION_TYPE = 'Vendor SKU, No CW SKU')                                AS VENDOR_SKU_NO_CW_ROWS,
    COUNT_IF(EXCEPTION_TYPE = 'CW SKU, No Vendor SKU')                                AS CW_SKU_NO_VENDOR_ROWS,
    COUNT_IF(EXCEPTION_TYPE = 'Vendor Billing, No CW Billing')                        AS VENDOR_NO_CW_ROWS,
    COUNT_IF(EXCEPTION_TYPE = 'CW Billing, No Vendor Billing')                        AS CW_NO_VENDOR_ROWS,
    COUNT_IF(EXCEPTION_TYPE = 'Vendor Billing, Insufficient CW Billing')              AS VENDOR_INSUFF_CW_ROWS,
    COUNT_IF(EXCEPTION_TYPE = 'Other Issue')                                          AS OTHER_ISSUE_ROWS,
    -- Revenue leakage total (Finance Queue buckets: vendor>0 but CW under-billed)
    ROUND(SUM(CASE WHEN EXCEPTION_TYPE IN (
                'Vendor Billing, No CW Billing',
                'Vendor Billing, Insufficient CW Billing',
                'API Usage Recorded, No CW Billing',
                'Vendor SKU, No CW SKU')
              THEN ABS(COALESCE(AMOUNT_DELTA, 0)) ELSE 0 END), 2)                     AS TOTAL_LEAKAGE_AMOUNT
FROM THIRD_PARTY_RECON_OUTPUT_PROD
GROUP BY VENDOR, BILLING_MONTH
ORDER BY VENDOR, BILLING_MONTH;
"""
    run_sql(conn, summary_sql, "THIRD_PARTY_RECON_SUMMARY")

    # ── Quick report ──────────────────────────────────────────────────────
    cur = conn.cursor()
    cur.execute("""
        SELECT VENDOR,
               TO_CHAR(BILLING_MONTH, 'YYYY-MM')            AS MONTH,
               TOTAL_ROWS,
               PERFECT_MATCH_ROWS                            AS CLEAR_ROWS,
               CLEAR_PCT,
               VENDOR_NO_CW_ROWS,
               CW_NO_VENDOR_ROWS,
               VENDOR_INSUFF_CW_ROWS,
               UNMAPPED_PARTNER_ROWS,
               OTHER_ISSUE_ROWS
        FROM THIRD_PARTY_RECON_SUMMARY
        ORDER BY VENDOR, BILLING_MONTH
    """)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close()

    W = 140
    print(f"\n{'=' * W}")
    print("  THIRD_PARTY_RECON_SUMMARY — canonical flag distribution")
    print(f"{'=' * W}")
    widths = [max(len(c), max((len(str(r[i] or '')) for r in rows), default=0))
              for i, c in enumerate(cols)]
    print("  " + " | ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  " + "-+-".join("-" * w for w in widths))
    for row in rows:
        print("  " + " | ".join(str(v or '').ljust(w) for v, w in zip(row, widths)))
    print(f"\n  {len(rows)} vendor-month rows")

    cur2 = conn.cursor()
    cur2.execute("SELECT COUNT(*), COUNT(DISTINCT VENDOR) FROM THIRD_PARTY_RECON_OUTPUT_PROD")
    total, vendors = cur2.fetchone()
    cur2.close()
    print(f"\n  THIRD_PARTY_RECON_OUTPUT_PROD: {total:,} rows across {vendors} vendors")

    # Exception type distribution
    cur3 = conn.cursor()
    cur3.execute("""
        SELECT EXCEPTION_TYPE, COUNT(*) AS row_count,
               ROUND(SUM(ABS(COALESCE(AMOUNT_DELTA,0))),0) AS dollar_impact
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
        GROUP BY 1 ORDER BY 2 DESC
    """)
    exc_rows = cur3.fetchall()
    cur3.close()
    print(f"\n  Exception type distribution:")
    for et, n, d in exc_rows:
        print(f"    {str(et or 'None'):<50}  {n:>7,} rows   ${d or 0:>12,.0f}")

    print("\n  Done.")

finally:
    conn.close()
