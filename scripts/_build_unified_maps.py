"""Build unified reference maps + export seed CSVs.

Runs sql/02_unified_reference_maps.sql then dumps the two unified tables to
seeds/RECON_PARTNER_MAP.csv and seeds/RECON_SKU_MAP.csv for engineering.
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline")
sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        sql_path = REPO / "sql" / "02_unified_reference_maps.sql"
        print(f"Executing {sql_path.name} ...")
        for cur in conn.execute_string(sql_path.read_text(encoding="utf-8"), return_cursors=True):
            try:
                cur.fetchall()
            except Exception:
                pass
        conn.commit()
        print("  OK")

        c = conn.cursor()
        print("\n--- RECON_PARTNER_MAP ---")
        c.execute("SELECT COUNT(*) FROM RECON_PARTNER_MAP")
        print(f"  total distinct rows: {c.fetchone()[0]:,}")
        c.execute("SELECT VENDOR, COUNT(*) FROM RECON_PARTNER_MAP GROUP BY 1 ORDER BY 1")
        for v, n in c.fetchall():
            print(f"    {v:<15} {n:>6,}")
        c.execute("""
            SELECT COUNT(DISTINCT COALESCE(SF_ID,'')||'|'||COALESCE(ZUORA_NAME,'')||'|'||COALESCE(PARTNER_NAME,''))
            FROM RECON_PARTNER_MAP
        """)
        print(f"  unique (SF_ID, ZUORA_NAME, PARTNER_NAME) triples: {c.fetchone()[0]:,}")

        print("\n--- RECON_SKU_MAP ---")
        c.execute("SELECT COUNT(*) FROM RECON_SKU_MAP")
        print(f"  total distinct rows: {c.fetchone()[0]:,}")
        c.execute("SELECT VENDOR, COUNT(*) FROM RECON_SKU_MAP GROUP BY 1 ORDER BY 1")
        for v, n in c.fetchall():
            print(f"    {v:<15} {n:>6,}")

        # Export CSVs
        seeds = REPO / "seeds"
        seeds.mkdir(exist_ok=True)
        for tbl in ("RECON_PARTNER_MAP", "RECON_SKU_MAP"):
            csv = seeds / f"{tbl}.csv"
            c.execute(f"SELECT * FROM {tbl} ORDER BY VENDOR")
            cols = [d[0] for d in c.description]
            rows = c.fetchall()
            with csv.open("w", encoding="utf-8", newline="") as f:
                import csv as csvmod
                w = csvmod.writer(f)
                w.writerow(cols)
                for r in rows:
                    w.writerow(["" if x is None else str(x) for x in r])
            print(f"  wrote {csv.relative_to(REPO)}: {len(rows):,} rows")

        # Sanity: backward-compat views resolve
        print("\n--- backward-compat view sanity ---")
        for v in ("ACRONIS","AUVIK","BITDEFENDER","ESET","EXIUM","KEEPIT",
                  "PROOFPOINT","SENTINELONE","WEBROOT"):
            for kind in ("PARTNER_MAPPING_V5","SKU_MAP_V5"):
                obj = f"{v}_{kind}"
                try:
                    c.execute(f"SELECT COUNT(*) FROM {obj}")
                    print(f"  {obj:<40} {c.fetchone()[0]:>6,} rows")
                except Exception as e:
                    print(f"  {obj:<40} ERROR: {e}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
