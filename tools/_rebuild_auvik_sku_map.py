"""
_rebuild_auvik_sku_map.py -- Rebuild Auvik rows in THIRD_PARTY_RECON_SKU_MAP_PROD.

Motivation:
The current 151 Auvik rows in THIRD_PARTY_RECON_SKU_MAP_PROD carry three
family-code values in CW_SKU (`AUVIK-ESSENTIALS`, `AUVIKPERFORMANCADDON`,
`AUVIK-SRM-SAM-MSP`) that do NOT exist in raw TRT or Zuora as actual
`product_sku` values. As a result, the reconciler's cw_sku_map join
(Auvik_Reconciliation_Script_Prod.sql line 263) never matches, and the
Auvik CLEAR_PCT floats in the 36-52% (rows) / 51-62% (amount) band.

June 2026 evidence:
    * Zuora billed 25 distinct Auvik CW SKUs; SKU_MAP covered 0 of them.
    * Raw TRT holds 16 seed-tagged Auvik SKUs in June = $2.77M activity;
      SKU_MAP covered 0 of them.
    * seed__product_categorization (vendor='Auvik') carries 336 distinct
      SKUs including every one Zuora billed and every one TRT saw.

Fix:
    1) NULL out the fake CW_SKU on the existing 151 vendor-side rows.
       They retain their VENDOR_PRODUCT -> SKU_MATCH_KEY role for vendor
       invoice classification (vendor_product_map CTE in the reconciler)
       but stop injecting fake CW_SKU values into cw_sku_map.
    2) INSERT 336 seed-derived CW-side rows with:
         CW_SKU        = seed.prod_sku
         SKU_MATCH_KEY = 'AUVIK_' || <CMS|CW> || '_' || <ASM|PERFORMANCE|ESSENTIALS>
         TRT_MATCH_KEY = seed.prod_sku (matches raw TRT.product_sku)
         VENDOR_PRODUCT = NULL (CW-side rows are not vendor invoice keys)
         MAPPING_NOTES = 'Seed-derived Auvik CW SKU ... (2026-08-30 rebuild)'
    3) Refresh RECON_SKU_MAP so vendor recon scripts see the change.

Idempotent: safe to re-run. Step 1 is a no-op after the first run
(CW_SKU already NULL); step 2 deletes prior seed-derived rows before
inserting.
"""
from __future__ import annotations
import sys

sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe  # noqa: E402


SEED_INSERT_SQL = """
INSERT INTO THIRD_PARTY_RECON_SKU_MAP_PROD
    (VENDOR, VENDOR_PRODUCT, VENDOR_SKU, CW_SKU, SKU_MATCH_KEY, MAPPING_NOTES,
     CONTRACT_COST_RATE, CW_RETAIL_RATE, TRT_MATCH_KEY)
SELECT
    'Auvik'::VARCHAR                                             AS VENDOR,
    NULL::VARCHAR                                                AS VENDOR_PRODUCT,
    NULL::VARCHAR                                                AS VENDOR_SKU,
    p.prod_sku::VARCHAR                                          AS CW_SKU,
    ('AUVIK_' ||
        CASE WHEN UPPER(p.prod_sku) LIKE 'CMS%' THEN 'CMS' ELSE 'CW' END || '_' ||
        CASE
            WHEN UPPER(COALESCE(pr.name, p.prod_sku)) LIKE '%ASM%'
              OR UPPER(COALESCE(pr.name, p.prod_sku)) LIKE '%SAM%' THEN 'ASM'
            WHEN UPPER(COALESCE(pr.name, p.prod_sku)) LIKE '%PERFORMANCE%'
              OR UPPER(COALESCE(pr.name, p.prod_sku)) LIKE '%PARM%'
              OR UPPER(COALESCE(pr.name, p.prod_sku)) LIKE '%ADDON%'
              OR UPPER(COALESCE(pr.name, p.prod_sku)) LIKE '%ADD-ON%'
              OR UPPER(COALESCE(pr.name, p.prod_sku)) LIKE '%AUVIKPERFORMANCADDON%' THEN 'PERFORMANCE'
            ELSE 'ESSENTIALS'
        END)::VARCHAR                                            AS SKU_MATCH_KEY,
    'Seed-derived Auvik CW SKU from seed__product_categorization (2026-08-30 rebuild)'::VARCHAR AS MAPPING_NOTES,
    NULL::FLOAT                                                  AS CONTRACT_COST_RATE,
    NULL::FLOAT                                                  AS CW_RETAIL_RATE,
    p.prod_sku::VARCHAR                                          AS TRT_MATCH_KEY
FROM analytics.dbo_transformation.seed__product_categorization p
LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__product pr
    ON pr.product_code = p.prod_sku AND pr.is_deleted = FALSE
WHERE p.vendor = 'Auvik'
"""

