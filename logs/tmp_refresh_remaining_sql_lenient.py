from pathlib import Path
from snowflake.connector.errors import ProgrammingError
from TEMPLATES.Python.connection import get_snowflake_connection

repo = Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline")
steps = [
    (r"Maps/sql/02_unified_reference_maps.sql", "build unified reference maps"),
    (r"Maps/sql/03_compat_dead_object_views.sql", "build compat/dead object views"),
    (r"Maps/sql/00b_backfill_invoice_prices.sql", "backfill invoice prices into usage"),
    (r"Reconciliation/10_vendor_invoice_usage_intra_prod.sql", "rebuild vendor invoice usage intra"),
]

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
cur = conn.cursor()
try:
    for rel, label in steps:
        p = repo / rel
        sql = p.read_text(encoding='utf-8')
        stmts = [s for s in sql.split(';')]
        print(f"\\n=== {label} ({rel}) ===")
        executed = 0
        skipped_empty = 0
        for stmt in stmts:
            q = stmt.strip()
            if not q:
                skipped_empty += 1
                continue
            try:
                cur.execute(q)
                executed += 1
            except ProgrammingError as e:
                if 'Empty SQL statement' in str(e):
                    skipped_empty += 1
                    continue
                raise
        print(f"  executed {executed} statements, skipped_empty={skipped_empty}")
    conn.commit()
    print("\\nRemaining SQL steps completed.")
finally:
    cur.close()
    conn.close()
