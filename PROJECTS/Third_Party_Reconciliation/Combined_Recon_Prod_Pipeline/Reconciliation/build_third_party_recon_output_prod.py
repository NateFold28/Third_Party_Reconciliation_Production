"""
Build THIRD_PARTY_RECON_OUTPUT_PROD and THIRD_PARTY_RECON_SUMMARY_PROD.

Reads from THIRD_PARTY_RECON_DETAIL_PROD (all vendors, canonical OUTCOME_FLAG)
and produces:

  THIRD_PARTY_RECON_OUTPUT_PROD   - full detail + EXCEPTION_TYPE + EST_DOLLAR_IMPACT
  THIRD_PARTY_RECON_SUMMARY_PROD  - per-vendor-month KPI rollup for the app

Canonical EXCEPTION_TYPE taxonomy (mutually exclusive buckets, priority order):
  1.  Unmapped Partner                     — no valid SF_ID
  2.  Clear                                — CW amount >= vendor amount (always clear regardless of bundle flag)
  2b. Clear (partner-month rollup)         — CW/vendor totals reconcile at partner-month grain
    3.  Known Discount / Bundle              — HAS_DISCOUNT=TRUE AND amounts DON'T already reconcile
                                             (Amit-defined Clear Internal: variance is intentional bundle/discount)
    4.  Marketplace Billing Delay            — prior-period timing artifact
    5.  API Usage, Insufficient CW Billing   — API qty > 0 and CW billing is missing or materially short
    6.  Vendor SKU, No CW SKU                — vendor product has no CW rebill SKU
    7.  CW SKU, No Vendor SKU                — CW subscription has no vendor counterpart
    8.  Vendor Billing, No CW Billing        — vendor_amount > 0, cw_amount = 0
    9.  CW Billing, No Vendor Billing        — cw_amount > 0, vendor_amount = 0
    10. Vendor Billing, Insufficient CW Billing  — vendor > CW by >25%, both have billing
    11. Clear (minor drift)                  — vendor > CW by 0-25%, both > 0 (Proofpoint tolerance band)
    12. Clear (both-zero)                    — both sides $0 (audit-trail rows with no exposure)
    13. Other Issue                          — catch-all (should be empty after 11/12)

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
    -- KeepIT restored ingestion preserves Feb-Jun takeout promo invoices as
    -- aggregate vendor rows. They intentionally do not have account-level SF_IDs,
    -- so classify them on matched billing dollars before the generic unmapped
    -- partner rule.
    WHEN VENDOR = 'KeepIT'
         AND LOWER(COALESCE(VENDOR_PARTNER_NAME, '')) LIKE '%connectwise%continuum%consolidat%'
         AND COALESCE(VENDOR_AMOUNT, 0) > 0
         AND COALESCE(TOTAL_BILLING_AMOUNT, 0) >= COALESCE(VENDOR_AMOUNT, 0)
    THEN 'Clear'
    WHEN VENDOR = 'KeepIT'
         AND LOWER(COALESCE(VENDOR_PARTNER_NAME, '')) LIKE '%connectwise%continuum%consolidat%'
         AND COALESCE(VENDOR_AMOUNT, 0) > 0
         AND COALESCE(TOTAL_BILLING_AMOUNT, 0) > 0
    THEN 'Vendor Billing, Insufficient CW Billing'

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

    -- Disabled vendor-source rows should be tracked in a dedicated bucket.
    WHEN OUTCOME_FLAG IN ('DISABLED_PARTNER_SKU', 'Disabled Partner SKU')
    THEN 'Disabled Partner SKU'

    -- ESET is quantity-first: vendor source files carry seats, while dollars
    -- come from a contract-cost overlay. Preserve ESET's quantity outcome at
    -- the app boundary instead of letting amount-first rules relabel rows.
    WHEN VENDOR = 'ESET' AND OUTCOME_FLAG = 'Clear'
    THEN 'Clear'
    WHEN VENDOR = 'ESET' AND OUTCOME_FLAG = 'Vendor Billing, No CW Billing'
    THEN 'Vendor Billing, No CW Billing'
    WHEN VENDOR = 'ESET' AND OUTCOME_FLAG = 'Vendor Billing, Insufficient CW Billing'
    THEN 'Vendor Billing, Insufficient CW Billing'
    WHEN VENDOR = 'ESET' AND OUTCOME_FLAG = 'CW Billing, No Vendor Billing'
    THEN 'CW Billing, No Vendor Billing'

    -- Duplicate-billing is intentionally not a primary exception bucket.
    -- Keep DUPLICATE_BILLING_FLAG for side-signal visibility only.
    -- WHEN (COALESCE(ZUORA_AMOUNT, 0) > 0 AND COALESCE(MARKETPLACE_AMOUNT, 0) > 0)
    --      OR (COALESCE(DUPLICATE_BILLING_FLAG, 'FALSE') = 'TRUE'
    --          OR OUTCOME_FLAG IN ('Duplicated CW Invoice', 'Duplicate Billing', 'DUPLICATE_BILLING'))
    -- THEN 'Duplicated CW Invoice'

    -- SentinelOne add-ons are invoice-backed catalog gaps. Their usage rows can
    -- carry zero vendor amount because the cost rate is invoice-derived, and
    -- partner-month rollups can otherwise clear them against unrelated SKUs.
    WHEN VENDOR = 'SentinelOne'
         AND COALESCE(VENDOR_QUANTITY, 0) > 0
         AND COALESCE(TOTAL_BILLING_QUANTITY, 0) = 0
         AND (
             UPPER(COALESCE(SKU_MATCH_GROUP, '')) IN (
                 'S1_PURPLE_AI', 'PURPLE_AI',
                 'S1_RANGER_INS', 'S1_RANGER_INSIGHTS', 'RANGER_INSIGHTS',
                 'S1_RANGER_AD', 'RANGER_AD',
                 'WATCHTOWER'
             )
             OR UPPER(COALESCE(VENDOR_PRODUCT, '')) IN (
                 'PURPLE AI', 'RANGER INSIGHTS', 'RANGER AD', 'WATCHTOWER'
             )
         )
    THEN 'Vendor SKU, No CW SKU'

    -- ── 2. Clear ────────────────────────────────────────────────────────────
    -- CW amount >= vendor amount is always "Clear". Both sides must have
    -- real billing; zero-side cases are handled by vendor-only / CW-only
    -- buckets further down.
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

    -- Duplicate-billing category intentionally disabled to avoid masking more
    -- actionable reconciliation gaps. Signal remains available in
    -- DUPLICATE_BILLING_FLAG and DUPLICATE_BILLING columns.
    -- WHEN (COALESCE(DUPLICATE_BILLING_FLAG, 'FALSE') = 'TRUE'
    --       OR OUTCOME_FLAG IN ('Duplicated CW Invoice', 'Duplicate Billing', 'DUPLICATE_BILLING'))
    --      AND COALESCE(ZUORA_AMOUNT, 0) > 0
    --      AND COALESCE(MARKETPLACE_AMOUNT, 0) > 0
    -- THEN 'Duplicated CW Invoice'

    -- ── 5. Marketplace Billing Delay ──────────────────────────────────────
    -- Prior-period Marketplace invoice timing artifact; will self-resolve.
    WHEN OUTCOME_FLAG IN (
        'Marketplace Billing Delay', 'MARKETPLACE_TIMING', 'BILLING_TIMING_ADJACENT_MONTH'
    )
    THEN 'Marketplace Billing Delay'

    -- ── 6. API Usage, Insufficient CW Billing ─────────────────────────────
    -- TRT / API confirms active usage and CW billing is either missing or
    -- materially short at this row's account/product grain. This intentionally
    -- captures both true no-bill rows and insufficient-bill rows where the API
    -- proves usage exists.
    --
    -- NOTE: API_QUANTITY is partner-month scoped for most vendors, but
    -- Proofpoint is product-scoped in _run_skeleton_pipeline.py. Keep this
    -- guard so classification remains robust across both feed shapes.
    -- To avoid false positives, ONLY fire this rule if THIS ROW's vendor
    -- activity matches the API activity (i.e., VENDOR_QUANTITY > 0 at row grain).
    -- If VENDOR_QUANTITY = 0 but API_QUANTITY > 0, the usage is NOT on this product,
    -- so it falls to Rule 9 (Vendor Billing / CW Billing mismatch).
    WHEN COALESCE(API_QUANTITY, 0) > 0
         AND (
             (COALESCE(VENDOR_QUANTITY, 0) > 0
              AND (
                  COALESCE(TOTAL_BILLING_QUANTITY, 0) <= 0
                  OR COALESCE(VENDOR_QUANTITY, 0) > COALESCE(TOTAL_BILLING_QUANTITY, 0) * 1.25
                  OR COALESCE(TOTAL_BILLING_AMOUNT, 0) <= 0
                  OR (COALESCE(VENDOR_AMOUNT, 0) > 0
                      AND COALESCE(VENDOR_AMOUNT, 0) > COALESCE(TOTAL_BILLING_AMOUNT, 0) * 1.25)
              ))
             OR OUTCOME_FLAG IN (
                 'API Usage, Insufficient CW Billing',
                 'API Usage Recorded, No CW Billing',
                 'Missing CW Billing - API Confirmed',
                 'TRT_VENDOR_USAGE_NOT_BILLED',
                 'STRUCTURAL_VENDOR_ONLY_TRT_CONFIRMED'
             )
         )
    THEN 'API Usage, Insufficient CW Billing'

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
    -- Guaranteed mutually exclusive with "API Usage, Insufficient CW Billing"
    -- because that rule fires first when API/TRT confirms the usage signal.
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
    WHEN 'Disabled Partner SKU'                         THEN 'No action - vendor source marks this partner SKU as disabled'
    WHEN 'Unmapped Partner'                             THEN 'Data team: update partner mapping'
    WHEN 'Duplicated CW Invoice'                        THEN 'Billing Ops: review duplicate overlap (informational flag)'
    WHEN 'Marketplace Billing Delay'                    THEN 'No action - prior-month invoice expected next cycle'
    WHEN 'Vendor SKU, No CW SKU'                        THEN 'Product / Catalog: add a CW rebill SKU for this vendor product'
    WHEN 'CW SKU, No Vendor SKU'                        THEN 'Ops: verify whether this CW rebill SKU should still be active'
    WHEN 'API Usage, Insufficient CW Billing'           THEN 'Finance: close billing gap for API-confirmed usage'
    WHEN 'Vendor Billing, No CW Billing'                THEN 'Finance / Sales: onboard billing - vendor charged CW with no CW rebill to partner'
    WHEN 'CW Billing, No Vendor Billing'                THEN 'Ops: verify vendor-side attribution or retire the stale CW subscription'
    WHEN 'Vendor Billing, Insufficient CW Billing'      THEN 'Finance / Sales: close billing gap - vendor materially ahead of CW'
    ELSE 'Review required'
END
""".strip()

