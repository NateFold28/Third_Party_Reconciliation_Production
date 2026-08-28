"""
React_App_Demo — FastAPI Backend
Serves renewal ATR/Actuals data from Snowflake to the React frontend.

Auth strategy:
  - SPCS (production): Uses the OAuth token Snowflake injects at
    /snowflake/session/token inside every SPCS container. No secrets or
    service account passwords required. The token is scoped to the role
    that owns the service.
  - Local dev: Uses externalbrowser SSO. First API call opens a browser
    tab to authenticate. Credentials are cached in ~/.snowflake/
"""

import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import snowflake.connector
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

SNOWFLAKE_ACCOUNT   = os.environ.get("SNOWFLAKE_ACCOUNT", "")
SNOWFLAKE_USER      = os.environ.get("SNOWFLAKE_USER", "")
SNOWFLAKE_WAREHOUSE = os.environ.get("SNOWFLAKE_WAREHOUSE", "CORTEX_WH")
SNOWFLAKE_DATABASE  = os.environ.get("SNOWFLAKE_DATABASE", "STREAMLIT_APPS")
SNOWFLAKE_SCHEMA    = os.environ.get("SNOWFLAKE_SCHEMA", "DBO")
SNOWFLAKE_ROLE      = os.environ.get("SNOWFLAKE_ROLE", "REACT_APP_ROLE")

# Path where SPCS injects the pre-authenticated OAuth token
_SPCS_TOKEN_PATH = Path("/snowflake/session/token")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def get_connection() -> snowflake.connector.SnowflakeConnection:
    """Return a new Snowflake connection. Caller is responsible for closing it.

    Inside SPCS: uses the injected OAuth token — no password or secrets needed.
    Local dev:   uses externalbrowser SSO.
    """
    if _SPCS_TOKEN_PATH.exists():
        # Running inside SPCS — use the pre-loaded OAuth token.
        # The token is scoped to the role that owns the service (DEVELOPER).
        token = _SPCS_TOKEN_PATH.read_text(encoding="ascii").strip()
        return snowflake.connector.connect(
            account=SNOWFLAKE_ACCOUNT,
            host=os.environ.get("SNOWFLAKE_HOST", f"{SNOWFLAKE_ACCOUNT}.snowflakecomputing.com"),
            authenticator="oauth",
            token=token,
            warehouse=SNOWFLAKE_WAREHOUSE,
            database=SNOWFLAKE_DATABASE,
            schema=SNOWFLAKE_SCHEMA,
            role=SNOWFLAKE_ROLE,
            session_parameters={"QUERY_TAG": "react_v5_forecast_app"},
        )
    else:
        # Local dev — interactive SSO via browser.
        return snowflake.connector.connect(
            account=SNOWFLAKE_ACCOUNT,
            user=SNOWFLAKE_USER,
            authenticator="externalbrowser",
            warehouse=SNOWFLAKE_WAREHOUSE,
            database=SNOWFLAKE_DATABASE,
            schema=SNOWFLAKE_SCHEMA,
            role=SNOWFLAKE_ROLE,
            session_parameters={"QUERY_TAG": "react_v5_forecast_app"},
        )


def run_query(sql: str, params: dict) -> list[dict[str, Any]]:
    """Execute parameterised SQL and return rows as a list of dicts."""
    conn = get_connection()
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
        # Snowflake DictCursor returns uppercase keys — normalise to lower
        return [{k.lower(): v for k, v in row.items()} for row in rows]
    finally:
        conn.close()


def load_sql(filename: str) -> str:
    # SQL files are copied to /app/sql/ inside the container (see Dockerfile)
    sql_dir = Path(__file__).parent / "sql"
    return (sql_dir / filename).read_text()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate connection on startup so SPCS health-check can fail fast
    if SNOWFLAKE_ACCOUNT:
        try:
            conn = get_connection()
            conn.close()
        except Exception as exc:
            raise RuntimeError(f"Snowflake connection failed at startup: {exc}") from exc
    yield


app = FastAPI(title="React App Demo API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Tighten to specific origin in production
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
DEFAULT_START = "2021-02-01"
DEFAULT_END   = "2026-12-31"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/open-renewals")
def open_renewals(
    start_date: str = Query(default=DEFAULT_START, description="YYYY-MM-DD"),
    end_date:   str = Query(default=DEFAULT_END,   description="YYYY-MM-DD"),
):
    try:
        sql = load_sql("open_renewals.sql")
        rows = run_query(sql, {"start_date": start_date, "end_date": end_date})
        return {"data": rows}
    except Exception as exc:
        logger.exception("open_renewals failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/all-renewals")
def all_renewals(
    start_date: str = Query(default=DEFAULT_START),
    end_date:   str = Query(default=DEFAULT_END),
):
    try:
        sql = load_sql("all_renewals.sql")
        rows = run_query(sql, {"start_date": start_date, "end_date": end_date})
        return {"data": rows}
    except Exception as exc:
        logger.exception("all_renewals failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/model-performance")
def model_performance(
    start_date: str = Query(default=DEFAULT_START),
    end_date:   str = Query(default=DEFAULT_END),
):
    try:
        sql = load_sql("model_performance.sql")
        rows = run_query(sql, {"start_date": start_date, "end_date": end_date})
        return {"data": rows}
    except Exception as exc:
        logger.exception("model_performance failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/monthly-summary")
def monthly_summary_alias(
    start_date: str = Query(default=DEFAULT_START),
    end_date:   str = Query(default=DEFAULT_END),
):
    return open_renewals(start_date, end_date)


@app.get("/api/segment-rollup")
def segment_rollup(
    start_date: str = Query(default=DEFAULT_START),
    end_date:   str = Query(default=DEFAULT_END),
):
    try:
        sql = load_sql("segment_rollup.sql")
        rows = run_query(sql, {"start_date": start_date, "end_date": end_date})
        return {"data": rows}
    except Exception as exc:
        logger.exception("segment_rollup failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/portfolio-rollup")
def portfolio_rollup(
    start_date: str = Query(default=DEFAULT_START),
    end_date:   str = Query(default=DEFAULT_END),
):
    try:
        sql = load_sql("portfolio_rollup.sql")
        rows = run_query(sql, {"start_date": start_date, "end_date": end_date})
        return {"data": rows}
    except Exception as exc:
        logger.exception("portfolio_rollup failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/model-runs")
def model_runs():
    try:
        sql = load_sql("model_runs.sql")
        rows = run_query(sql, {})
        return {"data": rows}
    except Exception as exc:
        logger.exception("model_runs failed")
        raise HTTPException(status_code=500, detail=str(exc))
