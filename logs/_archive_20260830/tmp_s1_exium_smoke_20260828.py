"""
Standalone smoke test: rebuild S1 + Exium recon detail with new direct-TRT
API blocks and confirm API_QUANTITY populates.
"""
import sys, time
from pathlib import Path
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(
    role="DEVELOPER",
    warehouse="REPORTING_WH",
    database="ANALYTICS_DEV",
    schema="DBT_NFOLD_TRANSFORMATION",
)
cur = conn.cursor()

def split_sql(text):
    """Split by ';' at statement boundaries (naive, safe for these files)."""
    out = []
    cur_stmt = []
    for line in text.splitlines():
        cur_stmt.append(line)
        stripped = line.strip()
        if stripped.endswith(';') and not stripped.startswith('--'):
            joined = '\n'.join(cur_stmt).strip()
            if joined:
                out.append(joined)
            cur_stmt = []
    tail = '\n'.join(cur_stmt).strip()
    if tail:
        out.append(tail)
    return [s for s in out if s and not s.lstrip().startswith('--') or 'CREATE' in s.upper() or 'USE' in s.upper() or 'DELETE' in s.upper() or 'INSERT' in s.upper()]

def run_file(path, label):
    print(f"\n=== {label} ({path}) ===")
    text = Path(path).read_text(encoding='utf-8')
    stmts = split_sql(text)
    t0 = time.time()
    for i, stmt in enumerate(stmts):
        if not stmt.strip(): continue
        head = stmt.strip().split('\n', 1)[0][:80]
        s0 = time.time()
        try:
            cur.execute(stmt)
        except Exception as e:
            print(f"  [stmt {i}] FAILED after {time.time()-s0:.1f}s: {head}")
            print(f"    ERROR: {e}")
            raise
        print(f"  [stmt {i}] {time.time()-s0:.1f}s: {head}")
    print(f"  TOTAL: {time.time()-t0:.1f}s")

run_file(
    r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/SentinelOne_Reconciliation_Script_Prod.sql",
    "SentinelOne recon"
)

run_file(
    r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Reconciliation/Exium_Reconciliation_Script_Prod.sql",
    "Exium recon"
)

print("\n--- SentinelOne API population ---")
q = """
SELECT
  COUNT(*) AS row_ct,
  COUNT(api_quantity) AS api_qty_populated,
  COUNT(avg_api_quantity) AS avg_api_populated,
  ROUND(AVG(api_quantity), 1) AS avg_api_val,
  ROUND(SUM(api_quantity), 0) AS sum_api_qty
FROM SENTINELONE_RECON_DETAIL
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n--- Exium API population ---")
q = """
SELECT
  COUNT(*) AS row_ct,
  COUNT(api_quantity) AS api_qty_populated,
  COUNT(avg_api_quantity) AS avg_api_populated,
  ROUND(AVG(api_quantity), 1) AS avg_api_val,
  ROUND(SUM(api_quantity), 0) AS sum_api_qty
FROM EXIUM_RECON_DETAIL
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n--- SentinelOne outcome_flag distribution (last 3 months) ---")
q = """
SELECT
  outcome_flag,
  COUNT(*) AS row_ct
FROM SENTINELONE_RECON_DETAIL
WHERE billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
GROUP BY 1 ORDER BY 2 DESC
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

print("\n--- Exium outcome_flag distribution (last 3 months) ---")
q = """
SELECT
  outcome_flag,
  COUNT(*) AS row_ct
FROM EXIUM_RECON_DETAIL
WHERE billing_month >= DATEADD(month, -3, DATE_TRUNC('month', CURRENT_DATE))
GROUP BY 1 ORDER BY 2 DESC
"""
print(fetch_dataframe(q, conn=conn).to_string(index=False))

conn.close()
print("\nDONE")
