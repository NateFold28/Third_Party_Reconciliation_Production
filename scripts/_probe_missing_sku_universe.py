"""What SKUs actually appear in USAGE_PROD for Exium/SentinelOne/Webroot?
And what's in the manual recon vs the vendor usage exports?"""
import sys
sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection(
    role="DEVELOPER", warehouse="REPORTING_WH",
    database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
)
c = conn.cursor()

for v in ("Exium", "SentinelOne", "Webroot"):
    print(f"\n=== {v}: distinct VENDOR_PRODUCT_SKU rows in USAGE_PROD ===")
    c.execute(f"""
        SELECT VENDOR_PRODUCT_SKU,
               COUNT(*) AS row_ct,
               COUNT(DISTINCT VENDOR_PARTNER_NAME) AS partners,
               ROUND(AVG(UNIT_PRICE), 4) AS avg_up,
               ROUND(SUM(AMOUNT), 0) AS total_amt
        FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
        WHERE VENDOR = '{v}'
        GROUP BY 1
        ORDER BY 2 DESC
    """)
    print(f"  {'SKU':<40} {'rows':>7} {'partners':>10} {'avg_up':>10} {'total_amt':>15}")
    for r in c.fetchall():
        sku = str(r[0])[:38]
        print(f"  {sku:<40} {r[1]:>7,} {r[2]:>10,} {r[3]!s:>10} {'$'+f'{r[4]:,.0f}' if r[4] is not None else 'null':>15}")

# Also look at what CW/Zuora billing has for these vendors as candidate CW_SKUs
print("\n=== Zuora billing SKUs found for these 3 vendors (top 20 by row) ===")
for v in ("Exium", "SentinelOne", "Webroot"):
    print(f"\n-- Zuora rows attributed to {v} --")
    # Use vendor-name pattern in product / description as loose match
    c.execute(f"""
        SELECT PRODUCT_SKU, COUNT(*) AS r
        FROM ANALYTICS_DEV.DBT_NFOLD.ZUORA_THIRD_PARTY_RECON_BASE
        WHERE VENDOR_NAME = '{v}'
        GROUP BY 1 ORDER BY 2 DESC LIMIT 20
    """)
    for r in c.fetchall():
        print(f"    {str(r[0]):<40} {r[1]:>10,}")

conn.close()
