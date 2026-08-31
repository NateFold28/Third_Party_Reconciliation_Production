from pathlib import Path
from TEMPLATES.Python.connection import get_snowflake_connection

repo = Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline")
steps = [
    (r"Maps/sql/01_unified_billing_sources.sql", "build unified billing sources"),
    (r"Maps/sql/02_unified_reference_maps.sql", "build unified reference maps"),
    (r"Maps/sql/03_compat_dead_object_views.sql", "build compat/dead object views"),
    (r"Maps/sql/00b_backfill_invoice_prices.sql", "backfill invoice prices into usage"),
    (r"Reconciliation/10_vendor_invoice_usage_intra_prod.sql", "rebuild vendor invoice usage intra"),
]

conn = get_snowflake_connection(role='DEVELOPER', warehouse='REPORTING_WH', database='ANALYTICS_DEV', schema='DBT_NFOLD_TRANSFORMATION')
try:
    for rel, label in steps:
        p = repo / rel
        sql = p.read_text(encoding='utf-8')
        print(f"\\n=== {label} ({rel}) ===")
        cursors = conn.execute_string(sql, return_cursors=True)
        n = 0
        for c in cursors:
            n += 1
            try:
                c.fetchall()
            except Exception:
                pass
        print(f"  executed {n} statement(s)")
    conn.commit()
    print("\\nAll SQL map/intra steps completed.")
finally:
    conn.close()
