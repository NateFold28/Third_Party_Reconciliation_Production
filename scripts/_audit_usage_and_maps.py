"""Audit: what does each vendor look like in THIRD_PARTY_RECON_VENDOR_USAGE_PROD?

For each vendor, report:
- row count
- distinct months
- pct rows with vendor_unit_price populated
- pct rows with vendor_amount populated
- avg unit price
- total amount
- do partner + sku maps have matching entries?

This tells us which ingestions are producing complete data and which are not.
"""
from __future__ import annotations
import sys
sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection

VENDORS = ("Acronis","Auvik","Bitdefender","ESET","Exium","KeepIT",
           "Proofpoint","SentinelOne","Webroot")

conn = get_snowflake_connection(
    role="DEVELOPER", warehouse="REPORTING_WH",
    database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
)
c = conn.cursor()

# Schema of USAGE_PROD
c.execute("""
    SELECT COLUMN_NAME, DATA_TYPE
    FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION'
      AND TABLE_NAME='THIRD_PARTY_RECON_VENDOR_USAGE_PROD'
    ORDER BY ORDINAL_POSITION
""")
print("=== THIRD_PARTY_RECON_VENDOR_USAGE_PROD schema ===")
for name, dtype in c.fetchall():
    print(f"  {name:<30} {dtype}")

# Per-vendor completeness audit
print("\n=== per-vendor completeness in USAGE_PROD ===")
print(f"{'VENDOR':<15} {'ROWS':>7} {'MONTHS':>7} {'UP % pop':>10} {'AMT % pop':>10} "
      f"{'AVG UP':>10} {'SUM AMT':>15} {'SUM QTY':>12}")
for v in VENDORS:
    c.execute(f"""
        SELECT COUNT(*),
               COUNT(DISTINCT BILLING_MONTH),
               ROUND(COUNT_IF(UNIT_PRICE IS NOT NULL)*100.0/COUNT(*),1),
               ROUND(COUNT_IF(AMOUNT IS NOT NULL AND AMOUNT != 0)*100.0/COUNT(*),1),
               ROUND(AVG(UNIT_PRICE), 4),
               ROUND(SUM(AMOUNT), 0),
               ROUND(SUM(QUANTITY), 0)
        FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
        WHERE VENDOR = '{v}'
    """)
    r = c.fetchone()
    if r and r[0]:
        print(f"{v:<15} {r[0]:>7,} {r[1]:>7} {r[2]:>10} {r[3]:>10} "
              f"{r[4] if r[4] is not None else 'NULL':>10} "
              f"{'$'+f'{r[5]:,.0f}' if r[5] is not None else 'NULL':>15} "
              f"{r[6] if r[6] is not None else 'NULL':>12}")
    else:
        print(f"{v:<15} 0 rows")

# Map coverage: partner-name resolution against RECON_PARTNER_MAP
print("\n=== partner map coverage (distinct USAGE partner names hit RECON_PARTNER_MAP.PARTNER_NAME for that vendor) ===")
print(f"{'VENDOR':<15} {'USAGE_PARTNERS':>16} {'MATCHED':>10} {'MATCH %':>10}")
for v in VENDORS:
    c.execute(f"""
        WITH up AS (
            SELECT DISTINCT UPPER(TRIM(VENDOR_PARTNER_NAME)) AS pn
            FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
            WHERE VENDOR = '{v}' AND VENDOR_PARTNER_NAME IS NOT NULL
        ),
        m AS (
            SELECT DISTINCT UPPER(TRIM(PARTNER_NAME)) AS pn
            FROM RECON_PARTNER_MAP WHERE VENDOR = '{v}'
        )
        SELECT COUNT(*) FROM up
    """)
    total = c.fetchone()[0]
    c.execute(f"""
        WITH up AS (
            SELECT DISTINCT UPPER(TRIM(VENDOR_PARTNER_NAME)) AS pn
            FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
            WHERE VENDOR = '{v}' AND VENDOR_PARTNER_NAME IS NOT NULL
        ),
        m AS (
            SELECT DISTINCT UPPER(TRIM(PARTNER_NAME)) AS pn
            FROM RECON_PARTNER_MAP WHERE VENDOR = '{v}'
        )
        SELECT COUNT(*) FROM up JOIN m USING(pn)
    """)
    matched = c.fetchone()[0]
    pct = round(matched * 100.0 / total, 1) if total else None
    print(f"{v:<15} {total:>16,} {matched:>10,} {str(pct) if pct is not None else 'n/a':>10}")

print("\n=== sku map coverage (distinct USAGE product_sku hit RECON_SKU_MAP.VENDOR_SKU or VENDOR_PRODUCT for that vendor) ===")
print(f"{'VENDOR':<15} {'USAGE_SKUS':>12} {'MATCHED':>10} {'MATCH %':>10}")
for v in VENDORS:
    c.execute(f"""
        WITH us AS (
            SELECT DISTINCT UPPER(TRIM(VENDOR_PRODUCT_SKU)) AS ps
            FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
            WHERE VENDOR = '{v}' AND VENDOR_PRODUCT_SKU IS NOT NULL
        ),
        m AS (
            SELECT DISTINCT UPPER(TRIM(VENDOR_SKU)) AS ps FROM RECON_SKU_MAP WHERE VENDOR = '{v}'
            UNION
            SELECT DISTINCT UPPER(TRIM(VENDOR_PRODUCT)) AS ps FROM RECON_SKU_MAP WHERE VENDOR = '{v}'
        )
        SELECT COUNT(*) FROM us
    """)
    total = c.fetchone()[0]
    c.execute(f"""
        WITH us AS (
            SELECT DISTINCT UPPER(TRIM(VENDOR_PRODUCT_SKU)) AS ps
            FROM THIRD_PARTY_RECON_VENDOR_USAGE_PROD
            WHERE VENDOR = '{v}' AND VENDOR_PRODUCT_SKU IS NOT NULL
        ),
        m AS (
            SELECT DISTINCT UPPER(TRIM(VENDOR_SKU)) AS ps FROM RECON_SKU_MAP WHERE VENDOR = '{v}'
            UNION
            SELECT DISTINCT UPPER(TRIM(VENDOR_PRODUCT)) AS ps FROM RECON_SKU_MAP WHERE VENDOR = '{v}'
        )
        SELECT COUNT(*) FROM us JOIN m USING(ps)
    """)
    matched = c.fetchone()[0]
    pct = round(matched * 100.0 / total, 1) if total else None
    print(f"{v:<15} {total:>12,} {matched:>10,} {str(pct) if pct is not None else 'n/a':>10}")

# TRT_PROD coverage
print("\n=== THIRD_PARTY_RECON_SOURCE_TRT_PROD contents ===")
c.execute("""
    SELECT VENDOR, COUNT(*) AS rows, COUNT(DISTINCT BILLING_MONTH) months,
           MIN(BILLING_MONTH), MAX(BILLING_MONTH)
    FROM THIRD_PARTY_RECON_SOURCE_TRT_PROD GROUP BY 1 ORDER BY 1
""")
print(f"  {'VENDOR':<15} {'ROWS':>7} {'MONTHS':>7} {'MIN':>12} {'MAX':>12}")
for r in c.fetchall():
    print(f"  {str(r[0]):<15} {r[1]:>7,} {r[2]:>7} {str(r[3]):>12} {str(r[4]):>12}")

conn.close()
