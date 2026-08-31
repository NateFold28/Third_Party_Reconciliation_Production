from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from TEMPLATES.Python.connection import get_snowflake_connection

ROOT = Path(r"c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline")
MAP_SQL = ROOT / "Maps" / "sql" / "02_unified_reference_maps.sql"

PARTNER_UPSERT_ROWS = [
    ("ELEVITYIT", "ACT-00238028", "Elevity IT"),
    ("ELEVITY IT", "ACT-00238028", "Elevity IT"),
    ("NUMSP", "ACT-00245551", "NuMSP"),
    ("SFY", "ACT-00035427", "Sfy It"),
    ("SFY IT", "ACT-00035427", "Sfy It"),
    ("EXECUTECH", "ACT-00246790", "Executech"),
    ("KMICRO", "ACT-00246783", "KMicro"),
    ("GFLEX", "ACT-00245462", "Gflex"),
    ("ACCESS GROUP INC", "ACT-00200001", "Access Group Inc"),
]


def run() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        values_sql = ",\n                ".join(
            f"('{pn.replace("'", "''")}', '{sfid}', '{zn.replace("'", "''")}')"
            for pn, sfid, zn in PARTNER_UPSERT_ROWS
        )
        upsert_sql = f"""
            MERGE INTO ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_PARTNER_MAP_PROD t
            USING (
                SELECT * FROM VALUES
                {values_sql}
                v(partner_name, sf_id, zuora_name)
            ) s
            ON UPPER(TRIM(t.PARTNER_NAME)) = UPPER(TRIM(s.partner_name))
            WHEN MATCHED THEN UPDATE SET
                t.SF_ID = s.sf_id,
                t.ZUORA_NAME = COALESCE(NULLIF(t.ZUORA_NAME, ''), s.zuora_name)
            WHEN NOT MATCHED THEN INSERT (PARTNER_NAME, PARENT_COMPANY, SF_ID, CMS_ID, ZUORA_NAME)
            VALUES (s.partner_name, NULL, s.sf_id, NULL, s.zuora_name)
        """
        with conn.cursor() as cur:
            cur.execute(upsert_sql)
        conn.commit()
        print("Partner map upsert complete")

        sql_text = MAP_SQL.read_text(encoding="utf-8")
        filtered_sql = "\n".join(
            line for line in sql_text.splitlines()
            if not line.strip().startswith("--")
        )
        for cur in conn.execute_string(filtered_sql, return_cursors=True):
            try:
                cur.fetchall()
            except Exception:
                pass
        conn.commit()
        print("02_unified_reference_maps.sql rebuild complete")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
