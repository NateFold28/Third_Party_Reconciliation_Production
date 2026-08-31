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
            VENDOR_PARTNER_NAME,
            SF_ID,
          COUNT(*) AS ROW_COUNT,
            ROUND(SUM(COALESCE(VENDOR_QUANTITY, 0)), 0) AS VENDOR_QTY,
            ROUND(SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0)), 0) AS BILLING_QTY
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE VENDOR = 'SentinelOne'
          AND UPPER(TRIM(VENDOR_PARTNER_NAME)) IN (
            'ACCESS GROUP INC', 'ELEVITYIT', 'ELEVITY IT', 'EXECUTECH', 'GFLEX', 'KMICRO', 'NUMSP', 'NUMSP', 'SFY', 'SFY IT', 'SFY IT'
          )
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    )
    print(fetch_dataframe(q).to_string(index=False))


if __name__ == "__main__":
    main()
