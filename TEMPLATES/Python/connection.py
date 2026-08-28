"""Reusable Snowflake helpers for local analysis scripts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from threading import Lock
from typing import Any

import pandas as pd
import snowflake.connector
from snowflake.connector import SnowflakeConnection


_CONNECTION_CACHE: dict[tuple[str, str, str, str, str, str, bool, str], SnowflakeConnection] = {}
_CONNECTION_CACHE_LOCK = Lock()


def get_repo_root() -> Path:
    """Return repository root based on this template's location."""
    return Path(__file__).resolve().parents[2]


def get_venv_python_executable(repo_root: Path | None = None) -> Path:
    """Return expected Python executable path in .venv for current OS."""
    root = repo_root or get_repo_root()
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def ensure_virtualenv_ready(*, install_missing_packages: bool = True) -> Path:
    """Validate project virtualenv and optionally install required package.

    Required package: snowflake-connector-python
    """
    venv_python = get_venv_python_executable()
    if not venv_python.exists():
        raise FileNotFoundError(
            f"Missing virtualenv interpreter at {venv_python}. "
            "Create it with: python -m venv .venv"
        )

    show_cmd = [str(venv_python), "-m", "pip", "show", "snowflake-connector-python"]
    show_result = subprocess.run(show_cmd, capture_output=True, text=True, check=False)

    if show_result.returncode != 0 and install_missing_packages:
        install_cmd = [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "snowflake-connector-python[secure-local-storage]",
            "pandas",
        ]
        subprocess.run(install_cmd, check=True)

    return venv_python


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_snowflake_connection(
    *,
    user: str | None = None,
    account: str | None = None,
    role: str | None = None,
    warehouse: str | None = None,
    database: str | None = None,
    schema: str | None = None,
    insecure_mode: bool | None = None,
    authenticator: str | None = None,
    use_cached_connection: bool | None = None,
) -> SnowflakeConnection:
    """Return a Snowflake connection with env-var overrides.

    Env vars supported:
    - SNOWFLAKE_USER
    - SNOWFLAKE_ACCOUNT
    - SNOWFLAKE_ROLE
    - SNOWFLAKE_WAREHOUSE
    - SNOWFLAKE_DATABASE
    - SNOWFLAKE_SCHEMA
    - SNOWFLAKE_INSECURE_MODE
    - SNOWFLAKE_AUTHENTICATOR
    - SNOWFLAKE_USE_CACHED_CONNECTION
    """
    resolved_user = user or os.getenv("SNOWFLAKE_USER", "nate.fold@connectwise.com")
    resolved_account = account or os.getenv("SNOWFLAKE_ACCOUNT", "connectwise_ent.us-east-1")
    resolved_role = role or os.getenv("SNOWFLAKE_ROLE", "STREAMLIT_USER")
    resolved_warehouse = warehouse or os.getenv("SNOWFLAKE_WAREHOUSE", "CORTEX_WH")
    resolved_database = database or os.getenv("SNOWFLAKE_DATABASE") or ""
    resolved_schema = schema or os.getenv("SNOWFLAKE_SCHEMA") or ""
    resolved_insecure_mode = (
        insecure_mode
        if insecure_mode is not None
        else _env_flag("SNOWFLAKE_INSECURE_MODE", False)
    )
    resolved_authenticator = authenticator or os.getenv("SNOWFLAKE_AUTHENTICATOR", "externalbrowser")
    resolved_use_cached = (
        use_cached_connection
        if use_cached_connection is not None
        else _env_flag("SNOWFLAKE_USE_CACHED_CONNECTION", True)
    )

    cache_key = (
        resolved_user,
        resolved_account,
        resolved_role,
        resolved_warehouse,
        resolved_database,
        resolved_schema,
        resolved_insecure_mode,
        resolved_authenticator,
    )

    if resolved_use_cached:
        with _CONNECTION_CACHE_LOCK:
            cached_conn = _CONNECTION_CACHE.get(cache_key)
            if cached_conn is not None and not cached_conn.is_closed():
                return cached_conn

    conn = snowflake.connector.connect(
        user=resolved_user,
        account=resolved_account,
        authenticator=resolved_authenticator,
        role=resolved_role,
        warehouse=resolved_warehouse,
        database=resolved_database or None,
        schema=resolved_schema or None,
        client_session_keep_alive=True,
        client_store_temporary_credential=True,
        insecure_mode=resolved_insecure_mode,
    )

    if resolved_use_cached:
        with _CONNECTION_CACHE_LOCK:
            _CONNECTION_CACHE[cache_key] = conn

    return conn


def close_cached_connections() -> None:
    """Close all cached Snowflake connections in this process."""
    with _CONNECTION_CACHE_LOCK:
        for conn in _CONNECTION_CACHE.values():
            if not conn.is_closed():
                conn.close()
        _CONNECTION_CACHE.clear()


def fetch_dataframe(
    query: str,
    *,
    conn: SnowflakeConnection | None = None,
    params: dict[str, Any] | None = None,
    use_cached_connection: bool | None = None,
) -> pd.DataFrame:
    """Run query and return a pandas DataFrame.

    If ``conn`` is not provided, this function reuses a cached connection by default.
    """
    active_conn = conn or get_snowflake_connection(use_cached_connection=use_cached_connection)

    with active_conn.cursor() as cursor:
        cursor.execute(query, params or {})
        return cursor.fetch_pandas_all()


def execute_sql(
    sql: str,
    *,
    conn: SnowflakeConnection | None = None,
    params: dict[str, Any] | None = None,
    use_cached_connection: bool | None = None,
) -> None:
    """Execute non-result SQL statement (DDL/DML)."""
    active_conn = conn or get_snowflake_connection(use_cached_connection=use_cached_connection)
    with active_conn.cursor() as cursor:
        cursor.execute(sql, params or {})


def print_environment_summary() -> None:
    """Print actionable runtime info for deterministic script execution."""
    venv_python = get_venv_python_executable()
    print(f"Current Python: {sys.executable}")
    print(f"Project venv Python: {venv_python}")
    print(f"SNOWFLAKE_ACCOUNT: {os.getenv('SNOWFLAKE_ACCOUNT', '<not set>')}")
    print(f"SNOWFLAKE_USER: {os.getenv('SNOWFLAKE_USER', '<not set>')}")
