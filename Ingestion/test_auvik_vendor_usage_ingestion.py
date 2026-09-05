from __future__ import annotations

# pyright: reportPrivateUsage=false

import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from Auvik_Vendor_Usage_Ingestion_Prod import (
    AUDIT_COLUMNS,
    USAGE_COLUMNS,
    SourceFile,
    _billable_quantity,
    _month_from_folder,
    _product_tenant_id_text,
    _vendor_product_sku,
    build_usage,
    load_snowflake,
)


class AuvikQuantityTests(unittest.TestCase):
    def test_committed_rows_use_committed_quantity(self) -> None:
        self.assertEqual(_billable_quantity("Committed", 100, 100, 735), 100)

    def test_chargeable_usage_rows_use_positive_overage(self) -> None:
        self.assertEqual(_billable_quantity("Usage", 185, 56, 272.72), 56)

    def test_negative_or_zero_dollar_overage_does_not_reduce_usage(self) -> None:
        self.assertEqual(_billable_quantity("Usage", 87, -27, 0), 0)
        self.assertEqual(_billable_quantity("Usage", 87, -27, 10), 0)

    def test_overage_product_is_normalized_to_base_sku(self) -> None:
        product = "Overage - ANM Essentials Evergreen"
        self.assertEqual(_vendor_product_sku(product), "ANM Essentials Evergreen")

    def test_numeric_tenant_id_is_marked_precision_limited(self) -> None:
        value, precision_limited = _product_tenant_id_text(1.94105e17)
        self.assertEqual(value, "1.94105e+17")
        self.assertTrue(precision_limited)


