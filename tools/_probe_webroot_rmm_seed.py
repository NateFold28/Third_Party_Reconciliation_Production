"""Validate engineering-provided seed pattern for Webroot RMM discount.

Ground truth expected for partner_id=391 on 2026-06-19:
    Webroot Desktop Endpoint = 184
    Webroot Server Endpoint  = 16
    RMM Desktop              = 123
    RMM Server               = 10
    Webroot Desktop to Bill  = 61  (= MAX(184 - 123, 0))
    Webroot Server to Bill   = 16  (unchanged; server not adjusted)
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"c:\Users\Nate.Fold\projects")

import pandas as pd

from TEMPLATES.Python.connection import fetch_dataframe, get_snowflake_connection

conn = get_snowflake_connection(
    role="DEVELOPER",
    warehouse="REPORTING_WH",
    database="ANALYTICS_DEV",
    schema="DBT_NFOLD_TRANSFORMATION",
)

pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 200)

# --- 1. Seed-derived RMM SKU universe (both Command + CW RMM engineering queries) ----
print("=" * 80)
print("STEP 1 : Extract RMM SKU universe from seed__product_categorization")
print("=" * 80)

rmm_sku_universe_sql = """
WITH command_skus AS (
    SELECT DISTINCT UPPER(TRIM(s.prod_sku)) AS product_sku
    FROM analytics.dbo_transformation.seed__product_categorization s
    LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__product p
      ON p.product_code = s.prod_sku
    WHERE (p.cws_product_line_c = 'UMM : Command' OR p.cws_brand_name_c = 'Command')
      AND s.prod_sku IS NOT NULL
),
cwrmm_skus AS (
    SELECT DISTINCT UPPER(TRIM(s.prod_sku)) AS product_sku
    FROM analytics.dbo_transformation.seed__product_categorization s
    LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__product p
      ON p.product_code = s.prod_sku
    WHERE NOT (p.cws_product_line_c = 'UMM : Command' OR p.cws_brand_name_c = 'Command')
      AND s.product_line = 'CW RMM'
      AND COALESCE(p.cws_brand_name_c, '') <> 'Auvik'
      AND COALESCE(p.cws_brand_name_c, '') <> 'Integrated Expert Services'
      AND s.prod_sku IS NOT NULL
)
SELECT 'command' AS source, COUNT(*) AS sku_count FROM command_skus
UNION ALL
SELECT 'cw_rmm', COUNT(*) FROM cwrmm_skus
UNION ALL
SELECT 'union_distinct',
       (SELECT COUNT(DISTINCT product_sku) FROM (
            SELECT product_sku FROM command_skus
            UNION SELECT product_sku FROM cwrmm_skus
       ))
