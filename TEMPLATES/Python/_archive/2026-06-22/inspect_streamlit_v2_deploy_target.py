from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from TEMPLATES.Python.connection import get_snowflake_connection


def main() -> None:
    conn = get_snowflake_connection(
        warehouse="REPORTING_WH",
        database="STREAMLIT_APPS",
        schema="DBO",
        role="STREAMLIT_USER",
    )
    cur = conn.cursor()
    cur.execute("USE DATABASE STREAMLIT_APPS")
    cur.execute("USE SCHEMA DBO")

    print("STREAMLITS")
    cur.execute("SHOW STREAMLITS IN SCHEMA STREAMLIT_APPS.DBO")
    for row in cur.fetchall():
        print(row)

    print("\nSTAGES_MATCHING")
    cur.execute("SHOW STAGES IN SCHEMA STREAMLIT_APPS.DBO")
    for row in cur.fetchall():
        as_text = " | ".join("" if value is None else str(value) for value in row)
        if "LO8PU71ZBTTI6DX9" in as_text or "FORECAST" in as_text.upper() or "STREAMLIT" in as_text.upper():
            print(as_text)

    print("\nPROD_STREAMLIT_DESC")
    cur.execute('DESCRIBE STREAMLIT STREAMLIT_APPS.DBO."FPOHZEPPAB9O9KA7"')
    for row in cur.fetchall():
        print(row)

    print("\nPROD_STAGE_LIST")
    try:
        cur.execute('LIST @STREAMLIT_APPS.DBO."FPOHZEPPAB9O9KA7"')
        for row in cur.fetchall():
            print(row)
    except Exception as exc:  # noqa: BLE001
        print(f"stage_list_unavailable: {exc}")


if __name__ == "__main__":
    main()
