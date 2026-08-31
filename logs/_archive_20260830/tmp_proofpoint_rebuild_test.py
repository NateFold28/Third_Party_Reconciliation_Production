"""Dry-parse the updated Proofpoint script against Snowflake.

Runs each statement in the file up to the point of the first CREATE TABLE, but
in a mode that only validates SQL — using EXPLAIN. Simpler: just run all the
statements in a transaction and immediately rollback (Snowflake DDL is
auto-committed, so use a scratch schema? no — easiest: run the whole thing).

Since this script is already what the pipeline runs, and the pipeline just
executed cleanly last session, safest sanity check is to run just this script
and let it CREATE OR REPLACE PROOFPOINT_RECON_DETAIL then check the columns
API_QUANTITY / AVG_API_QUANTITY exist and are populated.
"""
import sys
from pathlib import Path

sys.path.insert(0, r'c:/Users/Nate.Fold/projects')
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

SCRIPT = Path(r'c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/'
              r'Combined_Recon_Prod_Pipeline/Reconciliation/'
              r'Proofpoint_Reconciliation_Script_Prod.sql')

sql = SCRIPT.read_text(encoding='utf-8')

# Rebuild RECON_SKU_MAP first so TRT_MATCH_KEY column is present in the view.
SKU_MAP_SQL = Path(r'c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/'
                   r'Combined_Recon_Prod_Pipeline/Maps/sql/02_unified_reference_maps.sql').read_text(encoding='utf-8')

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH',
                                database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()


def run_statements(text, tag):
    """Statement runner that respects string literals via split_statements."""
    from snowflake.connector.util_text import split_statements
    from io import StringIO

    print(f"[{tag}] executing ...")
    with conn.cursor() as c:
        i = 0
        for stmt, _is_put_or_get in split_statements(StringIO(text), remove_comments=True):
            s = stmt.strip()
            if not s or s == ';':
                continue
            i += 1
            try:
                c.execute(s)
            except Exception as e:
                print(f"  FAIL stmt #{i}: {str(e)[:200]}")
                print(f"  stmt (first 400 chars): {s[:400]}")
                raise
    conn.commit()
    print(f"[{tag}] ok — {i} statements executed")


run_statements(SKU_MAP_SQL, 'RECON_SKU_MAP builder')
print()

# Quick check TRT_MATCH_KEY exists on RECON_SKU_MAP
print('=== RECON_SKU_MAP.Proofpoint sample (with TRT_MATCH_KEY) ===')
print(fetch_dataframe(
    """SELECT vendor, vendor_product, cw_sku, trt_match_key, sku_match_key
       FROM RECON_SKU_MAP WHERE vendor='Proofpoint' LIMIT 5""",
    conn=conn).to_string(index=False))
print()

run_statements(sql, 'Proofpoint_Reconciliation_Script_Prod')
print()

# Verify API columns
print('=== PROOFPOINT_RECON_DETAIL columns ===')
print(fetch_dataframe(
    """SELECT column_name FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
       WHERE table_schema='DBT_NFOLD_TRANSFORMATION' AND table_name='PROOFPOINT_RECON_DETAIL'
       ORDER BY ordinal_position""",
    conn=conn).to_string(index=False))
print()

print('=== PROOFPOINT_RECON_DETAIL: API_QUANTITY coverage ===')
print(fetch_dataframe(
    """SELECT billing_month,
              COUNT(*) AS rows_n,
              COUNT(api_quantity) AS api_non_null,
              SUM(IFF(api_quantity > 0, 1, 0)) AS api_gt_zero,
              ROUND(AVG(api_quantity), 2) AS avg_api,
              ROUND(AVG(vendor_quantity), 2) AS avg_vendor,
              ROUND(AVG(api_quantity) / NULLIF(AVG(vendor_quantity), 0), 3) AS avg_ratio
       FROM PROOFPOINT_RECON_DETAIL
       GROUP BY billing_month ORDER BY billing_month""",
    conn=conn).to_string(index=False))

conn.close()
