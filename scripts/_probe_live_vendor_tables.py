"""Quick probe: do live vendor tables exist and what columns do they carry?"""
import sys
sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection(
    role="DEVELOPER", warehouse="REPORTING_WH",
    database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
)
cur = conn.cursor()

for v in ("PROOFPOINT", "BITDEFENDER", "ACRONIS"):
    tbl = f"{v}_RECON_DETAIL"
    cur.execute(f"""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'DBT_NFOLD_TRANSFORMATION'
          AND TABLE_NAME = '{tbl}'
        ORDER BY ORDINAL_POSITION
    """)
    cols = cur.fetchall()
    if not cols:
        print(f"\n== {tbl}: DOES NOT EXIST")
        continue
    try:
        cur.execute(f"SELECT COUNT(*), MIN(BILLING_MONTH), MAX(BILLING_MONTH) FROM {tbl}")
        n, dmin, dmax = cur.fetchone()
    except Exception as e:
        n, dmin, dmax = f"?({e})", None, None
    print(f"\n== {tbl}: {len(cols)} cols, {n} rows, {dmin} to {dmax}")
    for name, dtype in cols:
        print(f"    {name:<35} {dtype}")

conn.close()