# Legacy vendor-side rows keep their VENDOR_PRODUCT role. We null out CW_SKU
# because the values (AUVIK-ESSENTIALS etc.) are family codes, not real SKUs.
NULL_OUT_LEGACY_SQL = """
UPDATE THIRD_PARTY_RECON_SKU_MAP_PROD
SET CW_SKU = NULL,
    MAPPING_NOTES = COALESCE(MAPPING_NOTES, '') ||
        ' | 2026-08-30: retired legacy CW_SKU family-code (not real product_sku)'
WHERE VENDOR = 'Auvik'
  AND VENDOR_PRODUCT IS NOT NULL
  AND UPPER(TRIM(CW_SKU)) IN ('AUVIK-ESSENTIALS','AUVIKPERFORMANCADDON','AUVIK-SRM-SAM-MSP')
  AND MAPPING_NOTES NOT ILIKE '%2026-08-30: retired legacy CW_SKU family-code%'
"""

DELETE_PRIOR_SEED_SQL = """
DELETE FROM THIRD_PARTY_RECON_SKU_MAP_PROD
WHERE VENDOR = 'Auvik'
  AND MAPPING_NOTES ILIKE 'Seed-derived Auvik CW SKU%'
"""

# Rebuild RECON_SKU_MAP so the reconciler sees the new rows.
# Mirror the CREATE OR REPLACE TABLE body from
# Maps/sql/02_unified_reference_maps.sql (section 2).
REFRESH_RECON_SKU_MAP_SQL = """
CREATE OR REPLACE TABLE RECON_SKU_MAP AS
WITH sku_map_seed AS (
    SELECT DISTINCT
        VENDOR::VARCHAR             AS VENDOR,
        VENDOR_PRODUCT::VARCHAR     AS VENDOR_PRODUCT,
        VENDOR_SKU::VARCHAR         AS VENDOR_SKU,
        CW_SKU::VARCHAR             AS CW_SKU,
        SKU_MATCH_KEY::VARCHAR      AS SKU_MATCH_KEY,
        TRT_MATCH_KEY::VARCHAR      AS TRT_MATCH_KEY,
        MAPPING_NOTES::VARCHAR      AS MAPPING_NOTES,
        CONTRACT_COST_RATE::FLOAT   AS CONTRACT_COST_RATE,
        CW_RETAIL_RATE::FLOAT       AS CW_RETAIL_RATE
    FROM THIRD_PARTY_RECON_SKU_MAP_PROD
)
SELECT
    VENDOR,
    VENDOR_PRODUCT,
    VENDOR_SKU,
    CASE
        WHEN VENDOR = 'Acronis' THEN
            CASE
                WHEN TRIM(COALESCE(CW_SKU, '')) = '' THEN 'UNMATCHED'
                WHEN REGEXP_LIKE(TRIM(CW_SKU), '^[0-9]+(\\.[0-9]+)?$') THEN 'UNMATCHED'
                WHEN UPPER(TRIM(CW_SKU)) IN ('ST5AMSENS') THEN 'UNMATCHED'
                WHEN UPPER(TRIM(COALESCE(SKU_MATCH_KEY, ''))) = 'SPEAMSENS'
                     AND UPPER(TRIM(CW_SKU)) IN ('SPFAMSENS', 'SPGAMSENS') THEN 'UNMATCHED'
                WHEN UPPER(TRIM(COALESCE(SKU_MATCH_KEY, ''))) = 'SRIAMSENS'
                     AND UPPER(TRIM(CW_SKU)) = 'SP4BMSENS' THEN 'UNMATCHED'
                WHEN UPPER(TRIM(COALESCE(SKU_MATCH_KEY, ''))) = 'SPGAMSENS'
                     AND UPPER(TRIM(CW_SKU)) = 'SP4BMSENS' THEN 'UNMATCHED'
                WHEN UPPER(TRIM(COALESCE(SKU_MATCH_KEY, ''))) = 'SPDAMSENS'
                     AND UPPER(TRIM(CW_SKU)) = 'SPIAMSENS' THEN 'UNMATCHED'
                ELSE CW_SKU
            END
        ELSE CW_SKU
    END AS CW_SKU,
    SKU_MATCH_KEY,
    TRT_MATCH_KEY,
    MAPPING_NOTES,
    CONTRACT_COST_RATE,
    CW_RETAIL_RATE
FROM sku_map_seed
"""


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    cur = conn.cursor()
    try:
        print("=== BEFORE state ===")
        cur.execute("""
            SELECT COUNT(*) AS total_rows,
                   COUNT_IF(CW_SKU IS NOT NULL) AS with_cw_sku,
                   COUNT_IF(VENDOR_PRODUCT IS NOT NULL) AS with_vendor_product,
                   COUNT(DISTINCT CW_SKU) AS distinct_cw_skus
            FROM THIRD_PARTY_RECON_SKU_MAP_PROD WHERE VENDOR='Auvik'
        """)
        row = cur.fetchone()
        print(f"  total={row[0]:>4}  with_cw_sku={row[1]:>4}  with_vendor_product={row[2]:>4}  distinct_cw_sku={row[3]:>4}")

        print("\n=== STEP 1: NULL out legacy fake CW_SKU on vendor-side rows ===")
        cur.execute(NULL_OUT_LEGACY_SQL)
        print(f"  rows updated: {cur.rowcount}")

        print("\n=== STEP 2: DELETE prior seed-derived rebuild rows (idempotent) ===")
        cur.execute(DELETE_PRIOR_SEED_SQL)
        print(f"  rows deleted: {cur.rowcount}")

        print("\n=== STEP 3: INSERT seed-derived CW SKU rows ===")
        cur.execute(SEED_INSERT_SQL)
        print(f"  rows inserted: {cur.rowcount}")

        print("\n=== STEP 4: refresh RECON_SKU_MAP (source of truth for reconcilers) ===")
        cur.execute(REFRESH_RECON_SKU_MAP_SQL)
        print("  RECON_SKU_MAP rebuilt.")

        print("\n=== AFTER state ===")
        cur.execute("""
            SELECT COUNT(*) AS total_rows,
                   COUNT_IF(CW_SKU IS NOT NULL) AS with_cw_sku,
                   COUNT_IF(VENDOR_PRODUCT IS NOT NULL) AS with_vendor_product,
                   COUNT(DISTINCT CW_SKU) AS distinct_cw_skus
            FROM THIRD_PARTY_RECON_SKU_MAP_PROD WHERE VENDOR='Auvik'
        """)
        row = cur.fetchone()
        print(f"  total={row[0]:>4}  with_cw_sku={row[1]:>4}  with_vendor_product={row[2]:>4}  distinct_cw_sku={row[3]:>4}")

        print("\n=== VERIFY: user's target query now finds usage ===")
        cur.execute("""
            WITH skus AS (
                SELECT DISTINCT cw_sku
                FROM THIRD_PARTY_RECON_SKU_MAP_PROD
                WHERE vendor='Auvik' AND cw_sku IS NOT NULL
            )
            SELECT COUNT(DISTINCT t.product_sku) AS distinct_skus_hit,
                   COUNT(*) AS trt_rows,
                   ROUND(SUM(t.amt),0) AS trt_agent_cnt_sum
            FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE t
            JOIN skus s ON UPPER(TRIM(t.product_sku))=UPPER(TRIM(s.cw_sku))
            WHERE t.on_date >= '2026-06-01' AND t.on_date < '2026-07-01'
        """)
        r = cur.fetchone()
        print(f"  June TRT via new SKU_MAP: distinct_skus={r[0]}  rows={r[1]}  agent_cnt_sum={r[2]}")

        conn.commit()
        return 0
    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        return 1
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
