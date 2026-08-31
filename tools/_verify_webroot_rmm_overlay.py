"""Verify the RMM overlay CTEs produce ground-truth results for partner 391."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from TEMPLATES.Python.connection import fetch_dataframe, get_snowflake_connection

conn = get_snowflake_connection()

overlay_sql = """
WITH webroot_trt_sku_universe AS (
    SELECT DISTINCT UPPER(TRIM(PRODUCT_SKU)) AS product_sku
    FROM ANALYTICS_DEV.DBT_NFOLD.FINAL_TPR_ENGINEERING_ZUORA_SOURCE_V2
    WHERE UPPER(VENDOR_NAME) = 'WEBROOT'
      AND PRODUCT_SKU IS NOT NULL
      AND INVOICE_STATUS = 'Posted'
      AND BILLING_MONTH >= '2026-01-01'
    UNION
    SELECT DISTINCT UPPER(TRIM(prod_sku)) AS product_sku
    FROM analytics.dbo_transformation.seed__product_categorization
    WHERE (vendor ILIKE '%webroot%' OR sub_category ILIKE '%webroot%')
      AND prod_sku IS NOT NULL
),
webroot_rmm_sku_universe AS (
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
webroot_rmm_daily AS (
    SELECT
        u.partner_id::VARCHAR AS cms_id,
        u.on_date::DATE       AS on_date,
        SUM(IFF(w.product_sku IS NOT NULL AND u.product_description = 'WebrootDesktop', u.agent_cnt::FLOAT, 0)) AS webroot_desktop_qty,
        SUM(IFF(w.product_sku IS NOT NULL AND u.product_description = 'WebrootServer',  u.agent_cnt::FLOAT, 0)) AS webroot_server_qty,
        SUM(IFF(r.product_sku IS NOT NULL AND COALESCE(u.is_server, 'N') = 'N', u.agent_cnt::FLOAT, 0)) AS rmm_desktop_qty,
        SUM(IFF(r.product_sku IS NOT NULL AND u.is_server = 'Y', u.agent_cnt::FLOAT, 0)) AS rmm_server_qty
    FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
    LEFT JOIN webroot_trt_sku_universe w ON UPPER(TRIM(u.product_sku)) = w.product_sku
    LEFT JOIN webroot_rmm_sku_universe r ON UPPER(TRIM(u.product_sku)) = r.product_sku
    WHERE u.on_date::DATE >= '2025-12-01'
      AND (w.product_sku IS NOT NULL OR r.product_sku IS NOT NULL)
    GROUP BY 1, 2
)
SELECT
    cms_id,
    on_date,
    webroot_desktop_qty,
    webroot_server_qty,
    rmm_desktop_qty,
    rmm_server_qty,
    (webroot_desktop_qty + webroot_server_qty)                    AS webroot_endpoint_qty_pit,
    (rmm_desktop_qty     + rmm_server_qty)                        AS rmm_endpoint_qty_pit,
    GREATEST(webroot_desktop_qty - rmm_desktop_qty, 0)
      + webroot_server_qty                                        AS webroot_endpoint_to_bill_pit,
    (webroot_desktop_qty + webroot_server_qty)
      - (GREATEST(webroot_desktop_qty - rmm_desktop_qty, 0)
         + webroot_server_qty)                                    AS rmm_discount_qty_pit
FROM webroot_rmm_daily
WHERE cms_id = '391' AND on_date = '2026-06-19'
"""

df = fetch_dataframe(overlay_sql, conn=conn)
print("=" * 80)
print("Overlay CTE result for partner 391 on 2026-06-19")
print("=" * 80)
print(df.to_string(index=False))
print()

expected = {
    "webroot_desktop_qty": 184,
    "webroot_server_qty": 16,
    "rmm_desktop_qty": 123,
    "rmm_server_qty": 10,
    "webroot_endpoint_qty_pit": 200,          # 184+16
    "rmm_endpoint_qty_pit": 133,              # 123+10
    "webroot_endpoint_to_bill_pit": 77,       # max(184-123,0)+16 = 61+16
    "rmm_discount_qty_pit": 123,              # 200-77
}

if df.empty:
    print("FAIL: no rows returned")
    sys.exit(1)

row = df.iloc[0]
ok = True
for key, want in expected.items():
    got = row[key.upper()] if key.upper() in row.index else row[key]
    match = float(got) == float(want)
    marker = "OK  " if match else "FAIL"
    print(f"  {marker}  {key:35s}  got={got}  want={want}")
    if not match:
        ok = False

print()
print("ALL MATCH" if ok else "MISMATCH")
sys.exit(0 if ok else 1)
