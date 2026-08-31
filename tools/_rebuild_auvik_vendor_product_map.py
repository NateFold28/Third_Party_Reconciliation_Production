"""Rebuild Auvik vendor_product map entries from live AUVIK_USAGE.

Purpose
=======
The canonical RECON_SKU_MAP (vendor='Auvik') currently has 56 rows with cw_sku
populated but zero rows with vendor_product/vendor_sku populated. The Auvik
reconciler references `vendor_product_map` (from RECON_SKU_MAP where
vendor_product IS NOT NULL AND sku_match_key matches AUVIK_(CMS|CW)_) as its
preferred classifier; it falls back to a hard-coded ILIKE regex when the map
does not have a matching row. This tool adds explicit vendor_product rows so
the map is authoritative instead of relying solely on regex fallback.

Classification uses the identical rules already in
Auvik_Reconciliation_Script_Prod.sql (AUVIK_(CMS|CW)_ESSENTIALS/PERFORMANCE/ASM)
so no CLEAR_PCT drift is introduced.

Idempotent: INSERT skips rows already present (vendor='Auvik',
vendor_product=X, sku_match_key=Y).
"""
from __future__ import annotations

import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")

from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe  # type: ignore


# -----------------------------------------------------------------------------
# Insert distinct (modifier, vendor_product_sku) pairs seen in AUVIK_USAGE
# since 2025, classified into AUVIK_(CMS|CW)_(ASM|PERFORMANCE|ESSENTIALS).
# -----------------------------------------------------------------------------
INSERT_AUVIK_VENDOR_PRODUCT_ROWS = """
INSERT INTO RECON_SKU_MAP
  (VENDOR, VENDOR_PRODUCT, VENDOR_SKU, CW_SKU, SKU_MATCH_KEY, TRT_MATCH_KEY,
   MAPPING_NOTES, CONTRACT_COST_RATE, CW_RETAIL_RATE)
WITH src AS (
    SELECT DISTINCT
        modifier                                             AS vendor_entity,
        TRIM(vendor_product_sku)                             AS vendor_product,
        CASE
            WHEN vendor_product_sku ILIKE '%asm%'
              OR vendor_product_sku ILIKE '%saas management%'
              OR vendor_product_sku ILIKE '%sam%'                 THEN 'ASM'
            WHEN vendor_product_sku ILIKE '%performance%'
              OR vendor_product_sku ILIKE '%addon%'
              OR vendor_product_sku ILIKE '%add-on%'
              OR vendor_product_sku ILIKE '%parm%'
              OR vendor_product_sku ILIKE '%prm-%'                THEN 'PERFORMANCE'
            ELSE 'ESSENTIALS'
        END                                                  AS product_group
    FROM AUVIK_USAGE
    WHERE billing_month >= '2025-01-01'
      AND vendor_product_sku IS NOT NULL
      AND TRIM(vendor_product_sku) <> ''
      AND modifier IN ('CW','CMS')
)
SELECT
    'Auvik'                                                  AS VENDOR,
    src.vendor_product                                        AS VENDOR_PRODUCT,
    src.vendor_product                                        AS VENDOR_SKU,
    NULL                                                      AS CW_SKU,
    'AUVIK_' || src.vendor_entity || '_' || src.product_group AS SKU_MATCH_KEY,
    NULL                                                      AS TRT_MATCH_KEY,
    '2026-08-30: rebuilt vendor_product map from AUVIK_USAGE' AS MAPPING_NOTES,
    NULL                                                      AS CONTRACT_COST_RATE,
    NULL                                                      AS CW_RETAIL_RATE
FROM src
WHERE NOT EXISTS (
    SELECT 1 FROM RECON_SKU_MAP m
    WHERE m.vendor = 'Auvik'
      AND UPPER(TRIM(m.vendor_product)) = UPPER(src.vendor_product)
      AND m.sku_match_key = 'AUVIK_' || src.vendor_entity || '_' || src.product_group
)
"""


# -----------------------------------------------------------------------------
# Sync THIRD_PARTY_RECON_SKU_MAP_PROD from RECON_SKU_MAP (column order differs)
# -----------------------------------------------------------------------------
SYNC_PROD_MAP_TRUNCATE = "TRUNCATE TABLE THIRD_PARTY_RECON_SKU_MAP_PROD"
SYNC_PROD_MAP_INSERT = """
INSERT INTO THIRD_PARTY_RECON_SKU_MAP_PROD
    (VENDOR, VENDOR_PRODUCT, VENDOR_SKU, CW_SKU, SKU_MATCH_KEY,
     MAPPING_NOTES, CONTRACT_COST_RATE, CW_RETAIL_RATE, TRT_MATCH_KEY)
SELECT
    VENDOR, VENDOR_PRODUCT, VENDOR_SKU, CW_SKU, SKU_MATCH_KEY,
    MAPPING_NOTES, CONTRACT_COST_RATE, CW_RETAIL_RATE, TRT_MATCH_KEY
FROM RECON_SKU_MAP
"""


def run(conn, sql: str, label: str):
    cur = conn.cursor()
    try:
        cur.execute(sql)
        print(f"  {label}: {cur.rowcount} row(s)")
    finally:
        cur.close()


def show_snapshot(conn, label: str):
    q = """
    SELECT vendor,
           COUNT(*)                                        AS rows_,
           COUNT_IF(vendor_product IS NOT NULL)            AS with_vp,
           COUNT_IF(vendor_sku IS NOT NULL)                AS with_vsku,
           COUNT_IF(cw_sku IS NOT NULL)                    AS with_cw,
           COUNT_IF(trt_match_key IS NOT NULL)             AS with_trt
    FROM RECON_SKU_MAP
    GROUP BY 1 ORDER BY 1
    """
    print(f"\n=== {label} ===")
    print(fetch_dataframe(q, conn=conn).to_string(index=False))


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        show_snapshot(conn, "BEFORE")
        cur = conn.cursor()
        cur.execute("BEGIN")
        try:
            run(conn, INSERT_AUVIK_VENDOR_PRODUCT_ROWS,
                "Auvik: insert vendor_product rows from AUVIK_USAGE")
            run(conn, SYNC_PROD_MAP_TRUNCATE,
                "Truncate THIRD_PARTY_RECON_SKU_MAP_PROD")
            run(conn, SYNC_PROD_MAP_INSERT,
                "Insert RECON_SKU_MAP -> THIRD_PARTY_RECON_SKU_MAP_PROD")
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        finally:
            cur.close()
        show_snapshot(conn, "AFTER")

        print("\n=== Auvik sku_match_key distribution AFTER ===")
        q = """
        SELECT sku_match_key,
               COUNT(*)                             AS rows_,
               COUNT_IF(vendor_product IS NOT NULL) AS with_vp,
               COUNT_IF(cw_sku IS NOT NULL)         AS with_cw
        FROM RECON_SKU_MAP WHERE vendor='Auvik'
        GROUP BY 1 ORDER BY 2 DESC
        """
        print(fetch_dataframe(q, conn=conn).to_string(index=False))

        print("\n=== PROD parity check ===")
        q = """
        SELECT 'RECON_SKU_MAP'                          AS tbl, COUNT(*) AS rows_ FROM RECON_SKU_MAP
        UNION ALL
        SELECT 'THIRD_PARTY_RECON_SKU_MAP_PROD'         AS tbl, COUNT(*) AS rows_ FROM THIRD_PARTY_RECON_SKU_MAP_PROD
        """
        print(fetch_dataframe(q, conn=conn).to_string(index=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
