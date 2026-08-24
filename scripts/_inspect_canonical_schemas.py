"""Inspect current schemas for canonical design."""
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection  # type: ignore

conn = get_snowflake_connection(
    role="DEVELOPER", warehouse="REPORTING_WH",
    database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
)
cur = conn.cursor()

TABLES = [
    "THIRD_PARTY_RECON_OUTPUT_PROD",
    "THIRD_PARTY_RECON_DETAIL_PROD",
    "THIRD_PARTY_RECON_SUMMARY",
    "THIRD_PARTY_RECON_VENDOR_USAGE_PROD",
]

for t in TABLES:
    cur.execute(f"""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION' AND TABLE_NAME='{t}'
        ORDER BY ORDINAL_POSITION
    """)
    rows = cur.fetchall()
    print(f"\n== {t} ({len(rows)} cols) ==")
    for c, d in rows:
        print(f"  {c:<48} {d}")

# Which vendors are in DETAIL_PROD right now?
cur.execute("SELECT vendor, COUNT(*) FROM THIRD_PARTY_RECON_DETAIL_PROD GROUP BY 1 ORDER BY 2 DESC")
print("\n== DETAIL_PROD vendors ==")
for v, n in cur.fetchall():
    print(f"  {v:<20} {n:,}")

# What are the current OUTCOME_FLAG values across vendors?
cur.execute("SELECT vendor, outcome_flag, COUNT(*) FROM THIRD_PARTY_RECON_DETAIL_PROD GROUP BY 1,2 ORDER BY 1, 3 DESC")
print("\n== DETAIL_PROD outcome_flag by vendor ==")
last_v = None
for v, of, n in cur.fetchall():
    if v != last_v:
        print(f"  --- {v} ---")
        last_v = v
    print(f"    {str(of):<70} {n:>8,}")

conn.close()
