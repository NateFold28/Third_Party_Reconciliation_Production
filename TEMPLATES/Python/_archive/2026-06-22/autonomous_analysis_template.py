"""Minimal chat-driven Snowflake iteration template.

Use this as the base loop when Copilot generates SQL/code from a prompt:
1) run SQL -> DataFrame
2) validate expected output
3) patch SQL/code and rerun until clean
"""

from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from connection import fetch_dataframe


@dataclass
class AnalysisRequest:
    name: str
    sql: str
    max_rows: int = 50000
    required_columns: tuple[str, ...] = ()
    min_rows: int = 1


def run_sql(request: AnalysisRequest) -> pd.DataFrame:
    sql = request.sql.strip().rstrip(";")
    bounded_sql = f"SELECT * FROM ({sql}) q LIMIT {request.max_rows}"
    df = fetch_dataframe(bounded_sql)
    print(f"Loaded {len(df):,} rows for request: {request.name}")
    return df


def summarize(df: pd.DataFrame) -> None:
    print("\n== Shape ==")
    print(df.shape)
    print("\n== Columns ==")
    print(list(df.columns))
    print("\n== Head ==")
    print(df.head(10))


def validate(df: pd.DataFrame, request: AnalysisRequest) -> list[str]:
    issues: list[str] = []

    if len(df) < request.min_rows:
        issues.append(f"Expected at least {request.min_rows} rows, got {len(df)}")

    missing = [col for col in request.required_columns if col not in df.columns]
    if missing:
        issues.append(f"Missing required columns: {missing}")

    return issues


def run_iteration(request: AnalysisRequest) -> pd.DataFrame:
    df = run_sql(request)
    summarize(df)
    issues = validate(df, request)

    if issues:
        print("\n== Validation issues ==")
        for issue in issues:
            print(f"- {issue}")
        raise RuntimeError("Validation failed. Update SQL/code and rerun.")

    print("\nValidation passed.")
    return df


def main() -> None:
    request = AnalysisRequest(
        name="Example request",
        sql="""
            SELECT *
            FROM STREAMLIT_APPS.DBO.ML_SANDBOX_BEHAVIOR_CLUSTERS
        """,
        max_rows=10000,
        required_columns=(),
        min_rows=1,
    )
    run_iteration(request)


if __name__ == "__main__":
    main()
