from __future__ import annotations

from pathlib import Path
import sys
from textwrap import dedent

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from TEMPLATES.Python.connection import fetch_dataframe

SFIDS = [
    "ACT-00238028",
    "ACT-00245551",
    "ACT-00035427",
    "ACT-00246790",
    "ACT-00246783",
    "ACT-00245462",
    "ACT-00200001",
]


def _sql_list(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def main() -> None:
    sfids = _sql_list(SFIDS)
    q = dedent(
        f"""
        SELECT
            a.CWS_ACCOUNT_UNIQUE_IDENTIFIER_C AS SF_ID,
            a.NAME AS ACCOUNT_NAME,
            a.PARENT_ID,
            p.CWS_ACCOUNT_UNIQUE_IDENTIFIER_C AS PARENT_SF_ID,
            p.NAME AS PARENT_NAME
        FROM ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT a
        LEFT JOIN ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT p
            ON p.ID = a.PARENT_ID
           AND p.IS_DELETED = FALSE
        WHERE a.IS_DELETED = FALSE
          AND a.CWS_ACCOUNT_UNIQUE_IDENTIFIER_C IN ({sfids})
        ORDER BY a.CWS_ACCOUNT_UNIQUE_IDENTIFIER_C
        """
    )
    df = fetch_dataframe(q)
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
