"""Rebuild RECON_SKU_MAP from THIRD_PARTY_RECON_SKU_MAP_PROD."""
import sys, time
from pathlib import Path
sys.path.insert(0, r"c:/Users/Nate.Fold/projects")
from TEMPLATES.Python.connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection(role='DEVELOPER',warehouse='REPORTING_WH',database='ANALYTICS_DEV',schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()

text = Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Maps/sql/02_unified_reference_maps.sql").read_text(encoding='utf-8')

# Split on top-level statement boundaries: semicolons followed by newline
stmts = []
cur_stmt = []
in_str = False
for line in text.splitlines():
    cur_stmt.append(line)
    stripped = line.rstrip()
    # naive: semicolon at end of a non-comment line ends stmt
    if stripped.endswith(';') and not stripped.lstrip().startswith('--'):
        stmts.append('\n'.join(cur_stmt))
        cur_stmt = []
if cur_stmt:
    tail = '\n'.join(cur_stmt).strip()
    if tail:
        stmts.append(tail)

t0 = time.time()
for i, s in enumerate(stmts):
    body = s.strip()
    if not body: continue
    # Skip comment-only or whitespace-only statements
    non_comment = [
        line for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith('--')
    ]
    if not non_comment:
        continue
    head = body.split('\n', 1)[0][:70]
    s0 = time.time()
    try:
        cur.execute(body)
        print(f"[{i:>3}] {time.time()-s0:5.1f}s  {head}")
    except Exception as e:
        print(f"[{i:>3}] FAILED: {head}")
        print(f"      {e}")
        raise
print(f"\nTotal: {time.time()-t0:.1f}s")

print()
print(fetch_dataframe(
    """SELECT vendor, COUNT(*) row_ct,
              COUNT_IF(trt_match_key IS NOT NULL AND TRIM(trt_match_key) <> '') trt_keys
       FROM RECON_SKU_MAP
       WHERE vendor IN ('SentinelOne','Exium')
       GROUP BY 1 ORDER BY 1""",
    conn=conn
).to_string(index=False))
