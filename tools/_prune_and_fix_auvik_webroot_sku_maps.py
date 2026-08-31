"""
Prune + repair Auvik and Webroot RECON_SKU_MAP entries.

Discoveries motivating this script:
 - Auvik map: 487 rows total; only 47 SKUs actually appear in Zuora since 2025.
   * 440+ dead rows (legacy M2M migrations, promo/trial/NFR/onboarding stubs,
     retired vendor_product family codes with NULL cw_sku).
   * 30+ CMS-side Auvik SKUs (CULCSAS/CULCSAAS/CMS-EG-UMM/CMS-3P-UMM/M2MLICSAAS)
     are tagged as AUVIK_CW_* by the 2026-08-30 seed rebuild, but they belong
     to the CMS entity per the manual recon workbook. Reclassify to AUVIK_CMS_*.
   * AUVIK-INVENT-BILLING ($91K/yr) is missing from the map.
 - Webroot map: 233 rows; 40/40 active Zuora SKUs covered but 185 rows dead.
   * 36 NON_WEBROOT_PRODUCT_TAGGED + 13 OTHER_WEBROOT_TAGGED are explicit
     garbage (RMM/Continuity/discount/EOL products the map notes call out).
   * ~130 additional dead rows across GSM/SAT/DNS/BUNDLE with no Zuora
     activity since 2025.
   * Vendor-side SKU pairings (SAEP, SDNS, SECA + identity mappings) not
     populated -- fills VENDOR_SKU on remaining rows for traceability.

The reconciler contract:
 - Auvik reconciler classifies at SKU_MATCH_KEY grain
   (AUVIK_{CMS|CW}_{ESSENTIALS|PERFORMANCE|ASM}). Removing dead rows is safe.
 - Webroot reconciler filters cw_sku_group_map to
   SKU_MATCH_KEY IN ('GSM','DNS','SAT','BUNDLE'); NON_/OTHER_ tags are
   already excluded from the join -- deletion is cosmetic + reduces CW_SKUS
   fan-out noise on empty-vendor rows.

Idempotent: guards look for the audit tag in MAPPING_NOTES.
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe  # noqa: E402

AUDIT_TAG = "2026-08-30-b: pruned+CMS-retag"


def run_sql(conn, sql: str, label: str) -> None:
    print(f"\n---- {label} ----")
    cur = conn.cursor()
    try:
        cur.execute(sql)
        print(f"  rows affected: {cur.rowcount}")
    finally:
        cur.close()


def snapshot(conn, label: str) -> None:
    print(f"\n=== {label} ===")
    q = """
    SELECT vendor, COUNT(*) AS rows_,
           COUNT_IF(cw_sku IS NOT NULL) AS with_cw,
           COUNT_IF(vendor_product IS NOT NULL) AS with_vp,
           COUNT_IF(vendor_sku IS NOT NULL) AS with_vsku,
           COUNT_IF(trt_match_key IS NOT NULL) AS with_trt
    FROM RECON_SKU_MAP WHERE vendor IN ('Auvik','Webroot') GROUP BY 1 ORDER BY 1
    """
    print(fetch_dataframe(q, conn=conn).to_string(index=False))


# ============================================================================
# AUVIK
# ============================================================================
# 1) Delete legacy vendor_product-only rows (cw_sku IS NULL). The reconciler's
#    regex fallback handles these vendor product names fine; these 151 rows
#    are just noise.
AUVIK_DELETE_LEGACY_VP = """
DELETE FROM RECON_SKU_MAP
WHERE vendor = 'Auvik'
  AND cw_sku IS NULL
  AND vendor_product IS NOT NULL