"""
print(fetch_dataframe(rmm_sku_universe_sql, conn=conn).to_string(index=False))

# --- 2. Sample the seed rows to confirm content ----
print()
print("=" * 80)
print("STEP 2 : Sample RMM SKU rows so we can eyeball the shape")
print("=" * 80)

sample_sql = """
WITH rmm_skus AS (
    SELECT DISTINCT UPPER(TRIM(s.prod_sku)) AS product_sku
    FROM analytics.dbo_transformation.seed__product_categorization s
    LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__product p
      ON p.product_code = s.prod_sku
    WHERE (
              (p.cws_product_line_c = 'UMM : Command' OR p.cws_brand_name_c = 'Command')
           OR (
                  NOT (p.cws_product_line_c = 'UMM : Command' OR p.cws_brand_name_c = 'Command')
              AND s.product_line = 'CW RMM'
              AND COALESCE(p.cws_brand_name_c, '') <> 'Auvik'
              AND COALESCE(p.cws_brand_name_c, '') <> 'Integrated Expert Services'
              )
          )
      AND s.prod_sku IS NOT NULL
)
SELECT * FROM rmm_skus LIMIT 20
"""
print(fetch_dataframe(sample_sql, conn=conn).to_string(index=False))

# --- 3. Ground truth for partner 391 on 2026-06-19 ------
print()
print("=" * 80)
print("STEP 3 : Partner 391 on 2026-06-19 — Webroot desktop/server + RMM desktop/server")
print("=" * 80)

partner_sql = """
WITH webroot_skus AS (
    SELECT DISTINCT UPPER(TRIM(prod_sku)) AS product_sku
    FROM analytics.dbo_transformation.seed__product_categorization
    WHERE (vendor ILIKE '%webroot%' OR sub_category ILIKE '%webroot%')
      AND prod_sku IS NOT NULL
),
rmm_skus AS (
    SELECT DISTINCT UPPER(TRIM(s.prod_sku)) AS product_sku
    FROM analytics.dbo_transformation.seed__product_categorization s
    LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__product p
      ON p.product_code = s.prod_sku
    WHERE (
              (p.cws_product_line_c = 'UMM : Command' OR p.cws_brand_name_c = 'Command')
           OR (
                  NOT (p.cws_product_line_c = 'UMM : Command' OR p.cws_brand_name_c = 'Command')
              AND s.product_line = 'CW RMM'
              AND COALESCE(p.cws_brand_name_c, '') <> 'Auvik'
              AND COALESCE(p.cws_brand_name_c, '') <> 'Integrated Expert Services'
              )
          )
      AND s.prod_sku IS NOT NULL
),
usage AS (
    SELECT partner_id, product_sku, product_description, is_server, agent_cnt
    FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
    WHERE partner_id = '391'
      AND on_date::DATE = '2026-06-19'
)
SELECT 'webroot_desktop' AS metric,
       SUM(agent_cnt) AS qty
FROM usage
WHERE product_description = 'WebrootDesktop'
  AND UPPER(TRIM(product_sku)) IN (SELECT product_sku FROM webroot_skus)
UNION ALL
SELECT 'webroot_server',
       SUM(agent_cnt)
FROM usage
WHERE product_description = 'WebrootServer'
  AND UPPER(TRIM(product_sku)) IN (SELECT product_sku FROM webroot_skus)
UNION ALL
SELECT 'rmm_desktop',
       SUM(agent_cnt)
FROM usage
WHERE UPPER(TRIM(product_sku)) IN (SELECT product_sku FROM rmm_skus)
  AND COALESCE(is_server, 'N') = 'N'
UNION ALL
SELECT 'rmm_server',
       SUM(agent_cnt)
FROM usage
WHERE UPPER(TRIM(product_sku)) IN (SELECT product_sku FROM rmm_skus)
  AND is_server = 'Y'
"""
print(fetch_dataframe(partner_sql, conn=conn).to_string(index=False))

# --- 4. What RMM SKUs did partner 391 actually have on that date? ----
print()
print("=" * 80)
print("STEP 4 : What RMM product_skus does partner 391 have on 2026-06-19?")
print("=" * 80)

skus_sql = """
WITH rmm_skus AS (
    SELECT DISTINCT UPPER(TRIM(s.prod_sku)) AS product_sku
    FROM analytics.dbo_transformation.seed__product_categorization s
    LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__product p
      ON p.product_code = s.prod_sku
    WHERE (
              (p.cws_product_line_c = 'UMM : Command' OR p.cws_brand_name_c = 'Command')
           OR (
                  NOT (p.cws_product_line_c = 'UMM : Command' OR p.cws_brand_name_c = 'Command')
              AND s.product_line = 'CW RMM'
              AND COALESCE(p.cws_brand_name_c, '') <> 'Auvik'
              AND COALESCE(p.cws_brand_name_c, '') <> 'Integrated Expert Services'
              )
          )
      AND s.prod_sku IS NOT NULL
)
SELECT
    UPPER(TRIM(u.product_sku)) AS product_sku,
    u.product_description,
    u.is_server,
    SUM(u.agent_cnt) AS agent_cnt
FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
WHERE u.partner_id = '391'
  AND u.on_date::DATE = '2026-06-19'
  AND UPPER(TRIM(u.product_sku)) IN (SELECT product_sku FROM rmm_skus)
GROUP BY 1, 2, 3
ORDER BY agent_cnt DESC
LIMIT 30
"""
print(fetch_dataframe(skus_sql, conn=conn).to_string(index=False))

conn.close()
