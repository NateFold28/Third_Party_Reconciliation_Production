from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from Netsuite_Invoice_JSON_Ingestion_Prod import _parse_auvik


class AuvikInvoiceParserTests(unittest.TestCase):
    def test_recovers_quantity_when_ocr_wraps_final_digit(self) -> None:
        text = """
| CHARGE DESCRIPTION | P.O. # | SERVICE PERIOD | QTY | UNIT PRICE | TOTAL |
| --- | --- | --- | --- | --- | --- |
| Account: Connectwise (OEM) | | | | | |
| BASIC | | Jun 1 2026 - Jun 30 2026 | 2,358,64 | 0.132 | 311,341.01 |
| | | | 4 | | |
"""

        rows = _parse_auvik(text, "2026_07/invoice.pdf")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 2_358_644.0)
        self.assertEqual(rows[0]["amount"], 311_341.01)

    def test_preserves_arithmetically_valid_quantity(self) -> None:
        text = """
| CHARGE DESCRIPTION | P.O. # | SERVICE PERIOD | QTY | UNIT PRICE | TOTAL |
| --- | --- | --- | --- | --- | --- |
| Account: Connectwise (OEM) | | | | | |
| BASIC | | Jul 1 2026 - Jul 31 2026 | 2,416,600 | 0.132 | 318,991.20 |
"""

        rows = _parse_auvik(text, "2026_08/invoice.pdf")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 2_416_600.0)


if __name__ == "__main__":
    unittest.main()
