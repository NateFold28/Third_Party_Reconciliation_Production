"""Build THIRD_PARTY_RECON_OUTPUT_PROD and THIRD_PARTY_RECON_SUMMARY_PROD.

Outcome centralization is strict and single-sourced: OUTCOME_FLAG and
EXCEPTION_TYPE are both produced from canonical_outcomes.strict_outcome_case().

Strict monetary rules:
    - Clear iff vendor_amount > 0, cw_amount > 0, and cw_amount >= vendor_amount.
        - Rows with vendor_amount = 0 and cw_amount = 0 are retained in shared detail
            for audit only and excluded from published output and KPI denominators.
    - API Usage, Insufficient CW Billing iff point-in-time API > 0, vendor_amount > 0,
        cw_amount >= 0, and cw_amount < vendor_amount.
    - Vendor Billing, No CW Billing iff no API signal, vendor_amount > 0,
        and cw_amount = 0.
    - Vendor Billing, Insufficient CW Billing iff no API signal, both amounts > 0,
        and cw_amount < vendor_amount.

Marketplace-delay and mapping evidence remain structural classifications.
Duplicate billing is the only side flag; primary duplicate classification is disabled.
"""
from __future__ import annotations
import sys, time
from pathlib import Path

REPO = Path(r"C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline")
sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402
from canonical_outcomes import strict_outcome_case  # noqa: E402

USE = (
    "USE ROLE DEVELOPER; "
    "USE WAREHOUSE REPORTING_WH; "
    "USE DATABASE ANALYTICS_DEV; "
    "USE SCHEMA DBT_NFOLD_TRANSFORMATION;"
)

