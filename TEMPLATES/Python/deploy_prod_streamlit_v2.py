from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from TEMPLATES.Python.connection import get_snowflake_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_APP = (
    PROJECT_ROOT
    / "PROJECTS"
    / "Production_Renewal_Forecasting_Pipeline"
    / "streamlit"
    / "Production_Forecast_App_V2.py"
)
SOURCE_PYPROJECT = (
    PROJECT_ROOT
    / "PROJECTS"
    / "Production_Renewal_Forecasting_Pipeline"
    / "streamlit"
    / "pyproject.toml"
)


def main() -> None:
    if not SOURCE_APP.exists():
        raise FileNotFoundError(SOURCE_APP)
    if not SOURCE_PYPROJECT.exists():
        raise FileNotFoundError(SOURCE_PYPROJECT)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_app = Path(tmp_dir) / "streamlit_app.py"
        tmp_pyproject = Path(tmp_dir) / "pyproject.toml"
        shutil.copyfile(SOURCE_APP, tmp_app)
        shutil.copyfile(SOURCE_PYPROJECT, tmp_pyproject)

        conn = get_snowflake_connection(
            warehouse="REPORTING_WH",
            database="STREAMLIT_APPS",
            schema="DBO",
            role="STREAMLIT_USER",
        )
        cur = conn.cursor()
        cur.execute("USE DATABASE STREAMLIT_APPS")
        cur.execute("USE SCHEMA DBO")

        tmp_app_uri = tmp_app.as_posix()
        tmp_pyproject_uri = tmp_pyproject.as_posix()
        stage_name = "STREAMLIT_APPS.DBO.RENEWALS_OUTLOOK_PROD_APP_STAGE"

        # Production app: RENEWALS_OUTLOOK_PROD_APP_FALLBACK (URL cuqlkbyg4do6xj5jjpby)
        # The original FPOHZEPPAB9O9KA7 object was dropped 2026-06-25 after its backing
        # service got stuck in PENDING. The fallback object is now the canonical prod app.
        _APP = "STREAMLIT_APPS.DBO.RENEWALS_OUTLOOK_PROD_APP_FALLBACK"
        statements = [
            f"CREATE STAGE IF NOT EXISTS {stage_name} DIRECTORY = (ENABLE = TRUE)",
            f"PUT 'file://{tmp_app_uri}' @{stage_name} OVERWRITE=TRUE AUTO_COMPRESS=FALSE",
            f"PUT 'file://{tmp_pyproject_uri}' @{stage_name} OVERWRITE=TRUE AUTO_COMPRESS=FALSE",
            f"""
            CREATE OR REPLACE STREAMLIT {_APP}
              FROM @{stage_name}
              MAIN_FILE = 'streamlit_app.py'
              QUERY_WAREHOUSE = REPORTING_WH
              RUNTIME_NAME = 'SYSTEM$ST_CONTAINER_RUNTIME_PY3_11'
              COMPUTE_POOL = SYSTEM_COMPUTE_POOL_CPU
              TITLE = 'RENEWALS OUTLOOK PRODUCTION APP'
            """,
            f"ALTER STREAMLIT {_APP} ADD LIVE VERSION FROM LAST",
            # CREATE OR REPLACE resets non-ownership grants; re-apply business access.
            f"GRANT USAGE ON STREAMLIT {_APP} TO ROLE STREAMLIT",
            f"GRANT USAGE ON STREAMLIT {_APP} TO ROLE REPORTING",
            f"GRANT USAGE ON STREAMLIT {_APP} TO ROLE READ_ANALYTICS_MAIN",
            f"GRANT USAGE ON STREAMLIT {_APP} TO ROLE SALESFORCE_ANALYTICS",
            f"DESCRIBE STREAMLIT {_APP}",
        ]

        for stmt in statements:
            first_line = " ".join(stmt.strip().split())[:160]
            print(f"RUN: {first_line}")
            cur.execute(stmt)
            try:
                rows = cur.fetchall()
            except Exception:  # noqa: BLE001
                rows = []
            for row in rows:
                print(row)


if __name__ == "__main__":
    main()
