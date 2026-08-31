"""Check whether orphan (no-CW_SKU) Acronis SKU-map rows are actually used downstream."""
import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects')
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH',
                                database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')

VENDOR_SKUS = ['SBDAMSENS','SBDBMSENS','SBDCMSENS','SBDDMSENS','SESDMSENS',
               'SH1AMSENS','SHAAMSENS','SHJAMSENS','SPSAMSENS',
               'SRDUMSENS','SREUMSENS','SRFUMSENS','SUPDMSENS']
QUOTED = ','.join(repr(k) for k in VENDOR_SKUS)

print("=== 1) In RAW ACRONIS_USAGE ===")
q1 = f"""
SELECT UPPER(TRIM(vendor_product_sku)) AS vendor_sku,
       COUNT(*) AS rows_n, SUM(quantity) AS qty, SUM(amount) AS amt
FROM ACRONIS_USAGE
WHERE UPPER(TRIM(vendor_product_sku)) IN ({QUOTED})
GROUP BY 1 ORDER BY 1
"""
df = fetch_dataframe(q1, conn=conn)
print(df.to_string(index=False) if not df.empty else "(none)")

print("\n=== 2) In Zuora source (vendor=Acronis) ===")
q2 = f"""
SELECT UPPER(TRIM(product_sku)) AS product_sku, COUNT(*) AS rows_n,
       SUM(qty) AS qty, SUM(charge_amount_usd) AS amt
FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
WHERE vendor='Acronis' AND UPPER(TRIM(product_sku)) IN ({QUOTED})
GROUP BY 1 ORDER BY 1
"""
df = fetch_dataframe(q2, conn=conn)
print(df.to_string(index=False) if not df.empty else "(none)")

print("\n=== 3) In raw TRT usage (charge_sku = VENDOR_SKU-001) ===")
suffix = ','.join(repr(f"{sku}-001") for sku in VENDOR_SKUS)
q3 = f"""
SELECT UPPER(TRIM(charge_sku)) AS charge_sku, COUNT(*) AS rows_n
FROM ANALYTICS.DBO_BASE_CW_DP_TRT.BASE_CW_DP_TRT_V_CS_BILLING_PRODUCT_USAGE
WHERE UPPER(TRIM(charge_sku)) IN ({suffix})
GROUP BY 1 ORDER BY 1
"""
df = fetch_dataframe(q3, conn=conn)
print(df.to_string(index=False) if not df.empty else "(none)")

print("\n=== 4) In ACRONIS_RECON_DETAIL (as VENDOR_PRODUCT) ===")
q4 = f"""
SELECT UPPER(TRIM(vendor_product)) AS vendor_product, COUNT(*) AS rows_n,
       SUM(vendor_quantity) AS qty, SUM(vendor_amount) AS amt
FROM ACRONIS_RECON_DETAIL
WHERE UPPER(TRIM(vendor_product)) IN ({QUOTED})
GROUP BY 1 ORDER BY 1
"""
df = fetch_dataframe(q4, conn=conn)
print(df.to_string(index=False) if not df.empty else "(none)")

print("\n=== 5) Marketplace source ===")
q5 = f"""
SELECT UPPER(TRIM(product_sku)) AS product_sku, COUNT(*) AS rows_n,
       SUM(qty) AS qty, SUM(amount) AS amt
FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
WHERE vendor='Acronis' AND UPPER(TRIM(product_sku)) IN ({QUOTED})
GROUP BY 1 ORDER BY 1
"""
df = fetch_dataframe(q5, conn=conn)
print(df.to_string(index=False) if not df.empty else "(none)")

conn.close()