FINANCE_QUEUE_BUCKETS_SQL = (
    "'Vendor Billing, No CW Billing', "
    "'Vendor Billing, Insufficient CW Billing', "
    "'API Usage, Insufficient CW Billing', "
    "'Vendor SKU, No CW SKU'"
)
OPS_QUEUE_BUCKETS_SQL = (
    "'CW Billing, No Vendor Billing', "
    "'CW SKU, No Vendor SKU', "
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
WITH partner_canonical AS (
    -- One canonical display name per SF_ID (2026-08-31 board-ready pass).
    --
    -- The vendor reconciliation scripts LISTAGG every alias variant they've
    -- ever seen for an account, so the app's Partner column ends up looking
    -- like "Oryx Align | Oryx Align Limited | VIRTUS DATA CENTRES (Oryx
    -- Align Ltd) | SDT Ltd". That collapses multiple real cases into
    -- indistinguishable rows and prevents the Recon Team Queue from
    -- grouping cleanly.
    --
    -- Selection rules (deterministic — same input always picks the same
    -- name). Real business names are usually the SHORTEST reasonable
    -- variant; qualified/legacy suffixes ("- Legacy X", "(Parent Co)")
    -- inflate length without adding clarity, and ALLCAPS codes like
    -- "TEMPLESA" are internal identifiers, not display names.
    --
    --   1. PARENT_COMPANY if the map has one set (~75 sf_ids today).
    --   2. Otherwise the "cleanest" partner_name for that SF_ID:
    --        a. skip names that literally contain a pipe (data-quality
    --           leak in the source map — e.g. "ROCK | IT Consultancy")
    --        b. skip ALLCAPS-only codes < 12 chars (TEMPLESA, TTALX)
    --        c. skip names containing "( )" qualifier clauses if a
    --           non-parenthesized alternative exists
    --        d. skip "- Legacy" / "- ThreatAdvice" trailing qualifiers
    --        e. tie-break: shortest length between 4 and 60 chars,
    --           then alphabetical
    --   3. sf_ids with > 20 mapped aliases are flagged
    --      IS_AGGREGATOR_ACCOUNT so the app can render
    --      "<name> (aggregator, N sub-partners)".
    SELECT
        sf_id,
        COALESCE(
            NULLIF(TRIM(MAX(parent_company)), ''),
            best_partner_name
        )                                                       AS canonical_partner_name,
        COUNT(DISTINCT partner_name) > 20                       AS is_aggregator_account,
        COUNT(DISTINCT partner_name)                            AS partner_alias_count
    FROM (
        SELECT
            m.sf_id,
            m.partner_name,
            m.parent_company,
            FIRST_VALUE(m.partner_name) OVER (
                PARTITION BY m.sf_id
                ORDER BY
                    -- 1. Names containing pipes go last (data-quality leak)
                    IFF(m.partner_name LIKE '% | %' OR m.partner_name LIKE '%|%', 1, 0) ASC,
                    -- 2. ALLCAPS short codes go last (internal identifiers)
                    IFF(m.partner_name = UPPER(m.partner_name)
                        AND LENGTH(m.partner_name) < 12, 1, 0) ASC,
                    -- 3. Parenthesized qualifier clauses go last
                    IFF(m.partner_name LIKE '%(%', 1, 0) ASC,
                    -- 4. Dash-suffixed legacy tags go last ("- Legacy X")
                    IFF(m.partner_name ILIKE '% - %', 1, 0) ASC,
                    -- 5. Prefer 4-60 char range (real business names)
                    IFF(LENGTH(m.partner_name) BETWEEN 4 AND 60, 0, 1) ASC,
                    -- 6. Prefer mixed case over all lower / all upper
                    IFF(m.partner_name = UPPER(m.partner_name)
                        OR m.partner_name = LOWER(m.partner_name), 1, 0) ASC,
                    -- 7. Shortest wins (Oryx Align beats Oryx Align Limited)
                    LENGTH(m.partner_name) ASC,
                    -- 8. Deterministic tie-break
                    m.partner_name ASC
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS best_partner_name
        FROM RECON_PARTNER_MAP m
        WHERE m.sf_id IS NOT NULL
          AND m.partner_name IS NOT NULL
          AND TRIM(m.partner_name) <> ''
    )
    GROUP BY sf_id, best_partner_name
),
filtered AS (
    -- Global internal / test partner exclusion (Proofpoint mechanism).
    -- Drop rows whose VENDOR_PARTNER_NAME matches the internal/test regex.
    -- Preserved in THIRD_PARTY_RECON_DETAIL_PROD if audit needed.
    -- Snowflake RLIKE requires implicit anchoring and does NOT support (?i);
    -- lowercase the input and use case-insensitive patterns.
    SELECT *
    FROM THIRD_PARTY_RECON_DETAIL_PROD
    WHERE NOT (
        RLIKE(LOWER(COALESCE(VENDOR_PARTNER_NAME, '')), '{_EXCLUSION_REGEX}')
        -- KeepIT February 2026 lacks a partner-level Promo summary; the vendor
        -- invoice PDF is real vendor usage at aggregate partner grain and must
        -- remain visible in app totals instead of being filtered as test data.
        AND NOT (
            VENDOR = 'KeepIT'
            AND LOWER(COALESCE(VENDOR_PARTNER_NAME, '')) LIKE '%connectwise%continuum%consolidat%'
            AND COALESCE(VENDOR_QUANTITY, 0) > 0
        )
    )
), resolved AS (
        SELECT
            d.* EXCLUDE (SF_ID),
            d.SF_ID                              AS SF_ID_ORIGINAL,
            COALESCE(
                CASE
                    WHEN r.old_sf_id IS NOT NULL
                     AND r.merge_effective_month IS NOT NULL
                     AND d.BILLING_MONTH < r.merge_effective_month
                        THEN d.SF_ID
                    WHEN r.old_sf_id IS NOT NULL
                        THEN r.canonical_sf_id
                END,
                d.SF_ID
            ) AS SF_ID
        FROM filtered d
        LEFT JOIN RECON_ACCOUNT_MERGE_RESOLVER r
            ON r.old_sf_id = d.SF_ID
), classified AS (
    SELECT
        *,
        {CANONICAL_EXCEPTION_TYPE}                         AS EXCEPTION_TYPE,
        ABS(COALESCE(AMOUNT_DELTA, 0))                     AS EST_DOLLAR_IMPACT,
        1::NUMBER                                          AS VENDOR_SOURCE_ROW_COUNT,
        -- Point-in-time vs. cycle-average API dollar comparison
        -- (2026-08-28). VENDOR_UNIT_PRICE is the vendor-invoiced $/seat.
        -- API_AMOUNT     = API_QUANTITY     × VENDOR_UNIT_PRICE, i.e. what
        --                  the vendor invoice WOULD be if the vendor priced
        --                  on the point-in-time seat snapshot (day 20 for
        --                  Proofpoint, 21 for S1/BD, etc.).
        -- AVG_API_AMOUNT = AVG_API_QUANTITY × VENDOR_UNIT_PRICE, i.e. what
        --                  the vendor invoice WOULD be if the vendor priced
        --                  on the cycle-average seat count instead.
        -- Compare either to ZUORA_AMOUNT / VENDOR_AMOUNT to quantify the
        -- pricing-methodology impact per row and per SKU.
        (COALESCE(API_QUANTITY, 0)     * COALESCE(VENDOR_UNIT_PRICE, 0))::FLOAT
            AS API_AMOUNT,
        (COALESCE(AVG_API_QUANTITY, 0) * COALESCE(VENDOR_UNIT_PRICE, 0))::FLOAT
            AS AVG_API_AMOUNT,
        (
            COALESCE(AVG_API_QUANTITY, 0) * COALESCE(VENDOR_UNIT_PRICE, 0)
          - COALESCE(API_QUANTITY, 0)     * COALESCE(VENDOR_UNIT_PRICE, 0)
        )::FLOAT                                            AS API_AVG_MINUS_POINT_AMOUNT
    FROM resolved
)
-- App-facing precomputed columns (2026-08-21 latency pass): these move the
-- per-row classification / label / group-id work out of the Streamlit
-- app and into Snowflake so tab / filter changes stay O(1) in Python.
--
-- 2026-08-31 board-ready pass: derive canonical PRODUCT_DISPLAY and
-- PARTNER_DISPLAY_NAME here so the app never has to render pipe-delimited
-- LISTAGG blobs like "S1ES-CTL-EN-T2-SA | S1ES-CTL-EN-T9-SA" or
-- "Oryx Align | Oryx Align Ltd | VIRTUS DATA CENTRES (Oryx Align Ltd)".
-- The raw fields are preserved for audit; app UI reads DISPLAY columns.
SELECT
    c.VENDOR,
    c.BILLING_MONTH,
    c.INV_ID,
    c.SF_ID,
    c.* EXCLUDE (VENDOR, BILLING_MONTH, INV_ID, SF_ID),
    -- ---------------- PRODUCT_DISPLAY ---------------------------------
    -- Prefer the upstream sku_match_group when the vendor script populated
    -- it (SentinelOne, Auvik, ESET, Exium, Webroot). Fall back to text
    -- inference for vendors without a group column (Bitdefender,
    -- Proofpoint, Acronis, KeepIT). Final fallback strips pipes to the
    -- first token so nothing ever displays as a pipe-list.
    CASE
        -- Bucket 1 — trust vendor-script sku_match_group when meaningful
        WHEN c.SKU_MATCH_GROUP IS NOT NULL
         AND c.SKU_MATCH_GROUP NOT IN ('', 'UNMAPPED_VENDOR_PRODUCT', 'UNMAPPED')
            THEN CASE c.SKU_MATCH_GROUP
                    WHEN 'AUVIK_ESSENTIALS'   THEN 'Auvik Essentials'
                    WHEN 'AUVIK_PERFORMANCE'  THEN 'Auvik Performance'
                    WHEN 'AUVIK_ASM'          THEN 'Auvik SaaS Management'
                    WHEN 'COMPLETE'           THEN 'Complete'
                    WHEN 'CONTROL'            THEN 'Control'
                    WHEN 'CORE'               THEN 'Core'
                    WHEN 'RANGER'             THEN 'Ranger'
                    WHEN 'RANGER_INSIGHTS'    THEN 'Ranger Insights'
                    WHEN 'RANGER_AD'          THEN 'Ranger AD'
                    WHEN 'PURPLE_AI'          THEN 'Purple AI'
                    WHEN 'GSM'                THEN 'Webroot GSM'
                    WHEN 'DNS'                THEN 'Webroot DNS'
                    WHEN 'SAT'                THEN 'Webroot SAT'
                    WHEN 'GRAVITYZONE'        THEN 'Bitdefender GravityZone'
                    WHEN 'ENCRYPTION'         THEN 'Bitdefender Cloud Encryption'
                    WHEN 'PATCH_MGMT'         THEN 'Bitdefender Patch Management'
                    WHEN 'ATS_EDR'            THEN 'Bitdefender ATS & EDR'
                    WHEN 'EMAIL_SECURITY'     THEN 'Bitdefender Email Security'
                    WHEN 'MOBILE'             THEN 'Bitdefender Mobile Security'
                    WHEN 'MSP_SECURE'         THEN 'Bitdefender MSP Secure'
                    ELSE INITCAP(REPLACE(c.SKU_MATCH_GROUP, '_', ' '))
                 END
        -- Bucket 2 — Bitdefender text inference (VENDOR_PRODUCT is a
        -- pipe-list of Royalties product descriptions).
        WHEN c.VENDOR = 'Bitdefender' AND UPPER(COALESCE(c.VENDOR_PRODUCT, '')) LIKE '%EMAIL SECURITY%'
            THEN 'Bitdefender Email Security'
        WHEN c.VENDOR = 'Bitdefender' AND UPPER(COALESCE(c.VENDOR_PRODUCT, '')) LIKE '%SECURITY FOR MOBILE%'
            THEN 'Bitdefender Mobile Security'
        WHEN c.VENDOR = 'Bitdefender' AND UPPER(COALESCE(c.VENDOR_PRODUCT, '')) LIKE '%PATCH MANAGEMENT%'
            THEN 'Bitdefender Patch Management'
        WHEN c.VENDOR = 'Bitdefender' AND UPPER(COALESCE(c.VENDOR_PRODUCT, '')) LIKE '%EDR (MSP SECURE)%'
            THEN 'Bitdefender MSP Secure'
        WHEN c.VENDOR = 'Bitdefender' AND UPPER(COALESCE(c.VENDOR_PRODUCT, '')) LIKE '%CLOUD ENCRYPTION%'
            THEN 'Bitdefender Cloud Encryption'
        WHEN c.VENDOR = 'Bitdefender' AND UPPER(COALESCE(c.VENDOR_PRODUCT, '')) LIKE '%ATS & EDR%'
            THEN 'Bitdefender ATS & EDR'
        WHEN c.VENDOR = 'Bitdefender' AND (
             UPPER(COALESCE(c.VENDOR_PRODUCT, '')) LIKE '%ADVANCED THREAT SECURITY%'
          OR UPPER(COALESCE(c.VENDOR_PRODUCT, '')) LIKE '%GRAVITYZONE%'
          OR UPPER(COALESCE(c.VENDOR_PRODUCT, '')) LIKE '%GRAVITY ZONE%'
          OR UPPER(COALESCE(c.VENDOR_PRODUCT, '')) LIKE '%CLOUD SEC%')
            THEN 'Bitdefender GravityZone'
        -- Bucket 3 — Proofpoint text inference for the 12 pipe-rows
        WHEN c.VENDOR = 'Proofpoint' AND c.VENDOR_PRODUCT LIKE '% | %'
            THEN SPLIT_PART(c.VENDOR_PRODUCT, ' | ', 1)
        -- Bucket 4 — anything else with a pipe: strip to first token
        WHEN c.VENDOR_PRODUCT LIKE '% | %'
            THEN SPLIT_PART(c.VENDOR_PRODUCT, ' | ', 1)
        ELSE COALESCE(NULLIF(TRIM(c.VENDOR_PRODUCT), ''), '(unmapped)')
    END                                                                              AS PRODUCT_DISPLAY,
    -- ---------------- PARTNER_DISPLAY_NAME ----------------------------
    -- If the map has a single canonical name for this SF_ID, prefer it.
    -- Otherwise (unmapped, or the vendor's own name has no pipes and is
    -- likely correct), fall back to the vendor-emitted first token.
    COALESCE(
        pc.canonical_partner_name,
        SPLIT_PART(c.VENDOR_PARTNER_NAME, ' | ', 1),
        c.VENDOR_PARTNER_NAME
    )                                                                                AS PARTNER_DISPLAY_NAME,
    -- Downstream can badge aggregator accounts ("+ N sub-partners") so
    -- the operator knows the row is a shared distributor/house account.
    COALESCE(pc.is_aggregator_account, FALSE)                                        AS IS_AGGREGATOR_ACCOUNT,
    COALESCE(pc.partner_alias_count, 0)                                              AS PARTNER_ALIAS_COUNT,
    IFF(COALESCE(c.DUPLICATE_BILLING_FLAG, 'FALSE') = 'TRUE', 'Y', 'N')            AS DUPLICATE_BILLING,
    {ACTION_NEEDED_CASE}                                                                AS ACTION_NEEDED,
    CASE WHEN c.EXCEPTION_TYPE IN ({FINANCE_QUEUE_BUCKETS_SQL}) THEN TRUE ELSE FALSE END  AS IS_LEAKAGE,
    CASE WHEN c.EXCEPTION_TYPE IN ({FINANCE_QUEUE_BUCKETS_SQL}) THEN TRUE ELSE FALSE END  AS IS_FINANCE_QUEUE,
    CASE WHEN c.EXCEPTION_TYPE IN ({OPS_QUEUE_BUCKETS_SQL})     THEN TRUE ELSE FALSE END  AS IS_OPS_QUEUE,
    CASE WHEN c.EXCEPTION_TYPE = 'Marketplace Billing Delay'    THEN TRUE ELSE FALSE END  AS IS_TIMING_QUEUE,
    CASE WHEN c.EXCEPTION_TYPE = 'Clear'                        THEN TRUE ELSE FALSE END  AS IS_CLEAR,
    -- Stable Case ID matches the app's Recon Team Queue key so team edits
    -- persist across filter changes without an app-side apply() loop.
    -- Uses PRODUCT_DISPLAY (canonical family) so raw-SKU pipe variants no
    -- longer split one real case into multiple queue rows.
    CONCAT_WS(
        '|',
        COALESCE(c.VENDOR, ''),
        COALESCE(c.SF_ID, ''),
        COALESCE(
            CASE
                WHEN c.SKU_MATCH_GROUP IS NOT NULL
                 AND c.SKU_MATCH_GROUP NOT IN ('', 'UNMAPPED_VENDOR_PRODUCT', 'UNMAPPED')
                    THEN c.SKU_MATCH_GROUP
                WHEN c.VENDOR_PRODUCT LIKE '% | %'
                    THEN SPLIT_PART(c.VENDOR_PRODUCT, ' | ', 1)
                ELSE c.VENDOR_PRODUCT
            END,
            ''
        ),
        TO_CHAR(c.BILLING_MONTH, 'YYYY-MM'),
        COALESCE(c.EXCEPTION_TYPE, '')
    )                                                                                    AS CASE_ID
FROM classified c
LEFT JOIN partner_canonical pc
    ON pc.sf_id = c.SF_ID;
"""
    run_sql(conn, output_sql, "THIRD_PARTY_RECON_OUTPUT_PROD")

    # ── Build THIRD_PARTY_RECON_SUMMARY_PROD ────────────────────────────────
    # App reads this table for the per-vendor-month KPI tiles.
    # PERFECT_MATCH_ROWS = rows classified as 'Clear'.
    #
    # DATA_LOAD_STATUS (added 2026-08-21):
    #   LOADED       — usage row count for this (vendor, month) is at least 30% of
    #                  that vendor's median row count across all months present.
    #   PARTIAL      — usage rows exist but are < 30% of the vendor median (i.e.,
    #                  the month has only a small fraction of expected data).
    #   NOT_LOADED   — vendor usage has zero rows for this (vendor, month), which
    #                  usually means the source files were not ingested yet.
    # The app should render "No Data Loaded" / "Partial Data" tiles for PARTIAL /
    # NOT_LOADED months instead of reporting a poor reconciliation rate.
    #
    # Grid: FULL OUTER JOIN OUTPUT_PROD aggregates with USAGE_PROD aggregates so
    # months where ingestion happened but OUTPUT_PROD dropped everything (or
    # vice versa) still surface a row.
    summary_sql = f"""{USE}
CREATE OR REPLACE TABLE THIRD_PARTY_RECON_SUMMARY_PROD AS
WITH output_agg AS (
    SELECT
        VENDOR,
        BILLING_MONTH,
        COUNT(*)                                                                    AS TOTAL_ROWS,
        COUNT_IF(EXCEPTION_TYPE = 'Clear')                                          AS PERFECT_MATCH_ROWS,
        SUM(COALESCE(VENDOR_QUANTITY, 0))                                           AS TOTAL_VENDOR_SEATS,
        SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0))                                    AS TOTAL_BILLING_SEATS,
        ROUND(SUM(COALESCE(VENDOR_AMOUNT, 0)), 2)                                   AS TOTAL_VENDOR_AMOUNT,
        ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 2)                            AS TOTAL_BILLING_AMOUNT,
        COUNT_IF(EXCEPTION_TYPE = 'Unmapped Partner')                               AS UNMAPPED_PARTNER_ROWS,
        COUNT_IF(COALESCE(DUPLICATE_BILLING_FLAG, 'FALSE') = 'TRUE')               AS DUPLICATE_INVOICE_ROWS,
        COUNT_IF(EXCEPTION_TYPE = 'Known Discount / Bundle')                        AS KNOWN_DISCOUNT_ROWS,
        COUNT_IF(EXCEPTION_TYPE = 'Marketplace Billing Delay')                      AS TIMING_ROWS,
        COUNT_IF(EXCEPTION_TYPE = 'API Usage, Insufficient CW Billing')             AS API_NO_CW_ROWS,
        COUNT_IF(EXCEPTION_TYPE = 'Vendor SKU, No CW SKU')                          AS VENDOR_SKU_NO_CW_ROWS,
        COUNT_IF(EXCEPTION_TYPE = 'CW SKU, No Vendor SKU')                          AS CW_SKU_NO_VENDOR_ROWS,
        COUNT_IF(EXCEPTION_TYPE = 'Vendor Billing, No CW Billing')                  AS VENDOR_NO_CW_ROWS,
        COUNT_IF(EXCEPTION_TYPE = 'CW Billing, No Vendor Billing')                  AS CW_NO_VENDOR_ROWS,
        COUNT_IF(EXCEPTION_TYPE = 'Vendor Billing, Insufficient CW Billing')        AS VENDOR_INSUFF_CW_ROWS,
        COUNT_IF(EXCEPTION_TYPE = 'Other Issue')                                    AS OTHER_ISSUE_ROWS,
        ROUND(SUM(CASE WHEN EXCEPTION_TYPE IN (
                    'Vendor Billing, No CW Billing',
                    'Vendor Billing, Insufficient CW Billing',
                    'API Usage, Insufficient CW Billing',
                    'Vendor SKU, No CW SKU')
                  THEN ABS(COALESCE(AMOUNT_DELTA, 0)) ELSE 0 END), 2)               AS TOTAL_LEAKAGE_AMOUNT
    FROM THIRD_PARTY_RECON_OUTPUT_PROD
    GROUP BY VENDOR, BILLING_MONTH
),
usage_agg AS (
    SELECT VENDOR, BILLING_MONTH::DATE AS BILLING_MONTH, COUNT(*) AS USAGE_ROW_COUNT
    FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
    GROUP BY VENDOR, BILLING_MONTH
),
vendor_medians AS (
    SELECT VENDOR, MEDIAN(USAGE_ROW_COUNT) AS MEDIAN_USAGE_ROWS
    FROM usage_agg
    WHERE USAGE_ROW_COUNT > 0
    GROUP BY VENDOR
),
grid AS (
    SELECT VENDOR, BILLING_MONTH FROM output_agg
    UNION
    SELECT VENDOR, BILLING_MONTH FROM usage_agg
)
SELECT
    g.VENDOR,
    g.BILLING_MONTH,
    COALESCE(o.TOTAL_ROWS, 0)                                                       AS TOTAL_ROWS,
    COALESCE(o.PERFECT_MATCH_ROWS, 0)                                               AS PERFECT_MATCH_ROWS,
    COALESCE(o.TOTAL_VENDOR_SEATS, 0)                                               AS TOTAL_VENDOR_SEATS,
    COALESCE(o.TOTAL_BILLING_SEATS, 0)                                              AS TOTAL_BILLING_SEATS,
    COALESCE(o.TOTAL_VENDOR_AMOUNT, 0)                                              AS TOTAL_VENDOR_AMOUNT,
    COALESCE(o.TOTAL_BILLING_AMOUNT, 0)                                             AS TOTAL_BILLING_AMOUNT,
    ROUND(COALESCE(o.PERFECT_MATCH_ROWS, 0) * 100.0 / NULLIF(o.TOTAL_ROWS, 0), 1)  AS CLEAR_PCT,
    COALESCE(o.UNMAPPED_PARTNER_ROWS, 0)                                            AS UNMAPPED_PARTNER_ROWS,
    COALESCE(o.DUPLICATE_INVOICE_ROWS, 0)                                           AS DUPLICATE_INVOICE_ROWS,
    COALESCE(o.KNOWN_DISCOUNT_ROWS, 0)                                              AS KNOWN_DISCOUNT_ROWS,
    COALESCE(o.TIMING_ROWS, 0)                                                      AS TIMING_ROWS,
    COALESCE(o.API_NO_CW_ROWS, 0)                                                   AS API_NO_CW_ROWS,
    COALESCE(o.VENDOR_SKU_NO_CW_ROWS, 0)                                            AS VENDOR_SKU_NO_CW_ROWS,
    COALESCE(o.CW_SKU_NO_VENDOR_ROWS, 0)                                            AS CW_SKU_NO_VENDOR_ROWS,
    COALESCE(o.VENDOR_NO_CW_ROWS, 0)                                                AS VENDOR_NO_CW_ROWS,
    COALESCE(o.CW_NO_VENDOR_ROWS, 0)                                                AS CW_NO_VENDOR_ROWS,
    COALESCE(o.VENDOR_INSUFF_CW_ROWS, 0)                                            AS VENDOR_INSUFF_CW_ROWS,
    COALESCE(o.OTHER_ISSUE_ROWS, 0)                                                 AS OTHER_ISSUE_ROWS,
    COALESCE(o.TOTAL_LEAKAGE_AMOUNT, 0)                                             AS TOTAL_LEAKAGE_AMOUNT,
    COALESCE(u.USAGE_ROW_COUNT, 0)                                                  AS USAGE_ROW_COUNT,
    vm.MEDIAN_USAGE_ROWS::INT                                                       AS VENDOR_MEDIAN_USAGE_ROWS,
    CASE
        WHEN COALESCE(u.USAGE_ROW_COUNT, 0) = 0 THEN 'NOT_LOADED'
        WHEN vm.MEDIAN_USAGE_ROWS IS NULL     THEN 'LOADED'
        WHEN u.USAGE_ROW_COUNT < vm.MEDIAN_USAGE_ROWS * 0.3 THEN 'PARTIAL'
        ELSE 'LOADED'
    END                                                                              AS DATA_LOAD_STATUS
