from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from Webroot_Vendor_Usage_Ingestion_Prod import (
    AUDIT_COLUMNS,
    PARTNER_SEED_COLUMNS,
    USAGE_COLUMNS,
    load_snowflake,
    _source_period_mismatch,
)


class WebrootSourcePeriodTests(unittest.TestCase):
    def test_accepts_matching_folder_and_billing_date(self) -> None:
        path = Path("07_JUL_2026/Aggregator Order Details.xlsx")

        error = _source_period_mismatch(path, dt.date(2026, 7, 15))

        self.assertIsNone(error)

    def test_rejects_prior_month_file_copied_into_folder(self) -> None:
        path = Path("07_JUL_2026/Aggregator Order Details.xlsx")

        error = _source_period_mismatch(path, dt.date(2026, 6, 25))

        self.assertEqual(
            error,
            "Source folder month 2026-07 disagrees with embedded billing date month 2026-06.",
        )

    @patch("invoice_rate_backfill.fill_missing_prices_dynamic")
    @patch("snowflake.connector.pandas_tools.write_pandas")
    @patch("Webroot_Vendor_Usage_Ingestion_Prod._snowflake_connection")
    def test_price_fill_only_runs_for_usage_table(
        self,
        connection_mock,
        write_mock,
        fill_mock,
    ) -> None:
        connection_mock.return_value.cursor.return_value = MagicMock()
        fill_mock.side_effect = lambda df, vendor_name, conn: df
        write_mock.return_value = (True, 1, 1, None)
        usage = pd.DataFrame([
            {
                "BILLING_MONTH": dt.date(2026, 7, 1),
                "VENDOR": "Webroot",
                "VENDOR_PARTNER_NAME": "Example",
                "VENDOR_PRODUCT_SKU": "GSM",
                "MODIFIER": "CW",
                "QUANTITY": 1.0,
                "UNIT_PRICE": 0.56,
                "AMOUNT": 0.56,
                "CURRENCY": "USD",
            }
        ], columns=USAGE_COLUMNS)
        audit = pd.DataFrame([{column: None for column in AUDIT_COLUMNS}])
        partner = pd.DataFrame([{column: None for column in PARTNER_SEED_COLUMNS}])

        load_snowflake(usage, audit, partner)

        fill_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()