# ---------------------------------------------------------------------------
# Canonical classifier SQL: one source for OUTCOME_FLAG and EXCEPTION_TYPE.
# Marketplace-delay and mapping evidence remain precedence-ordered.
# Duplicate billing stays side-flag-only (primary duplicate rule is disabled).
# ---------------------------------------------------------------------------
CANONICAL_CLASSIFIER_CASE = strict_outcome_case(
    sf_id="SF_ID",
    vendor_amount="VENDOR_AMOUNT",
    cw_amount="TOTAL_BILLING_AMOUNT",
    api_quantity="API_QUANTITY",
    avg_api_quantity="AVG_API_QUANTITY",  # exploratory only; never classifies
)


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
    WHEN 'Unmapped Partner'                             THEN 'Data / Catalog: correct partner or SKU mapping'
    WHEN 'Marketplace Billing Delay'                    THEN 'No action - prior-month invoice expected next cycle'
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
    "'API Usage, Insufficient CW Billing'"
)
OPS_QUEUE_BUCKETS_SQL = (
    "'CW Billing, No Vendor Billing', "
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


def require_sql(conn, sql: str, label: str) -> None:
    """Run a publication step and terminate the build if it fails."""
    if not run_sql(conn, sql, label):
        raise RuntimeError(f"Required build step failed: {label}")


conn = get_snowflake_connection(
    role="DEVELOPER", warehouse="REPORTING_WH",
    database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
)
try:
    # ── Build THIRD_PARTY_RECON_OUTPUT_PROD ───────────────────────────────
    # SELECT * from the unified detail table and add:
    #   EXCEPTION_TYPE  — strict canonical classification
    #   EST_DOLLAR_IMPACT — ABS(amount_delta) precomputed for dashboard efficiency
    #   SF_ID_RESOLVED — surviving SFDC id after approved account rewrites
    # VENDOR_SOURCE_ROW_COUNT defaults to 1 so ghost-month logic works correctly
    # (all rows in THIRD_PARTY_RECON_DETAIL_PROD came from a real vendor file).
    #
    # SF_ID_RESOLVED is intentionally narrow. Only true merged-account rows and
    # governed manual overrides may rewrite SF_ID; parent-child relationships are
    # retained as context elsewhere and must not collapse child accounts to a
    # parent during reconciliation. SF_ID_ORIGINAL preserves the audit trail.
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
    --   1. Use the "cleanest" partner_name for that SF_ID. Parent company is
    --      retained separately as context and must not replace a child name:
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
        best_partner_name                                       AS canonical_partner_name,
        NULLIF(TRIM(MAX(cms_id)), '')                            AS cms_id,
        NULLIF(TRIM(MAX(parent_company)), '')                    AS parent_company,
        COUNT(DISTINCT partner_name) > 20                       AS is_aggregator_account,
        COUNT(DISTINCT partner_name)                            AS partner_alias_count
    FROM (
        SELECT
            m.sf_id,
            m.partner_name,
            m.cms_id,
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
), monthly_cms AS (
    -- Date-aware CMS identity. Some Salesforce accounts have historical CMS
    -- values, so prefer the most frequent governed value for that account and
    -- month instead of applying one static MAX across the full history.
    SELECT
        sf_id,
        billing_month,
        cms_id
    FROM (
        SELECT
            sf_id,
            billing_month,
            NULLIF(TRIM(cms_id), '') AS cms_id,
            COUNT(*) AS mapping_rows
        FROM RECON_PARTNER_MAP_MONTHLY
        WHERE sf_id IS NOT NULL
          AND NULLIF(TRIM(cms_id), '') IS NOT NULL
        GROUP BY 1, 2, 3
    )
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY sf_id, billing_month
        ORDER BY mapping_rows DESC, cms_id
    ) = 1
), billing_line_stats AS (
    -- Reconciliation remains at vendor/account/month/product-family grain,
    -- but expose how many underlying Zuora invoice lines were rolled into
    -- each account-month. Exact line detail is published separately below.
    SELECT
        vendor,
        sf_id,
        billing_month,
        COUNT(*) AS zuora_account_month_line_count,
        COUNT(DISTINCT invoice_number) AS zuora_account_month_invoice_count,
        COUNT(DISTINCT product_sku) AS zuora_account_month_sku_count
    FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
    WHERE sf_id IS NOT NULL
    GROUP BY 1, 2, 3
),
filtered AS (
    -- Exclude rows with no financial activity on either side. They remain in
    -- THIRD_PARTY_RECON_DETAIL_PROD for source/audit traceability, but they are
    -- not reconciliation cases and must not enter OUTPUT_PROD, SUMMARY_PROD,
    -- the app, or the Clear-rate denominator.
    --
    -- No regex partner exclusion is applied at this layer. Any non-zero row
    -- that is not Clear should remain visible for review in OUTPUT/SUMMARY.
    SELECT *
    FROM THIRD_PARTY_RECON_DETAIL_PROD
    WHERE NOT (
        COALESCE(VENDOR_AMOUNT, 0) = 0
        AND COALESCE(TOTAL_BILLING_AMOUNT, 0) = 0
    )
), resolved AS (
        SELECT
            d.* EXCLUDE (SF_ID),
            d.SF_ID                              AS SF_ID_ORIGINAL,
            d.SF_ID AS SF_ID
        FROM filtered d
), classified_base AS (
    SELECT
        * REPLACE (({CANONICAL_CLASSIFIER_CASE}) AS OUTCOME_FLAG)
    FROM resolved
), classified AS (
    SELECT
        *,
        OUTCOME_FLAG                                       AS EXCEPTION_TYPE,
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
    FROM classified_base
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
    COALESCE(mc.cms_id, pc.cms_id)                                                AS CMS_ID,
    pc.canonical_partner_name                                                     AS CW_PARTNER_NAME,
    pc.parent_company                                                             AS CW_PARENT_COMPANY,
    c.VENDOR_PRODUCT                                                              AS VENDOR_PRODUCT_SKU,
    COALESCE(
        NULLIF(TRIM(c.CW_SKUS), ''),
        NULLIF(TRIM(c.ZUORA_SKUS), ''),
        NULLIF(TRIM(c.MARKETPLACE_SKUS), '')
    )                                                                             AS CW_SKU,
    COALESCE(
        NULLIF(TRIM(c.ZUORA_SKUS), ''),
        NULLIF(TRIM(c.MARKETPLACE_SKUS), '')
    )                                                                             AS MATCHED_INVOICE_SKU,
    NULL::VARCHAR                                                                 AS SALESFORCE_ACCOUNT_ID,
    NULL::VARCHAR                                                                 AS SALESFORCE_ACCOUNT_URL,
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
                    WHEN 'AUVIK_ANM_NETWORK'  THEN 'Auvik Network Monitoring'
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
    pc.parent_company                                                               AS PARTNER_PARENT_COMPANY,
    COALESCE(bls.zuora_account_month_line_count, 0)                                AS ZUORA_ACCOUNT_MONTH_LINE_COUNT,
    COALESCE(bls.zuora_account_month_invoice_count, 0)                             AS ZUORA_ACCOUNT_MONTH_INVOICE_COUNT,
    COALESCE(bls.zuora_account_month_sku_count, 0)                                 AS ZUORA_ACCOUNT_MONTH_SKU_COUNT,
    IFF(COALESCE(c.DUPLICATE_BILLING_FLAG, 'FALSE') = 'TRUE', 'Y', 'N')            AS DUPLICATE_BILLING,
    {ACTION_NEEDED_CASE}                                                                AS ACTION_NEEDED,
    CASE WHEN c.EXCEPTION_TYPE IN ({FINANCE_QUEUE_BUCKETS_SQL}) THEN TRUE ELSE FALSE END  AS IS_LEAKAGE,
    CASE WHEN c.EXCEPTION_TYPE IN ({FINANCE_QUEUE_BUCKETS_SQL}) THEN TRUE ELSE FALSE END  AS IS_FINANCE_QUEUE,
    CASE WHEN c.EXCEPTION_TYPE IN ({OPS_QUEUE_BUCKETS_SQL})     THEN TRUE ELSE FALSE END  AS IS_OPS_QUEUE,
    CASE WHEN c.EXCEPTION_TYPE = 'Marketplace Billing Delay'    THEN TRUE ELSE FALSE END  AS IS_TIMING_QUEUE,
    CASE WHEN c.EXCEPTION_TYPE = 'Clear'                        THEN TRUE ELSE FALSE END  AS IS_CLEAR,
    -- Stable case identity is independent of classification. Preserve the
    -- native subgrain for vendors whose product case intentionally has more
    -- than one stream/family, and preserve vendor partner identity because
    -- governed aliases can currently resolve to the same Salesforce account.
    CONCAT_WS(
        '|',
        COALESCE(c.VENDOR, ''),
        COALESCE(c.SF_ID, '~UNMAPPED'),
        COALESCE(c.VENDOR_PARTNER_NAME, ''),
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
        COALESCE(c.RECON_SUBGRAIN, '')
    )                                                                                    AS CASE_ID
FROM classified c
LEFT JOIN partner_canonical pc
    ON pc.sf_id = c.SF_ID
LEFT JOIN monthly_cms mc
    ON mc.sf_id = c.SF_ID
   AND mc.billing_month = c.BILLING_MONTH
LEFT JOIN billing_line_stats bls
    ON bls.vendor = c.VENDOR
   AND bls.sf_id = c.SF_ID
   AND bls.billing_month = c.BILLING_MONTH;
"""
    require_sql(conn, output_sql, "THIRD_PARTY_RECON_OUTPUT_PROD")

    # RECON_PARTNER_MAP stores the ConnectWise account number (ACT-*), not
    # Salesforce's 15/18-character record ID. Resolve links after the atomic
    # table create so every output rebuild picks up current map/account changes
    # without adding any app-side query or join latency.
    salesforce_link_sql = f"""{USE}
UPDATE THIRD_PARTY_RECON_OUTPUT_PROD o
SET
    SALESFORCE_ACCOUNT_ID = sf.salesforce_account_id,
    SALESFORCE_ACCOUNT_URL =
        'https://connectwise20.lightning.force.com/lightning/r/Account/'
        || sf.salesforce_account_id || '/view?cws_id=' || o.SF_ID
FROM (
    SELECT
        UPPER(TRIM(CWS_ACCOUNT_UNIQUE_IDENTIFIER_C)) AS cws_account_id,
        ID AS salesforce_account_id
    FROM ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT
    WHERE NULLIF(TRIM(CWS_ACCOUNT_UNIQUE_IDENTIFIER_C), '') IS NOT NULL
      AND REGEXP_LIKE(ID, '^[A-Za-z0-9]{{15,18}}$')
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY UPPER(TRIM(CWS_ACCOUNT_UNIQUE_IDENTIFIER_C))
        ORDER BY IFF(MASTER_RECORD_ID IS NULL, 0, 1), LAST_MODIFIED_DATE DESC NULLS LAST, ID
    ) = 1
) sf
WHERE UPPER(TRIM(o.SF_ID)) = sf.cws_account_id;
"""
    require_sql(conn, salesforce_link_sql, "THIRD_PARTY_RECON_OUTPUT_PROD Salesforce links")

    # ── Build line-level ConnectWise billing drilldown ─────────────────────
    # The canonical reconciliation table intentionally remains at a stable
    # comparison grain. This companion preserves every Zuora invoice line and
    # links it to the matching case by raw billed SKU, avoiding quantity/amount
    # fanout when one vendor row is backed by several invoice lines.
    billing_line_sql = f"""{USE}