"""

# 2) Delete dead seed rows: no Zuora activity since 2025 AND no TRT activity
#    since 2025. These are the M2M migrations, promos, trials, NFR, onboarding,
#    referral, and marketplace-only stubs the seed table carries.
AUVIK_DELETE_DEAD_SEED = """
DELETE FROM RECON_SKU_MAP m
USING (
  SELECT DISTINCT UPPER(TRIM(product_sku)) AS sku
  FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
  WHERE vendor='Auvik' AND billing_month >= '2025-01-01' AND product_sku IS NOT NULL
) act_z
JOIN (
  SELECT DISTINCT UPPER(TRIM(product_sku)) AS sku
  FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
  WHERE on_date >= '2025-01-01'
) act_t ON 1=1
WHERE FALSE
"""
# Snowflake requires MERGE or LEFT-ANTI-JOIN via USING for this pattern.
AUVIK_DELETE_DEAD_SEED = """
MERGE INTO RECON_SKU_MAP tgt
USING (
  WITH act_z AS (
    SELECT DISTINCT UPPER(TRIM(product_sku)) AS sku
    FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
    WHERE vendor='Auvik' AND billing_month >= '2025-01-01' AND product_sku IS NOT NULL
  ),
  act_t AS (
    SELECT DISTINCT UPPER(TRIM(product_sku)) AS sku
    FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
    WHERE on_date >= '2025-01-01' AND product_sku IS NOT NULL
  )
  SELECT m.vendor, m.cw_sku, m.sku_match_key, m.trt_match_key
  FROM RECON_SKU_MAP m
  LEFT JOIN act_z z ON z.sku = UPPER(TRIM(m.cw_sku))
  LEFT JOIN act_t t ON t.sku = UPPER(TRIM(m.trt_match_key))
  WHERE m.vendor = 'Auvik'
    AND m.cw_sku IS NOT NULL
    AND z.sku IS NULL
    AND t.sku IS NULL
) src
ON tgt.vendor = src.vendor
   AND COALESCE(tgt.cw_sku,'') = COALESCE(src.cw_sku,'')
   AND COALESCE(tgt.sku_match_key,'') = COALESCE(src.sku_match_key,'')
   AND COALESCE(tgt.trt_match_key,'') = COALESCE(src.trt_match_key,'')
WHEN MATCHED THEN DELETE
"""

# 3) Retag CMS Auvik SKUs. The seed rebuild inserted all Auvik rows as
#    AUVIK_CW_ESSENTIALS by default. The CULCSAS/CULCSAAS/CMS-EG-UMM/CMS-3P-UMM/
#    M2MLICSAAS/M2MLISAAS families belong to the CMS entity per the manual
#    recon workbook.
AUVIK_RETAG_CMS = """
UPDATE RECON_SKU_MAP
SET sku_match_key = REGEXP_REPLACE(sku_match_key, '^AUVIK_CW_', 'AUVIK_CMS_'),
    mapping_notes = COALESCE(mapping_notes, '') ||
                    ' | {tag}: retagged CW->CMS (CMS RMM Networks Auvik bundle)'
WHERE vendor = 'Auvik'
  AND sku_match_key ILIKE 'AUVIK_CW_%'
  AND (
        UPPER(cw_sku) LIKE 'CULCSAS%'
     OR UPPER(cw_sku) LIKE 'CULCSAAS%'
     OR UPPER(cw_sku) LIKE 'CMS-EG-UMM%'
     OR UPPER(cw_sku) LIKE 'CMS-3P-UMM%'
     OR UPPER(cw_sku) LIKE 'M2MLICSAAS%'
     OR UPPER(cw_sku) LIKE 'M2MLISAAS%'
  )
  AND mapping_notes NOT ILIKE '%retagged CW->CMS%'
""".replace("{tag}", AUDIT_TAG)

# 4) Add AUVIK-INVENT-BILLING (active $91K in Zuora, missing from map).
AUVIK_INSERT_MISSING = """
INSERT INTO RECON_SKU_MAP (
  vendor, vendor_product, vendor_sku, cw_sku, sku_match_key,
  trt_match_key, mapping_notes
)
SELECT 'Auvik', NULL, NULL, 'AUVIK-INVENT-BILLING', 'AUVIK_CW_ESSENTIALS',
       'AUVIK-INVENT-BILLING',
       '{tag}: added ConnectWise Invent Billing for Auvik Networks (active in Zuora)'
