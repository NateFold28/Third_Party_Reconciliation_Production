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
    _month_from_folder,
    _source_period_mismatch,
    validate_source_completeness,
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

    def test_rejects_invalid_month_folder_abbreviation(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid month folder name"):
            _month_from_folder(Path("07_JUN_2026/Aggregator Order Details.xlsx"))

    def test_requires_all_monthly_source_slots(self) -> None:
        audit = pd.DataFrame([
            {
                "STREAM": "CW",
                "CHANNEL": "MSP",
                "SOURCE_FILE": "cw-msp.xlsx",
                "LOAD_STATUS": "LOADED",
                "BILLING_MONTH": dt.date(2026, 7, 1),
                "ERROR_MESSAGE": None,
                "SUBTOTAL_ROW_COUNT": 1,
                "TOTAL_SEATS_DELTA": 0,
                "TOTAL_EXTENDED_AMOUNT_DELTA": 0,
            },
            {
                "STREAM": "CW",
                "CHANNEL": "RESELLER",
                "SOURCE_FILE": "cw-reseller.xlsx",
                "LOAD_STATUS": "LOADED",
                "BILLING_MONTH": dt.date(2026, 7, 1),
                "ERROR_MESSAGE": None,
                "SUBTOTAL_ROW_COUNT": 1,
                "TOTAL_SEATS_DELTA": 0,
                "TOTAL_EXTENDED_AMOUNT_DELTA": 0,
            },
        ])

        with self.assertRaisesRegex(RuntimeError, "source manifest mismatch"):
            validate_source_completeness(audit, {"2026-07"})

    @patch("snowflake.connector.pandas_tools.write_pandas")
    @patch("Webroot_Vendor_Usage_Ingestion_Prod._snowflake_connection")
    def test_load_stages_all_tables_before_atomic_publish(
        self,
        connection_mock,
        write_mock,
    ) -> None:
        cursor = MagicMock()
        connection_mock.return_value.cursor.return_value = cursor
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
                "ADDITIONAL_INFO": "{}",
            }
        ], columns=USAGE_COLUMNS)
        audit = pd.DataFrame([{column: None for column in AUDIT_COLUMNS}])
        partner = pd.DataFrame([{column: None for column in PARTNER_SEED_COLUMNS}])

        load_snowflake(usage, audit, partner)

        self.assertEqual(write_mock.call_count, 3)
        self.assertTrue(
            all(call.kwargs.get("use_logical_type") for call in write_mock.call_args_list)
        )
        executed_sql = "\n".join(
            str(call.args[0]) for call in cursor.execute.call_args_list
        )
        self.assertIn("CREATE OR REPLACE TEMPORARY TABLE", executed_sql)
        self.assertIn("BEGIN", executed_sql)
        self.assertIn("INSERT INTO", executed_sql)
        connection_mock.return_value.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()