CREATE OR REPLACE TABLE THIRD_PARTY_RECON_BILLING_LINE_DETAIL_PROD AS
WITH source_lines AS (
    SELECT
        z.*,
        ROW_NUMBER() OVER (
            PARTITION BY
                z.vendor, z.sf_id, z.billing_month, z.invoice_id,
                z.product_sku, z.charge_name, z.subscription_name,
                z.service_start_date, z.service_end_date,
                z.qty, z.unit_price_usd, z.charge_amount_usd
            ORDER BY z.charge_date, z.invoice_item_sku
        ) AS duplicate_ordinal,
        SHA2(CONCAT_WS(
            '|', COALESCE(z.vendor, '∅'), COALESCE(z.sf_id, '∅'),
            COALESCE(z.invoice_id, '∅'), COALESCE(z.invoice_number, '∅'),
            COALESCE(z.product_sku, '∅'), COALESCE(z.charge_name, '∅'),
            COALESCE(z.subscription_name, '∅'),
            COALESCE(z.service_start_date::VARCHAR, '∅'),
            COALESCE(z.service_end_date::VARCHAR, '∅'),
            COALESCE(z.qty::VARCHAR, '∅'),
            COALESCE(z.unit_price_usd::VARCHAR, '∅'),
            COALESCE(z.charge_amount_usd::VARCHAR, '∅')
        ), 256) AS source_line_fingerprint
    FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD z
), output_candidates AS (
    SELECT
        s.*,
        o.CASE_ID,
        o.SKU_MATCH_GROUP,
        o.PRODUCT_DISPLAY,
        o.PARTNER_DISPLAY_NAME,
        o.EXCEPTION_TYPE,
        o.IS_CLEAR,
        IFF(
            CONTAINS(
                ',' || REPLACE(UPPER(COALESCE(o.ZUORA_SKUS, '')), ' ', '') || ',',
                ',' || REPLACE(UPPER(COALESCE(s.product_sku, '')), ' ', '') || ','
            ),
            1,
            0
        ) AS exact_output_sku_match,
        COUNT(o.CASE_ID) OVER (
            PARTITION BY
                s.vendor, s.sf_id, s.billing_month, s.invoice_id,
                s.product_sku, s.charge_name, s.subscription_name,
                s.service_start_date, s.service_end_date,
                s.qty, s.unit_price_usd, s.charge_amount_usd,
                s.duplicate_ordinal
        ) AS output_candidate_count
    FROM source_lines s
    LEFT JOIN THIRD_PARTY_RECON_OUTPUT_PROD o
        ON o.VENDOR = s.vendor
       AND o.SF_ID = s.sf_id
       AND o.BILLING_MONTH = s.billing_month
), selected AS (
    SELECT *
    FROM output_candidates
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY
            vendor, sf_id, billing_month, invoice_id,
            product_sku, charge_name, subscription_name,
            service_start_date, service_end_date,
            qty, unit_price_usd, charge_amount_usd,
            duplicate_ordinal
        ORDER BY exact_output_sku_match DESC, CASE_ID
    ) = 1
)
SELECT
    SHA2(CONCAT_WS(
        '|', source_line_fingerprint, billing_month::VARCHAR,
        duplicate_ordinal::VARCHAR
    ), 256) AS BILLING_LINE_ID,
    source_line_fingerprint AS SOURCE_LINE_FINGERPRINT,
    'ZUORA'::VARCHAR AS BILLING_SOURCE,
    vendor AS VENDOR,
    billing_month AS BILLING_MONTH,
    source_billing_month AS SOURCE_BILLING_MONTH,
    sf_id AS SF_ID,
    sf_id_source AS SF_ID_SOURCE,
    CASE
        WHEN exact_output_sku_match = 1 THEN CASE_ID
        ELSE NULL
    END AS CASE_ID,
    CASE
        WHEN exact_output_sku_match = 1 THEN 'EXACT_BILLED_SKU'
        WHEN output_candidate_count > 0 THEN 'UNRESOLVED_NO_EXACT_SKU'
        ELSE 'UNMATCHED_ACCOUNT_MONTH'
    END AS CASE_MATCH_METHOD,
    IFF(exact_output_sku_match = 1, SKU_MATCH_GROUP, NULL)
        AS SKU_MATCH_GROUP,
    IFF(exact_output_sku_match = 1, PRODUCT_DISPLAY, NULL)
        AS PRODUCT_DISPLAY,
    IFF(exact_output_sku_match = 1, PARTNER_DISPLAY_NAME, NULL)
        AS PARTNER_DISPLAY_NAME,
    IFF(exact_output_sku_match = 1, EXCEPTION_TYPE, NULL)
        AS EXCEPTION_TYPE,
    IFF(exact_output_sku_match = 1, IS_CLEAR, NULL)
        AS IS_CLEAR,
    invoice_number AS INVOICE_NUMBER,
    invoice_id AS INVOICE_ID,
    invoice_date AS INVOICE_DATE,
    duplicate_ordinal AS LINE_OCCURRENCE,
    product_sku AS PRODUCT_SKU,
    invoice_item_sku AS INVOICE_ITEM_SKU,
    product_name AS PRODUCT_NAME,
    charge_name AS CHARGE_NAME,
    subscription_name AS SUBSCRIPTION_NAME,
    qty AS QUANTITY,
    unit_price_usd AS UNIT_PRICE_USD,
    charge_amount_usd AS CHARGE_AMOUNT_USD,
    item_tax_amount_usd AS ITEM_TAX_AMOUNT_USD,
    charge_date AS CHARGE_DATE,
    service_start_date AS SERVICE_START_DATE,
    service_end_date AS SERVICE_END_DATE,
    billing_period_method AS BILLING_PERIOD_METHOD,
    account_currency AS ACCOUNT_CURRENCY,
    zuora_account_number AS ZUORA_ACCOUNT_NUMBER,
    zuora_account_name AS ZUORA_ACCOUNT_NAME,
    subscription_sold_to_account_name AS SUBSCRIPTION_SOLD_TO_ACCOUNT_NAME
