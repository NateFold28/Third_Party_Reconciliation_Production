"""Validate our Webroot RMM overlay against the 50-partner ground-truth table
that engineering provided on 2026-08-29 for the 2026-06-19 snapshot day.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from TEMPLATES.Python.connection import fetch_dataframe, get_snowflake_connection

# ---------------------------------------------------------------------------
# Ground truth pasted by user on 2026-08-29
# columns: partner_id, partner_name, webroot_desktop, webroot_server,
#          rmm_desktop, rmm_server, wr_desktop_for_bill, wr_server_for_bill
# ---------------------------------------------------------------------------
GROUND_TRUTH = [
    (23,  "Clare Computer Solutions",             100,  8, 1921, 256,  0,  8),
    (31,  "Convergent Technologies Group",         31,  5,   42,  45,  0,  5),
    (58,  "Exp1248",                               15,  0,   24,   1,  0,  0),
    (63,  "Fluent Network Services",               30,  0,  592,  21,  0,  0),
    (104, "Optimal Networks Inc",                 274,  8,    0,   0, 274, 8),
    (142, "Internal Engineering-TEST",              5,  1,  287, 174,  0,  1),
    (235, "JJ Integrations",                       11,  0,   16,   0,  0,  0),
    (251, "General Informatics",                   16,  9,10954,1554,  0,  9),
    (267, "EKARU",                                266, 13, 1332,  82,  0, 13),
    (283, "CMIT Solutions of Fort Worth Downtown 4",128,10,  218,  92,  0, 10),
    (300, "CMIT San Antonio North Central 145",   187, 14,  288,  18,  0, 14),
    (301, "CMIT National",                         16,  2,   14,   6,  2,  2),
    (318, "The Strickland Group Inc",               8,  0,    0,   0,  8,  0),
    (374, "Rubino Network Consulting dba RNC Inc",625,  0, 1130,   6,  0,  0),
    (391, "Computer Systems Development Services",184, 16,  123,  10, 61, 16),
    (413, "CMIT Everett 172",                     254, 52, 1011, 163,  0, 52),
    (416, "eMazzanti Technologies",              1355,103, 2211, 223,  0,103),
    (421, "Benchmark Information Technology",     205, 23,  403,  42,  0, 23),
    (431, "CMIT Solutions of The Florida Parishes 31",199,13,207, 15,  0, 13),
    (443, "Rehmann",                              830,135, 2785, 480,  0,135),
    (453, "CyberPrairie",                          46,  5,  460,  32,  0,  5),
    (457, "3L Partners",                            1,  1,    1,   6,  0,  1),
    (466, "MySherpa",                               1,  0,    0,   0,  1,  0),
    (467, "Drum Communications",                   49,  4,  127,   9,  0,  4),
    (505, "CMIT Solutions of Wexford",              68,  6,   41,   5, 27,  6),
    (531, "Greystone Technology Group",            266, 11,17652,1190,  0, 11),
    (553, "Pointivity",                            440, 10, 1278, 101,  0, 10),
    (590, "Xceed Networks LLC",                     53,  2,  150,   8,  0,  2),
    (592, "CMIT South Brevard 179",                208,  5,  242,   6,  0,  5),
    (612, "J J Etc Inc",                             7,  2,   27,   9,  0,  2),
    (660, "Anchor Networks",                         1,  0,    2,   1,  0,  0),
    (665, "Cameron Park Computer Services",          8,  0,   12,   0,  0,  0),
    (673, "Techs In Suits",                        132, 13,  150,  12,  0, 13),
    (684, "TACPros",                                60,  4,   95,   4,  0,  4),
    (707, "Sklar Technology Partners",             382, 42, 1693, 153,  0, 42),
    (738, "Iceberg Managed Solutions LLC",          25,  2,   25,   3,  0,  2),
    (742, "Optfinity",                               6,  0,  363,  24,  0,  0),
    (743, "AkitaTek Ltd",                           54,  5,   71,   6,  0,  5),
    (747, "CMIT Solutions of NYCE",                  1,  0,    2,   0,  0,  0),
    (751, "GizmoFish",                            1253, 44, 1750,  63,  0, 44),
    (845, "ACE Computer Consultants",                8,  1,   16,   1,  0,  1),
    (876, "NorthEast Computer Services LLC",       346, 16,  456,  24,  0, 16),
    (888, "Alliant Technology Group",              854, 73, 1201,  86,  0, 73),
    (901, "Roadrunner Networking",                 268, 14,  493,  45,  0, 14),
    (917, "Downtown Computers",                    467, 23,  541,  31,  0, 23),
    (920, "SystemsNet",                            155,  3, 2318, 113,  0,  3),
    (951, "NCIC Consulting Group",                  70,  9,   85,   5,  0,  9),
    (976, "ProfIT CS LLC",                         115,  0,   61,   0, 54,  0),
    (977, "ABS Information Systems Inc",            11,  3, 2178,  80,  0,  3),
    (993, "Stevens and Stevens Ltd",                 3,  3,    0,   0,  3,  3),
    (995, "Vann Data Services Inc",                 24,  2, 1767, 141,  0,  2),
]

truth = pd.DataFrame(
    GROUND_TRUTH,
    columns=[
        "partner_id", "partner_name",
        "gt_webroot_desktop", "gt_webroot_server",
        "gt_rmm_desktop", "gt_rmm_server",
        "gt_wr_desktop_for_bill", "gt_wr_server_for_bill",
    ],
)
partner_ids = ",".join(str(p) for p in truth["partner_id"])

conn = get_snowflake_connection(
    role="DEVELOPER",
    warehouse="REPORTING_WH",
    database="ANALYTICS_DEV",
    schema="DBT_NFOLD_TRANSFORMATION",
)

# Same CTEs as the wired-in Webroot overlay — verbatim, so this is a true
# apples-to-apples validation of the production script.
overlay_sql = f"""
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
    -- (A) Command / MSP RMM
    SELECT DISTINCT UPPER(TRIM(s.prod_sku)) AS product_sku
    FROM analytics.dbo_transformation.seed__product_categorization s
    LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__product p
        ON p.product_code = s.prod_sku
    WHERE (p.cws_product_line_c = 'UMM : Command' OR p.cws_brand_name_c = 'Command')
      AND s.prod_sku IS NOT NULL
    UNION
    -- (B) CW RMM family (Auvik excluded; reconciled separately)
    SELECT DISTINCT UPPER(TRIM(s.prod_sku)) AS product_sku
    FROM analytics.dbo_transformation.seed__product_categorization s
    LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__product p
        ON p.product_code = s.prod_sku
    WHERE s.product_line = 'CW RMM'
      AND COALESCE(p.cws_brand_name_c, '') <> 'Auvik'
      AND NOT (p.cws_product_line_c = 'UMM : Command' OR p.cws_brand_name_c = 'Command')
      AND s.prod_sku IS NOT NULL
    UNION
    -- (C) Integrated Expert Services bundles carrying Webroot
    SELECT DISTINCT UPPER(TRIM(s.prod_sku)) AS product_sku
    FROM analytics.dbo_transformation.seed__product_categorization s
    LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__product p
        ON p.product_code = s.prod_sku
    WHERE p.cws_brand_name_c = 'Integrated Expert Services'
      AND s.prod_sku IS NOT NULL
)
SELECT
    u.partner_id::VARCHAR AS partner_id,
    u.on_date::DATE       AS on_date,
    SUM(IFF(w.product_sku IS NOT NULL AND u.product_description = 'WebrootDesktop', u.agent_cnt::FLOAT, 0)) AS webroot_desktop,
    SUM(IFF(w.product_sku IS NOT NULL AND u.product_description = 'WebrootServer',  u.agent_cnt::FLOAT, 0)) AS webroot_server,
    SUM(IFF(r.product_sku IS NOT NULL AND COALESCE(u.is_server,'N')='N', u.agent_cnt::FLOAT, 0)) AS rmm_desktop,
    SUM(IFF(r.product_sku IS NOT NULL AND u.is_server='Y',               u.agent_cnt::FLOAT, 0)) AS rmm_server
FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE u
LEFT JOIN webroot_trt_sku_universe w ON UPPER(TRIM(u.product_sku)) = w.product_sku
LEFT JOIN webroot_rmm_sku_universe r ON UPPER(TRIM(u.product_sku)) = r.product_sku
WHERE u.on_date::DATE = '2026-06-19'
  AND u.partner_id::VARCHAR IN ({partner_ids})
  AND (w.product_sku IS NOT NULL OR r.product_sku IS NOT NULL)
GROUP BY 1, 2
"""

ours = fetch_dataframe(overlay_sql, conn=conn)
ours["partner_id"] = ours["PARTNER_ID"].astype(int)
ours = ours.rename(
    columns={
        "WEBROOT_DESKTOP": "our_webroot_desktop",
        "WEBROOT_SERVER":  "our_webroot_server",
        "RMM_DESKTOP":     "our_rmm_desktop",
        "RMM_SERVER":      "our_rmm_server",
    }
)[
    ["partner_id", "our_webroot_desktop", "our_webroot_server",
     "our_rmm_desktop", "our_rmm_server"]
]

df = truth.merge(ours, on="partner_id", how="left").fillna(0)
df["our_wr_desktop_for_bill"] = (
    (df["our_webroot_desktop"] - df["our_rmm_desktop"]).clip(lower=0)
).astype(int)
df["our_wr_server_for_bill"] = df["our_webroot_server"].astype(int)

for col in [
    "our_webroot_desktop", "our_webroot_server",
    "our_rmm_desktop", "our_rmm_server",
]:
    df[col] = df[col].astype(int)

df["d_wr_desk"]      = df["our_webroot_desktop"]        - df["gt_webroot_desktop"]
df["d_wr_srv"]       = df["our_webroot_server"]         - df["gt_webroot_server"]
df["d_rmm_desk"]     = df["our_rmm_desktop"]            - df["gt_rmm_desktop"]
df["d_rmm_srv"]      = df["our_rmm_server"]             - df["gt_rmm_server"]
df["d_bill_desk"]    = df["our_wr_desktop_for_bill"]    - df["gt_wr_desktop_for_bill"]
df["d_bill_srv"]     = df["our_wr_server_for_bill"]     - df["gt_wr_server_for_bill"]
df["all_match"]      = (
    (df[["d_wr_desk", "d_wr_srv", "d_rmm_desk",
         "d_rmm_srv", "d_bill_desk", "d_bill_srv"]] == 0).all(axis=1)
)

pd.set_option("display.width", 260)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 100)

print("=" * 120)
print(f"Per-partner comparison — {len(df)} partners, snapshot 2026-06-19")
print("=" * 120)
show = df[[
    "partner_id", "partner_name",
    "gt_webroot_desktop", "our_webroot_desktop", "d_wr_desk",
    "gt_webroot_server",  "our_webroot_server",  "d_wr_srv",
    "gt_rmm_desktop",     "our_rmm_desktop",     "d_rmm_desk",
    "gt_rmm_server",      "our_rmm_server",      "d_rmm_srv",
    "gt_wr_desktop_for_bill", "our_wr_desktop_for_bill", "d_bill_desk",
    "gt_wr_server_for_bill",  "our_wr_server_for_bill",  "d_bill_srv",
    "all_match",
]]
show.columns = [
    "pid", "name",
    "GT_WD", "OUR_WD", "d_WD",
    "GT_WS", "OUR_WS", "d_WS",
    "GT_RD", "OUR_RD", "d_RD",
    "GT_RS", "OUR_RS", "d_RS",
    "GT_BD", "OUR_BD", "d_BD",
    "GT_BS", "OUR_BS", "d_BS",
    "match",
]
show["name"] = show["name"].str.slice(0, 26)
print(show.to_string(index=False))

print()
matches = int(df["all_match"].sum())
total   = len(df)
print(f"Fully-matching partners: {matches}/{total}")
mismatched = df.loc[~df["all_match"], "partner_id"].tolist()
if mismatched:
    print(f"Mismatched partner_ids: {mismatched}")
