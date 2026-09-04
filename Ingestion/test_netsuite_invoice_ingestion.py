from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from Netsuite_Invoice_JSON_Ingestion_Prod import _parse_auvik, _parse_webroot, parse_all


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


class WebrootInvoiceParserTests(unittest.TestCase):
    line_table = """
|  Qty | Description | Ship Via | Unit Price | Subtotal | Tax : | Price  |
| --- | --- | --- | --- | --- | --- | --- |
|  515 | 1000062533 OpenText Core Endpoint Protection 2026-05-15 to 2026-06-14 Contract Number : 426568 PO Number:June PO End User Information:10309662 End User Name:CONNECTWISE LLC Entitlement Group:10309662 | Electronic | 406.85 | 406.85 | 0.00 | 406.85 USD  |
|  237,098 | 1000062533 OpenText Core Endpoint Protection 2026-05-15 to 2026-06-14 Contract Number : 426569 PO Number:June PO End User Information:10309662 End User Name:CONNECTWISE LLC Entitlement Group:10309662 | Electronic | 132,774.88 | 132,774.88 | 0.00 | 132,774.88 USD  |
"""

    def test_extracts_open_text_invoice_fields(self) -> None:
        rows = _parse_webroot(self.line_table, "2026_06/invoice.pdf")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["sku"], "1000062533")
        self.assertEqual(rows[0]["description"], "OpenText Core Endpoint Protection")
        self.assertEqual(rows[0]["quantity"], 515.0)
        self.assertEqual(rows[0]["unit_price"], 406.85)
        self.assertEqual(rows[0]["amount"], 406.85)
        self.assertEqual(rows[1]["quantity"], 237_098.0)
        self.assertEqual(rows[1]["amount"], 132_774.88)

    def test_routes_open_text_to_webroot_target_schema(self) -> None:
        parsed_document = {
            "pages": [{
                "content": (
                    "Invoice To:\nCONNECTWISE LLC\nCustomer ID:\n10309662\n"
                    "SMB Invoice\nBilling Doc. #: 9006222523\n" + self.line_table
                ),
            }]
        }

        result = parse_all([
            (101459330, "OpenText Inc", "2026_06/source.pdf", parsed_document),
        ])

        self.assertEqual(len(result), 2)
        first = result.iloc[0]
        self.assertEqual(first["BILLING_MONTH"], "2026-06-01")
        self.assertEqual(first["VENDOR"], "Webroot")
        self.assertEqual(first["INVOICE_ID"], "9006222523")
        self.assertEqual(first["INVOICE_DESCRIPTION"], "Main")
        self.assertEqual(first["NETSUITE_TRANSACTION_ID"], "101459330")
        self.assertEqual(
            first["NETSUITE_URL"],
            "https://6230579.app.netsuite.com/app/accounting/transactions/"
            "vendbill.nl?id=101459330&whence=",
        )
        self.assertIsNone(first["PARTNER"])
        self.assertEqual(first["SOURCE_STREAM"], "CW")

    def test_identifies_continuum_invoice_as_cms_stream(self) -> None:
        parsed_document = {
            "pages": [{
                "content": (
                    "Invoice To:\nContinuum Holdco 1, LLC\nCustomer ID:\n10551253\n"
                    "SMB Invoice\nBilling Doc. #: 9006231323\n" + self.line_table
                ),
            }]
        }

        result = parse_all([
            (101577439, "OpenText Inc", "2026_06/source.pdf", parsed_document),
        ])

        self.assertEqual(set(result["SOURCE_STREAM"]), {"CMS"})


if __name__ == "__main__":
    unittest.main()
