"""
Populate TRT_MATCH_KEY and prune orphans for SentinelOne + Exium SKU map rows.

Audit results (2026-08-28):
  SentinelOne (79 mapped CW_SKUs, 16 UNMAPPED marker rows):
    17 in active TRT -> set TRT_MATCH_KEY = CW_SKU
    3  in Zuora/Marketplace only -> KEEP (no TRT_MATCH_KEY)
    59 unused everywhere -> PRUNE
    16 UNMAPPED -> KEEP (true unmapped, do not touch)

  Exium (5 mapped CW_SKUs + 1 empty CW_SKU row):
    5 in active TRT (100%) -> set TRT_MATCH_KEY = CW_SKU
    1 empty CW_SKU -> PRUNE
"""
import sys
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(
    role="DEVELOPER",
    warehouse="REPORTING_WH",
    database="ANALYTICS_DEV",
    schema="DBT_NFOLD_TRANSFORMATION",
)

MAP = "ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SKU_MAP_PROD"
TRT_RAW = "ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE"


def sniff(label):
    print(f"\n--- {label} ---")
    q = f"""
      SELECT
        vendor,
        COUNT(*) AS row_ct,
        COUNT(DISTINCT cw_sku) AS distinct_cw_sku,
        COUNT(CASE WHEN TRIM(COALESCE(trt_match_key, '')) <> '' THEN 1 END) AS trt_key_populated
      FROM {MAP}
      WHERE vendor IN ('SentinelOne', 'Exium')
      GROUP BY 1 ORDER BY 1
    """
    print(fetch_dataframe(q, conn=conn).to_string(index=False))


sniff("BEFORE")

cur = conn.cursor()

# ---------- SentinelOne: populate TRT_MATCH_KEY for TRT-active CW_SKUs ----------
sql_s1_populate = f"""
UPDATE {MAP} m
SET trt_match_key = m.cw_sku
WHERE m.vendor = 'SentinelOne'
  AND m.cw_sku IS NOT NULL
  AND TRIM(m.cw_sku) <> ''
  AND m.cw_sku <> 'UNMAPPED'
  AND EXISTS (
    SELECT 1 FROM {TRT_RAW} t
    WHERE t.product_sku = m.cw_sku
      AND t.on_date >= DATEADD(day, -120, CURRENT_TIMESTAMP())
  )
"""
cur.execute(sql_s1_populate)
print(f"\nS1 populate TRT_MATCH_KEY: {cur.rowcount} rows updated")

# ---------- Exium: populate TRT_MATCH_KEY for TRT-active CW_SKUs ----------
sql_ex_populate = f"""
UPDATE {MAP} m
SET trt_match_key = m.cw_sku
WHERE m.vendor = 'Exium'
  AND m.cw_sku IS NOT NULL
  AND TRIM(m.cw_sku) <> ''
  AND m.cw_sku <> 'UNMAPPED'
  AND EXISTS (
    SELECT 1 FROM {TRT_RAW} t
    WHERE t.product_sku = m.cw_sku
      AND t.on_date >= DATEADD(day, -120, CURRENT_TIMESTAMP())
  )
"""
cur.execute(sql_ex_populate)
print(f"Exium populate TRT_MATCH_KEY: {cur.rowcount} rows updated")

# ---------- SentinelOne: prune truly-unused orphan rows ----------
# Guard rails:
#   * only rows where CW_SKU is populated and not 'UNMAPPED'
#   * only rows whose CW_SKU has ZERO matches in raw TRT (last 120 days),
#     ZERO matches in Zuora billing, ZERO matches in Marketplace,
#     AND ZERO matches in SENTINELONE_RECON_DETAIL vendor_product.
sql_s1_prune = f"""
DELETE FROM {MAP}
WHERE vendor = 'SentinelOne'
  AND cw_sku IS NOT NULL
  AND TRIM(cw_sku) <> ''
  AND cw_sku <> 'UNMAPPED'
  AND cw_sku NOT IN (
      SELECT DISTINCT product_sku
      FROM {TRT_RAW}
      WHERE on_date >= DATEADD(day, -120, CURRENT_TIMESTAMP())
        AND product_sku IS NOT NULL
  )
  AND cw_sku NOT IN (
      SELECT DISTINCT product_sku
      FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
      WHERE vendor = 'SentinelOne' AND product_sku IS NOT NULL
  )
  AND cw_sku NOT IN (
      SELECT DISTINCT product_sku
      FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
      WHERE vendor = 'SentinelOne' AND product_sku IS NOT NULL
  )
  AND cw_sku NOT IN (
      SELECT DISTINCT vendor_product
      FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.SENTINELONE_RECON_DETAIL
      WHERE vendor_product IS NOT NULL
  )
"""
cur.execute(sql_s1_prune)
print(f"S1 prune orphan rows: {cur.rowcount} rows deleted")

# ---------- Exium: prune the empty-CW_SKU row ----------
sql_ex_prune = f"""
DELETE FROM {MAP}
WHERE vendor = 'Exium'
  AND (cw_sku IS NULL OR TRIM(cw_sku) = '')
"""
cur.execute(sql_ex_prune)
print(f"Exium prune empty CW_SKU rows: {cur.rowcount} rows deleted")

conn.commit()
cur.close()

sniff("AFTER")

# Final verification: show TRT_MATCH_KEY assignments
print("\n--- SentinelOne TRT_MATCH_KEY populated ---")
q = f"""
SELECT DISTINCT cw_sku, trt_match_key
FROM {MAP}
WHERE vendor = 'SentinelOne' AND trt_match_key IS NOT NULL
ORDER BY 1
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n--- Exium TRT_MATCH_KEY populated ---")
q = f"""
SELECT DISTINCT cw_sku, trt_match_key
FROM {MAP}
WHERE vendor = 'Exium'
ORDER BY 1
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n--- SentinelOne remaining rows (KEEP w/o TRT_MATCH_KEY: 3 zuora/mkt + 16 UNMAPPED) ---")
q = f"""
SELECT
  CASE WHEN cw_sku = 'UNMAPPED' THEN 'UNMAPPED' ELSE 'HAS_CW_SKU' END AS bucket,
  COUNT(*) AS row_ct
FROM {MAP}
WHERE vendor = 'SentinelOne' AND (trt_match_key IS NULL OR TRIM(trt_match_key) = '')
GROUP BY 1
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

conn.close()
print("\nDONE")
