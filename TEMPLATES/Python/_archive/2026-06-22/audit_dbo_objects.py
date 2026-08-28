"""Audit STREAMLIT_APPS.DBO — classify every object as KEEP (prod pipeline) or DROP candidate."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur  = conn.cursor()

# ── PROD pipeline prefixes / names (V5 or pipeline-related) ──────────────────
KEEP_PREFIXES = (
    "V5_",
    "ML_SANDBOX_V5",
    "ML_MODEL_REGISTRY",
    "SP_V5_",
    "SP_RENEWALS_",
    "V5_PIPELINE_",
    "CARR__RENEWALS_",   # source views if any live here
)
# Exact names that are V5-prod regardless of prefix
KEEP_EXACT = {
    "V5_APP_USAGE_LOG",
    "ML_MODEL_REGISTRY",
}

def classify(name: str) -> str:
    n = name.upper()
    if n in KEEP_EXACT:
        return "KEEP"
    for p in KEEP_PREFIXES:
        if n.startswith(p):
            return "KEEP"
    return "DROP ?"

sections = {
    "TABLES":     "SELECT TABLE_NAME AS OBJ_NAME, TABLE_TYPE FROM STREAMLIT_APPS.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'DBO' ORDER BY TABLE_NAME",
    "VIEWS":      "SELECT TABLE_NAME AS OBJ_NAME FROM STREAMLIT_APPS.INFORMATION_SCHEMA.VIEWS WHERE TABLE_SCHEMA = 'DBO' ORDER BY TABLE_NAME",
    "PROCEDURES": "SHOW PROCEDURES IN STREAMLIT_APPS.DBO",
    "TASKS":      "SHOW TASKS IN STREAMLIT_APPS.DBO",
    "STAGES":     "SHOW STAGES IN STREAMLIT_APPS.DBO",
}

results = {}

for section, sql in sections.items():
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    # Name column varies by object type
    name_col_idx = 0
    for i, c in enumerate(cols):
        if c.lower() in ("name", "obj_name", "table_name"):
            name_col_idx = i
            break
    results[section] = [(r[name_col_idx], r, cols) for r in rows]

# Also check for Snowflake ML model registries
try:
    cur.execute("SHOW MODEL REGISTRY IN STREAMLIT_APPS.DBO")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    results["MODEL_REGISTRY"] = [(r[0], r, cols) for r in rows]
except Exception:
    results["MODEL_REGISTRY"] = []

try:
    cur.execute("SHOW MODELS IN STREAMLIT_APPS.DBO")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    results["MODELS"] = [(r[0], r, cols) for r in rows]
except Exception:
    results["MODELS"] = []

print()
print("=" * 80)
print("  STREAMLIT_APPS.DBO OBJECT AUDIT")
print("=" * 80)

drop_list = []
keep_list = []

for section, items in results.items():
    if not items:
        print(f"\n  [{section}] — none\n")
        continue
    print(f"\n  [{section}]  ({len(items)} objects)")
    print(f"  {'STATUS':<10}  {'NAME'}")
    print("  " + "-" * 60)
    for (name, row, cols) in items:
        verdict = classify(name)
        print(f"  {verdict:<10}  {name}")
        if verdict.startswith("DROP"):
            drop_list.append((section, name))
        else:
            keep_list.append((section, name))

print()
print("=" * 80)
print(f"  KEEP: {len(keep_list)}   |   DROP candidates: {len(drop_list)}")
print("=" * 80)
print()
print("  DROP candidates:")
for section, name in sorted(drop_list):
    print(f"    {section:<15}  {name}")
print()
conn.close()