FROM selected;
"""
    require_sql(conn, billing_line_sql, "THIRD_PARTY_RECON_BILLING_LINE_DETAIL_PROD")

    # ── Build THIRD_PARTY_RECON_SUMMARY_PROD ────────────────────────────────
    # App reads this table for the per-vendor-month KPI tiles.
    # PERFECT_MATCH_ROWS = rows classified as 'Clear'.
    #
    # DATA_LOAD_STATUS gate:
    #   LOADED       — usage row count for this (vendor, month) is > 0.
    #   NOT_LOADED   — usage row count is 0 for this (vendor, month).
    #
    # Rationale: months with no vendor usage should be excluded from vendor-health
    # reconciliation denominators. No median/partial threshold is applied.
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
    NULL::INT                                                                       AS VENDOR_MEDIAN_USAGE_ROWS,
    CASE
        WHEN COALESCE(u.USAGE_ROW_COUNT, 0) = 0 THEN 'NOT_LOADED'
        ELSE 'LOADED'
    END                                                                              AS DATA_LOAD_STATUS
FROM grid g
LEFT JOIN output_agg   o  ON o.VENDOR = g.VENDOR AND o.BILLING_MONTH = g.BILLING_MONTH
LEFT JOIN usage_agg    u  ON u.VENDOR = g.VENDOR AND u.BILLING_MONTH = g.BILLING_MONTH
ORDER BY g.VENDOR, g.BILLING_MONTH;
"""
    require_sql(conn, summary_sql, "THIRD_PARTY_RECON_SUMMARY_PROD")

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
