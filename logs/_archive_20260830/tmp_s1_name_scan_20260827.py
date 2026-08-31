from __future__ import annotations

from pathlib import Path
import sys
from textwrap import dedent

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from TEMPLATES.Python.connection import fetch_dataframe


def main() -> None:
    q = dedent(
        """
        SELECT
            UPPER(TRIM(VENDOR_PARTNER_NAME)) AS PARTNER_NAME,
            COUNT(*) AS ROW_COUNT,
            SUM(COALESCE(QUANTITY, 0)) AS QTY
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.SENTINELONE_USAGE
        WHERE BILLING_MONTH >= '2026-01-01'
          AND REGEXP_LIKE(UPPER(VENDOR_PARTNER_NAME), 'SFY|ELEVITY|NUMSP|EXECUTECH|KMICRO|GFLEX|ACCESS\\s+GROUP')
        GROUP BY 1
        ORDER BY 1
        """
    )
    print(fetch_dataframe(q).to_string(index=False))


if __name__ == "__main__":
    main()
