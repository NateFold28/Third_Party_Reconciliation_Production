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
    cur.execute("SELECT GET_DDL('STREAMLIT', 'STREAMLIT_APPS.DBO.FPOHZEPPAB9O9KA7')")
    print(cur.fetchone()[0])


if __name__ == "__main__":
    main()
