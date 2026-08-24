"""Find every table/view referenced in the 9 vendor SQLs vs what actually
exists in Snowflake, so we know exactly which vendors can run vs. which
are wired to dead objects.
"""
import re
import sys
from pathlib import Path
sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection

REPO = Path(r"C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline")
VENDORS = ["Acronis","Auvik","Bitdefender","ESET","Exium","KeepIT","Proofpoint","SentinelOne","Webroot"]

# Anything of the form FROM/JOIN <ident> or <schema>.<ident>[.<ident>] captured
REF_RE = re.compile(r"\b(?:FROM|JOIN)\s+((?:[A-Z_][A-Z0-9_]*\.){0,2}[A-Z_][A-Z0-9_]*)", re.IGNORECASE)

# CTE names in the same file are not "real" tables, exclude them
CTE_RE = re.compile(r"([A-Z_][A-Z0-9_]*)\s+AS\s*\(", re.IGNORECASE)

conn = get_snowflake_connection(
    role="DEVELOPER", warehouse="REPORTING_WH",
    database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
)
c = conn.cursor()

# Cache: exists() for a table/view name in DBT_NFOLD_TRANSFORMATION
def exists(name: str) -> str:
    n = name.upper()
    # skip fully qualified refs; those work if the target exists
    parts = n.split(".")
    if len(parts) == 3:
        db, sch, tbl = parts
    elif len(parts) == 2:
        db, sch, tbl = "ANALYTICS_DEV", parts[0], parts[1]
    else:
        db, sch, tbl = "ANALYTICS_DEV", "DBT_NFOLD_TRANSFORMATION", parts[0]
    c.execute(f"""
        SELECT TABLE_TYPE FROM {db}.INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = '{sch}' AND TABLE_NAME = '{tbl}'
        UNION ALL
        SELECT 'VIEW' FROM {db}.INFORMATION_SCHEMA.VIEWS
        WHERE TABLE_SCHEMA = '{sch}' AND TABLE_NAME = '{tbl}'
    """)
    r = c.fetchone()
    return r[0] if r else "MISSING"

for v in VENDORS:
    p = REPO / "Vendor_Recon_Pipelines_Prod" / v / f"{v}_Reconciliation_Script_Prod.sql"
    if not p.exists():
        print(f"\n== {v}: SQL file missing"); continue
    sql = p.read_text(encoding="utf-8")
    ctes = {m.group(1).upper() for m in CTE_RE.finditer(sql)}
    refs = {m.group(1).upper() for m in REF_RE.finditer(sql)}
    # exclude CTE names and obvious keywords
    real = sorted(refs - ctes - {"SELECT","WHERE","AND","OR","AS","ON"})
    print(f"\n== {v} ({len(real)} refs) ==")
    missing = []
    for r in real:
        s = exists(r)
        marker = " " if s in ("BASE TABLE","VIEW") else "*"
        print(f"  {marker} {s:<12} {r}")
        if s == "MISSING":
            missing.append(r)
    if missing:
        print(f"  MISSING for {v}: {missing}")

conn.close()