class AuvikDiscoveryTests(unittest.TestCase):
    def test_rejects_invalid_month_folder_abbreviation(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid month folder name"):
            _month_from_folder(Path("07_JUN_2026/usage.xlsx"))

    @patch("Auvik_Vendor_Usage_Ingestion_Prod.parse_usage_workbook")
    @patch("Auvik_Vendor_Usage_Ingestion_Prod.discover_source_files")
    def test_cw_copy_is_authoritative_when_both_roots_have_month(
        self,
        discover_mock: MagicMock,
        parse_mock: MagicMock,
    ) -> None:
        cw = SourceFile(Path("CW/07_JUL_2026/usage.xlsx"), "2026-07")
        cms = SourceFile(Path("CMS/07_JUL_2026/usage.xlsx"), "2026-07")
        discover_mock.side_effect = [[cw], [cms]]
        usage = pd.DataFrame(
            [{
                "BILLING_MONTH": dt.date(2026, 7, 1),
                "VENDOR": "Auvik",
                "VENDOR_PARTNER_NAME": "Example",
                "VENDOR_PRODUCT_SKU": "ANM Essentials",
                "MODIFIER": "CW",
                "QUANTITY": 1.0,
                "UNIT_PRICE": 1.0,
                "AMOUNT": 1.0,
                "CURRENCY": "USD",
                "ADDITIONAL_INFO": None,
                "_SOURCE_PRODUCT": "ANM Essentials",
                "_CHARGE_TYPE": "Committed",
                "_INVOICE_NAME": "INV-1",
                "_SOURCE_FILE": "usage.xlsx",
                "_SOURCE_SHEET": "INV-1",
                "_SOURCE_ROW_NUMBER": 2,
                "_PRIMARY_TENANT_ID": "primary",
                "_PRODUCT_TENANT_ID": "product",
                "_PRODUCT_TENANT_ID_PRECISION_LIMITED": False,
                "_DOMAIN_PREFIX": "example",
                "_START_DATE": dt.date(2026, 7, 1),
                "_END_DATE": dt.date(2026, 7, 31),
                "_INVOICE_DATE": dt.date(2026, 8, 1),
                "_SOURCE_QUANTITY": 1.0,
                "_OVERAGE_QUANTITY": 1.0,
                "_SUBTOTAL": 1.0,
                "_TAX": 0.0,
            }]
        )
        audit = pd.DataFrame([{column: None for column in AUDIT_COLUMNS}])
        audit.loc[0, "SOURCE_CONTENT_HASH"] = "hash"
        audit.loc[0, "LOAD_STATUS"] = "LOADED"
        parse_mock.return_value = (usage, audit)

        result, _ = build_usage(Path("CW"), Path("CMS"), {"2026-07"})

        parse_mock.assert_called_once()
        self.assertEqual(parse_mock.call_args.args[0], cw)
        self.assertEqual(len(result), 1)
        provenance = json.loads(str(result.loc[0, "ADDITIONAL_INFO"]))
        self.assertEqual(provenance["source_product"], "ANM Essentials")


class AuvikPublicationTests(unittest.TestCase):
    @patch("snowflake.connector.pandas_tools.write_pandas")
    @patch("Auvik_Vendor_Usage_Ingestion_Prod._snowflake_connection")
    def test_load_stages_before_atomic_publish(
        self,
        connection_mock: MagicMock,
        write_mock: MagicMock,
    ) -> None:
        cursor = MagicMock()
        connection_mock.return_value.cursor.return_value = cursor
        write_mock.return_value = (True, 1, 1, None)
        usage = pd.DataFrame(
            [{
                "BILLING_MONTH": dt.date(2026, 7, 1),
                "VENDOR": "Auvik",
                "VENDOR_PARTNER_NAME": "Example",
                "VENDOR_PRODUCT_SKU": "ANM Essentials",
                "MODIFIER": "CW",
                "QUANTITY": 1.0,
                "UNIT_PRICE": 1.0,
                "AMOUNT": 1.0,
                "CURRENCY": "USD",
                "ADDITIONAL_INFO": "{}",
            }],
            columns=USAGE_COLUMNS,
        )
        audit = pd.DataFrame([{column: None for column in AUDIT_COLUMNS}])

        load_snowflake(usage, audit)

        self.assertEqual(write_mock.call_count, 2)
        self.assertTrue(all(c.kwargs.get("use_logical_type") for c in write_mock.call_args_list))
        statements = [str(c.args[0]) for c in cursor.execute.call_args_list]
        begin_index = next(i for i, sql in enumerate(statements) if sql == "BEGIN")
        insert_indices = [i for i, sql in enumerate(statements) if "INSERT INTO" in sql]
        self.assertTrue(insert_indices and all(i > begin_index for i in insert_indices))
        connection_mock.return_value.commit.assert_called_once()

    @patch("snowflake.connector.pandas_tools.write_pandas")
    @patch("Auvik_Vendor_Usage_Ingestion_Prod._snowflake_connection")
    def test_failed_staging_never_begins_publication(
        self,
        connection_mock: MagicMock,
        write_mock: MagicMock,
    ) -> None:
        cursor = MagicMock()
        connection_mock.return_value.cursor.return_value = cursor
        write_mock.return_value = (False, 0, 0, "failed")
        usage = pd.DataFrame(
            [{
                "BILLING_MONTH": dt.date(2026, 7, 1),
                "VENDOR": "Auvik",
                "VENDOR_PARTNER_NAME": "Example",
                "VENDOR_PRODUCT_SKU": "ANM Essentials",
                "MODIFIER": "CW",
                "QUANTITY": 1.0,
                "UNIT_PRICE": 1.0,
                "AMOUNT": 1.0,
                "CURRENCY": "USD",
                "ADDITIONAL_INFO": "{}",
            }],
            columns=USAGE_COLUMNS,
        )

        with self.assertRaisesRegex(RuntimeError, "Staging failed"):
            load_snowflake(usage, pd.DataFrame(columns=AUDIT_COLUMNS))

        statements = [str(c.args[0]) for c in cursor.execute.call_args_list]
        self.assertNotIn("BEGIN", statements)
        connection_mock.return_value.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