WHERE NOT EXISTS (
  SELECT 1 FROM RECON_SKU_MAP
  WHERE vendor='Auvik' AND UPPER(cw_sku)='AUVIK-INVENT-BILLING'
)
""".replace("{tag}", AUDIT_TAG)


# ============================================================================
# WEBROOT
# ============================================================================
# 1) Delete explicit-garbage tag rows (NON_WEBROOT_PRODUCT_TAGGED,
#    OTHER_WEBROOT_TAGGED). The map's own MAPPING_NOTES calls these out as
#    "not a direct one-to-one OpenText invoice line".
WEBROOT_DELETE_TAGGED_GARBAGE = """
DELETE FROM RECON_SKU_MAP
WHERE vendor = 'Webroot'
  AND sku_match_key IN ('NON_WEBROOT_PRODUCT_TAGGED', 'OTHER_WEBROOT_TAGGED')
"""

# 2) Delete dead rows: no Zuora activity since 2025.
WEBROOT_DELETE_DEAD = """
MERGE INTO RECON_SKU_MAP tgt
USING (
  WITH act_z AS (
    SELECT DISTINCT UPPER(TRIM(product_sku)) AS sku
    FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
    WHERE vendor='Webroot' AND billing_month >= '2025-01-01' AND product_sku IS NOT NULL
  )
  SELECT m.vendor, m.cw_sku, m.sku_match_key
  FROM RECON_SKU_MAP m
  LEFT JOIN act_z z ON z.sku = UPPER(TRIM(m.cw_sku))
  WHERE m.vendor = 'Webroot' AND m.cw_sku IS NOT NULL AND z.sku IS NULL
) src
ON tgt.vendor = src.vendor
   AND COALESCE(tgt.cw_sku,'') = COALESCE(src.cw_sku,'')
   AND COALESCE(tgt.sku_match_key,'') = COALESCE(src.sku_match_key,'')
WHEN MATCHED THEN DELETE
"""

# 3) Populate VENDOR_SKU on remaining rows. Per user's explicit mapping:
#      SAEP -> Webroot Endpoint Protection family (EPP/RMM CW SKUs)
#      SDNS -> Webroot DNS family (DNS + WSADNSP CW SKUs)
#      SECA -> Webroot SAT family (SAT + SEWRSSAT CW SKUs)
#    Identity mapping (VENDOR_SKU = CW_SKU) for the SEWRSGSM*/WRSECGSM*/
#    SEWRSSAT*/WSADNSP* families -- these are the GSM/SAT/DNS tier SKUs
#    that Webroot invoices and CW rebills identically.
WEBROOT_PAINT_SAEP = """
UPDATE RECON_SKU_MAP
SET vendor_sku = 'SAEP',
    mapping_notes = COALESCE(mapping_notes,'') ||
                    ' | {tag}: SAEP vendor_sku paired'
WHERE vendor='Webroot'
  AND vendor_sku IS NULL
  AND (
        UPPER(cw_sku) IN (
          'CU-WEBROOT-EPP-RMM',
          'CW-RMM-WR-EEP-OVERAG',
          'CMS-EG-CYBR-SOLP-SAAS-MDRDSKTP',
          'CMS-EG-CYBR-SOLP-SAAS-MDRSERVR',
          'CMS-IH-CYBR-SOLP-SAAS-MDRDSKTP',
          'CMS-IH-CYBR-SOLP-SAAS-MDRSERVR'
        )
  )
""".replace("{tag}", AUDIT_TAG)

WEBROOT_PAINT_SDNS = """
UPDATE RECON_SKU_MAP
SET vendor_sku = 'SDNS',
    mapping_notes = COALESCE(mapping_notes,'') ||
                    ' | {tag}: SDNS vendor_sku paired'
WHERE vendor='Webroot'
  AND vendor_sku IS NULL
  AND UPPER(cw_sku) IN ('3P-SAAS30021010FFDNS','WSADNSP-STAND-ALONE','WSADNSP-IIT')
