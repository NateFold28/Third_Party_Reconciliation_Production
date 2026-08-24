"""Inspect Zuora/Marketplace source tables to understand what's available
for building Exium billing compat views."""

import sys
sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection(
    role="DEVELOPER",
    warehouse="REPORTING_WH",
    database="ANALYTICS_DEV",
    schema="DBT_NFOLD_TRANSFORMATION",
)
c = conn.cursor()

for tbl in [
    "THIRD_PARTY_RECON_SOURCE_ZUORA_PROD",
    "THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD",
    "ZUORA_THIRD_PARTY_RECON_BASE",
    "EXIUM_USAGE",
]:
    print(f"\n=== {tbl} ===")
    c.execute(f"""SELECT COLUMN_NAME, DATA_TYPE
                    FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION'
                     AND TABLE_NAME='{tbl}'
                   ORDER BY ORDINAL_POSITION""")
    cols = c.fetchall()
    if not cols:
        print("  (not found)")
        continue
    for n, d in cols:
        print(f"  {n:40s} {d}")
    col_names = [x[0] for x in cols]
    if "VENDOR" in col_names:
        c.execute(f"SELECT COUNT(*) FROM {tbl} WHERE VENDOR = 'Exium'")
        print(f"  ROW_COUNT (Exium): {c.fetchone()[0]:,}")
    elif "VENDOR_NAME" in col_names:
        c.execute(f"SELECT COUNT(*) FROM {tbl} WHERE VENDOR_NAME = 'Exium'")
        print(f"  ROW_COUNT (Exium via VENDOR_NAME): {c.fetchone()[0]:,}")
    else:
        c.execute(f"SELECT COUNT(*) FROM {tbl}")
        print(f"  ROW_COUNT (all): {c.fetchone()[0]:,}")
