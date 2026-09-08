from __future__ import annotations

import datetime as dt
import importlib.util
import tempfile
import unittest
from pathlib import Path

import openpyxl


MODULE_PATH = Path(__file__).with_name("SentinelOne_Vendor_Usage_Ingestion_Prod.py")
SPEC = importlib.util.spec_from_file_location("sentinelone_ingestion", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
s1 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s1)


def _write_workbook(
    path: Path,
    *,
    duplicate_site: bool = False,
    footer_delta: float = 0,
) -> None:
    headers = [
        s1.ACCOUNT_COL,
        s1.SITE_ACCOUNT_COL,
        s1.SKU_COL,
        s1.SITE_TYPE_COL,
        s1.SITE_ID_COL,
        s1.CREATED_DATE_COL,
        s1.RETENTION_COL,
        *s1.EXPECTED_AGENT_COLUMNS,
    ]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = s1.EXPECTED_SHEET_NAME
    ws.append(headers)

    values = {column: 0 for column in s1.EXPECTED_AGENT_COLUMNS}
    values[s1.TOTAL_AGENTS_COL] = 10
    values["Ranger Active Agents"] = 2
    values["Data Retention Agents"] = 3

    def detail(partner: str, site_id: str) -> list[object]:
        return [
            "Connectwise",
            partner,
            "Complete",
            "Paid",
            site_id,
            dt.datetime(2026, 1, 20),
            "30 Days",
            *(values[column] for column in s1.EXPECTED_AGENT_COLUMNS),
        ]

    ws.append(detail("Partner A", "site-1"))
    if duplicate_site:
        ws.append(detail("Partner B", "site-1"))

    multiplier = 2 if duplicate_site else 1
    footer_values = {
        column: values[column] * multiplier for column in s1.EXPECTED_AGENT_COLUMNS
    }
    footer_values[s1.TOTAL_AGENTS_COL] += footer_delta
    ws.append(
        [
            "Total",
            "",
            "",
            "",
            "",
            "",
            "",
            *(footer_values[column] for column in s1.EXPECTED_AGENT_COLUMNS),
        ]
    )
    wb.save(path)
    wb.close()


class DiscoveryTests(unittest.TestCase):
    def test_authoritative_file_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            expected = folder / "ConnectWise Usage_012026.xlsx"
            expected.touch()
            (folder / "S1-USAGE-JAN2026.xlsx").touch()
            self.assertEqual(s1.locate_usage_file(folder), expected)

    def test_ambiguous_authoritative_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "ConnectWise Usage_a.xlsx").touch()
            (folder / "ConnectWise Usage_b.xlsm").touch()
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                s1.locate_usage_file(folder)


class ContractTests(unittest.TestCase):
    def _parse(self, path: Path):
        rates = {
            "COMPLETE": {dt.date(2025, 12, 1): 1.0, dt.date(2026, 1, 1): 1.25},
            "RANGER": {dt.date(2025, 12, 1): 0.5},
        }
        return s1.parse_usage_workbook(
            path,
            expected_month="2026-01",
            rate_history=rates,
            usage_alias_map={},
            ingested_at=dt.datetime(2026, 1, 21),
        )

    def test_quantity_aggregation_and_rates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ConnectWise Usage_test.xlsx"
            _write_workbook(path)
            result = self._parse(path).set_index("VENDOR_PRODUCT_SKU")
            self.assertEqual(float(result.loc["Complete", "QUANTITY"]), 10)
            self.assertEqual(float(result.loc["Complete", "UNIT_PRICE"]), 1.25)
            self.assertEqual(float(result.loc["Complete", "AMOUNT"]), 12.5)
            self.assertEqual(float(result.loc["Ranger", "UNIT_PRICE"]), 0.5)
            self.assertEqual(float(result.loc["Data Retention - 30 Days", "QUANTITY"]), 3)
            self.assertIn("source_content_hash", result.loc["Complete", "ADDITIONAL_INFO"])

    def test_footer_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ConnectWise Usage_test.xlsx"
            _write_workbook(path, footer_delta=1)
            with self.assertRaisesRegex(RuntimeError, "do not match the Total row"):
                self._parse(path)

    def test_duplicate_site_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ConnectWise Usage_test.xlsx"
            _write_workbook(path, duplicate_site=True)
            with self.assertRaisesRegex(RuntimeError, "duplicate Site ID"):
                self._parse(path)


class RateResolutionTests(unittest.TestCase):
    def test_exact_month_wins(self) -> None:
        history = {"CONTROL": {dt.date(2025, 12, 1): 0.6, dt.date(2026, 1, 1): 0.7}}
        self.assertEqual(s1._resolve_month_rate("CONTROL", dt.date(2026, 1, 1), history), 0.7)

    def test_latest_prior_month_fallback(self) -> None:
        history = {"CONTROL": {dt.date(2025, 11, 1): 0.5, dt.date(2025, 12, 1): 0.6}}
        self.assertEqual(s1._resolve_month_rate("CONTROL", dt.date(2026, 1, 1), history), 0.6)


if __name__ == "__main__":
    unittest.main()