""".replace("{tag}", AUDIT_TAG)

WEBROOT_PAINT_SECA = """
UPDATE RECON_SKU_MAP
SET vendor_sku = 'SECA',
    mapping_notes = COALESCE(mapping_notes,'') ||
                    ' | {tag}: SECA vendor_sku paired'
WHERE vendor='Webroot'
  AND vendor_sku IS NULL
  AND UPPER(cw_sku) IN ('3P-SAAS30022019FFSAT',)
""".replace("{tag}", AUDIT_TAG).replace(",)", ")")

# Identity mapping: for GSM/SAT/DNS tier SKUs where Webroot vendor SKU == CW SKU.
WEBROOT_PAINT_IDENTITY = """
UPDATE RECON_SKU_MAP
SET vendor_sku = cw_sku,
    mapping_notes = COALESCE(mapping_notes,'') ||
                    ' | {tag}: identity vendor_sku=cw_sku (GSM/SAT/DNS tier)'
WHERE vendor='Webroot'
  AND vendor_sku IS NULL
  AND cw_sku IS NOT NULL
  AND sku_match_key IN ('GSM','SAT','DNS','BUNDLE')
  AND (
        UPPER(cw_sku) LIKE 'SEWRSGSM%'
     OR UPPER(cw_sku) LIKE 'WRSECGSM%'
     OR UPPER(cw_sku) LIKE 'SEWRSSAT%'
     OR UPPER(cw_sku) LIKE 'WSADNSP%'
     OR UPPER(cw_sku) LIKE '3RDPARTYSAAS%'
     OR UPPER(cw_sku) LIKE '3PARTYONPREM%'
  )
""".replace("{tag}", AUDIT_TAG)


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        snapshot(conn, "BEFORE")

        run_sql(conn, AUVIK_DELETE_LEGACY_VP, "Auvik: delete legacy vendor_product-only rows")
        run_sql(conn, AUVIK_DELETE_DEAD_SEED, "Auvik: delete dead seed rows")
        run_sql(conn, AUVIK_RETAG_CMS, "Auvik: retag CW->CMS for CULCSAS/CULCSAAS/etc")
        run_sql(conn, AUVIK_INSERT_MISSING, "Auvik: insert AUVIK-INVENT-BILLING")

        run_sql(conn, WEBROOT_DELETE_TAGGED_GARBAGE, "Webroot: delete NON_/OTHER_ tagged rows")
        run_sql(conn, WEBROOT_DELETE_DEAD, "Webroot: delete dead rows (no 2025+ Zuora activity)")
        run_sql(conn, WEBROOT_PAINT_SAEP, "Webroot: paint SAEP vendor_sku")
        run_sql(conn, WEBROOT_PAINT_SDNS, "Webroot: paint SDNS vendor_sku")
        run_sql(conn, WEBROOT_PAINT_SECA, "Webroot: paint SECA vendor_sku")
        run_sql(conn, WEBROOT_PAINT_IDENTITY, "Webroot: paint identity vendor_sku=cw_sku")

        conn.commit()

        snapshot(conn, "AFTER")

        print("\n=== Auvik: sku_match_key distribution AFTER ===")
        q = """
        SELECT sku_match_key, COUNT(*) AS rows_
        FROM RECON_SKU_MAP WHERE vendor='Auvik' GROUP BY 1 ORDER BY 2 DESC
        """
        print(fetch_dataframe(q, conn=conn).to_string(index=False))

        print("\n=== Webroot: sku_match_key distribution AFTER ===")
        q = """
        SELECT sku_match_key, COUNT(*) AS rows_,
               COUNT_IF(vendor_sku IS NOT NULL) AS with_vsku
        FROM RECON_SKU_MAP WHERE vendor='Webroot' GROUP BY 1 ORDER BY 2 DESC
        """
        print(fetch_dataframe(q, conn=conn).to_string(index=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
