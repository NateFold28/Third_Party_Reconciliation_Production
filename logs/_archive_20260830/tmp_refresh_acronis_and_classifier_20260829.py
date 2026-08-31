import sys
import subprocess
from pathlib import Path

sys.path.insert(0, r"c:/Users/Nate.Fold/projects")

from TEMPLATES.Python.connection import get_snowflake_connection
from PROJECTS.Third_Party_Reconciliation.Combined_Recon_Prod_Pipeline.Reconciliation._run_skeleton_pipeline import run_sql, live_emit_block

REPO = Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline")

conn = get_snowflake_connection(role="DEVELOPER", warehouse="REPORTING_WH", database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION")
try:
    ok = run_sql(
        conn,
        live_emit_block("Acronis", "ACRONIS_RECON_DETAIL", target_table="THIRD_PARTY_RECON_DETAIL_PROD"),
        "refresh Acronis rows in DETAIL_PROD from ACRONIS_RECON_DETAIL"
    )
    if not ok:
        raise RuntimeError("Acronis emit failed")
finally:
    conn.close()

result = subprocess.run(
    [sys.executable, str(REPO / "Reconciliation" / "build_third_party_recon_output_prod.py")],
    capture_output=True,
    text=True,
    cwd=str(REPO / "Reconciliation"),
)
print(result.stdout)
if result.returncode != 0:
    print(result.stderr)
    raise SystemExit(result.returncode)