FROM grid g
LEFT JOIN output_agg   o  ON o.VENDOR = g.VENDOR AND o.BILLING_MONTH = g.BILLING_MONTH
LEFT JOIN usage_agg    u  ON u.VENDOR = g.VENDOR AND u.BILLING_MONTH = g.BILLING_MONTH
LEFT JOIN vendor_medians vm ON vm.VENDOR = g.VENDOR
ORDER BY g.VENDOR, g.BILLING_MONTH;
"""
    run_sql(conn, summary_sql, "THIRD_PARTY_RECON_SUMMARY_PROD")

    # ── Quick report ──────────────────────────────────────────────────────
    cur = conn.cursor()
    cur.execute("""
        SELECT VENDOR,
               TO_CHAR(BILLING_MONTH, 'YYYY-MM')            AS MONTH,
               DATA_LOAD_STATUS,
               USAGE_ROW_COUNT,
               TOTAL_ROWS,
               PERFECT_MATCH_ROWS                            AS CLEAR_ROWS,
               CLEAR_PCT,
               VENDOR_NO_CW_ROWS,
               CW_NO_VENDOR_ROWS,
               VENDOR_INSUFF_CW_ROWS,
               UNMAPPED_PARTNER_ROWS,
               OTHER_ISSUE_ROWS
        FROM THIRD_PARTY_RECON_SUMMARY_PROD
        ORDER BY VENDOR, BILLING_MONTH
    """)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close()

    W = 140
    print(f"\n{'=' * W}")
    print("  THIRD_PARTY_RECON_SUMMARY_PROD - canonical flag distribution")
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
