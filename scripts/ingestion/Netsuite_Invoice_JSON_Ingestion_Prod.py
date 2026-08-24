"""Populate THIRD_PARTY_RECON_VENDOR_INVOICES from NETSUITE.DBO.PARSED_VENDOR_DATA.

SOURCE
    NETSUITE.DBO.PARSED_VENDOR_DATA
      VENDOR_NAME       - raw vendor name (used for routing to per-vendor parser)
      FILE_PATH         - format YYYY_MM/<filename> -- YYYY_MM is the billing month
      PARSED_DOCUMENT   - JSON: {"pages": [{"content": "<pdf text>"}, ...]}

TARGET
    ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
      BILLING_MONTH      DATE
      VENDOR             VARCHAR
      PARTNER            VARCHAR
      VENDOR_PRODUCT_SKU VARCHAR
      DESCRIPTION        VARCHAR
      QUANTITY           NUMBER(18,6)
      UNIT_PRICE         NUMBER(18,6)
      AMOUNT             NUMBER(18,6)
      FILE_PATH          VARCHAR

HOW PRICES FLOW DOWNSTREAM
    VENDOR_INVOICES -> sql/00b_backfill_invoice_prices.sql (LAST_VALUE IGNORE NULLS carry-forward)
    -> THIRD_PARTY_RECON_VENDOR_USAGE_PROD (UNIT_PRICE + AMOUNT gaps filled dynamically)

Run modes:
    --dry-run         Parse all; print summary; do NOT write.
    --validate        Show live table stats vs parsed output. Implies dry-run.
    --inspect         Print raw PDF text for matching rows.
    --vendor V        Filter to vendor_name ILIKE '%V%'
    --month YYYY-MM   Filter to FILE_PATH ILIKE '%YYYY_MM%'
    --append          Append rows instead of replacing.
    --from YYYY-MM    Process all months >= YYYY-MM (default: 2026-01)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Callable

import pandas as pd

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

TARGET_TABLE = "ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES"
DEFAULT_FROM_MONTH = "2026-01"

VENDOR_NAME_MAP: dict[str, str] = {
    "acronis":     "Acronis",
    "auvik":       "Auvik",
    "bitdefender": "Bitdefender",
    "eset":        "ESET",
    "exium":       "Exium",
    "keepit":      "KeepIT",
    "proofpoint":  "Proofpoint",
    "sentinelone": "SentinelOne",
    "webroot":     "Webroot",
}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _month_from_path(file_path: str) -> str | None:
    m = re.match(r"(\d{4})_(\d{2})/", file_path or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-01"
    return None


def _num(s: object) -> float | None:
    """Parse a numeric string, handling both US (1,234.56) and European (1.234,56) formats."""
    if s is None:
        return None
    t = str(s).strip().lstrip("$").replace(" ", "")
    if not t or t in ("-", "—"):
        return None
    # Detect European format: ends with ,XX (two decimal places after comma with dots before)
    if re.match(r"^\d{1,3}(\.\d{3})*(,\d+)$", t):
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def _extract_text(parsed_doc) -> str:
    try:
        doc = json.loads(parsed_doc) if isinstance(parsed_doc, str) else parsed_doc
        if not isinstance(doc, dict):
            return ""
        return "\n\n".join(p.get("content", "") for p in doc.get("pages", []))
    except Exception:
        return ""


def _parse_markdown_table(text: str) -> list[list[str]]:
    """Return rows from a markdown table as lists of stripped cell strings."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|") and not re.match(r"^\|[-| :]+\|$", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows.append(cells)
    return rows


# ---------------------------------------------------------------------------
# Per-vendor parsers
# Format confirmed from live PDF text samples (2026-05/06 invoices).
# ---------------------------------------------------------------------------

def _parse_acronis(text: str, file_path: str) -> list[dict]:
    """
    Table: | Pos. | Item number | Quantity | Unit price | Tax pct | Amount USD |
    Item number format: "SCODE: description text"
    Partner: always NULL (one invoice per month, no per-partner breakdown)
    """
    results: list[dict] = []
    rows = _parse_markdown_table(text)
    # Find header
    header_idx = None
    for i, row in enumerate(rows):
        h = [c.lower() for c in row]
        if any("item number" in c for c in h) and any("quantity" in c for c in h):
            header_idx = i
            break
    if header_idx is None:
        return results

    hdr = [c.lower() for c in rows[header_idx]]
    try:
        i_item = next(i for i, h in enumerate(hdr) if "item number" in h)
        i_qty  = next(i for i, h in enumerate(hdr) if "quantity" in h)
        i_up   = next(i for i, h in enumerate(hdr) if "unit price" in h)
        i_amt  = next(i for i, h in enumerate(hdr) if "amount" in h)
    except StopIteration:
        return results

    for row in rows[header_idx + 1:]:
        if len(row) <= max(i_item, i_qty, i_up, i_amt):
            continue
        item_cell = row[i_item].strip()
        if not item_cell or item_cell.startswith("-"):
            continue
        # Split "CODE: description" - code is everything before first ":"
        if ":" in item_cell:
            sku, desc = item_cell.split(":", 1)
            sku  = sku.strip()
            desc = desc.strip()
        else:
            sku  = item_cell
            desc = ""
        qty   = _num(row[i_qty])
        up    = _num(row[i_up])
        amt   = _num(row[i_amt])
        if not sku or (qty is None and amt is None):
            continue
        results.append({"partner": None, "sku": sku, "description": desc,
                         "quantity": qty, "unit_price": up, "amount": amt})
    return results


def _parse_auvik(text: str, file_path: str) -> list[dict]:
    """
    Each account block:
      Row 1 (header): | CHARGE DESCRIPTION | P.O. # | SERVICE PERIOD | QTY | UNIT PRICE | TOTAL |
      Row 2 (account): | Account: <name> | | | | | |   <- all cells except first empty
      Row 3..N (data): | <SKU description> | | <period> | <qty> | <unit_price> | <total> |
      Row N+1 (total): | | | | | Amount Total: X | |  <- second-to-last cell has Amount Total
    """
    results: list[dict] = []
    current_partner: str | None = None

    rows = _parse_markdown_table(text)
    for row in rows:
        if not row:
            continue
        first = row[0].strip()

        # Account header row: first cell = "Account: <partner name>"
        acc_match = re.match(r"Account:\s*(.+)", first, re.IGNORECASE)
        if acc_match:
            current_partner = acc_match.group(1).strip()
            continue

        # Skip: header rows, dividers, and invoice metadata rows
        first_lc = first.lower()
        if "charge description" in first_lc or not first:
            continue
        # Skip invoice/remittance metadata rows: these are non-billing tables at top/bottom of Auvik invoices
        if re.match(r"^(account name|account number|bank|routing|iban|swift|wire|remit|"
                    r"invoice number|invoice date|due date|payment terms|payment method|po number|"
                    r"bill to|ship to|subtotal|tax total|total|please)",
                    first_lc):
            continue

        # Skip Amount Total summary row: second-to-last cell contains "Amount Total:"
        if len(row) >= 2 and "amount total" in row[-2].lower():
            continue

        # Data row: columns are CHARGE DESCRIPTION | P.O.# | SERVICE PERIOD | QTY | UNIT PRICE | TOTAL
        # All cells after [0] except QTY/UNIT PRICE/TOTAL may be blank.
        # Last 3 numeric-candidate cells = total, unit_price, qty (right to left)
        if len(row) < 4:
            continue

        total = _num(row[-1])
        up    = _num(row[-2])
        qty   = _num(row[-3])

        # A valid data row must have at least a non-zero TOTAL
        if total is None:
            continue

        sku = first
        if not sku:
            continue

        results.append({
            "partner":     current_partner,
            "sku":         sku,
            "description": sku,
            "quantity":    qty,
            "unit_price":  up,
            "amount":      total,
        })
    return results


def _parse_bitdefender(text: str, file_path: str) -> list[dict]:
    """
    Table: | Item number | Description | Quantity | Unit | Unit price | Discount | Net amount | Sales tax |
    unit_price is always 0 in Bitdefender invoices; use Net amount for amount.
    Description contains the order number etc. — extract just the product description before "Order number"
    """
    results: list[dict] = []
    rows = _parse_markdown_table(text)
    header_idx = None
    for i, row in enumerate(rows):
        h = [c.lower() for c in row]
        if any("item number" in c for c in h) and any("net amount" in c for c in h):
            header_idx = i
            break
    if header_idx is None:
        return results

    hdr = [c.lower() for c in rows[header_idx]]
    try:
        i_item = next(i for i, h in enumerate(hdr) if "item number" in h)
        i_desc = next(i for i, h in enumerate(hdr) if "description" in h)
        i_qty  = next(i for i, h in enumerate(hdr) if "quantity" in h)
        i_net  = next(i for i, h in enumerate(hdr) if "net amount" in h)
    except StopIteration:
        return results

    for row in rows[header_idx + 1:]:
        if len(row) <= max(i_item, i_desc, i_qty, i_net):
            continue
        sku = row[i_item].strip()
        if not sku or sku.startswith("-"):
            continue
        raw_desc = row[i_desc].strip()
        # Strip "Order number : ..." and everything after
        desc = re.split(r"\s*/\s*" + re.escape(sku), raw_desc, maxsplit=1)[0].strip()
        desc = re.split(r"Order\s+number\s*:", desc, flags=re.IGNORECASE)[0].strip()
        # Clean up slash-separated duplicate SKU entries "BP_2773 : : 1 : 315150 : BP_2773"
        desc = re.split(r"\s*:\s*:\s*\d+\s*:", desc, maxsplit=1)[0].strip()
        qty  = _num(row[i_qty])
        amt  = _num(row[i_net])
        results.append({"partner": None, "sku": sku, "description": desc,
                         "quantity": qty, "unit_price": None, "amount": amt})
    return results


def _parse_eset(text: str, file_path: str) -> list[dict]:
    """
    Table: | ITEM | DESCRIPTION | SEATS | RATE | NET PRICE |
    Note: table text may be multi-line per cell (ESET sometimes repeats the PO in the cell).
    """
    results: list[dict] = []
    rows = _parse_markdown_table(text)
    header_idx = None
    for i, row in enumerate(rows):
        h = [c.lower() for c in row]
        if any("item" in c for c in h) and any("seats" in c for c in h):
            header_idx = i
            break
    if header_idx is None:
        return results

    hdr = [c.lower() for c in rows[header_idx]]
    try:
        i_item = next(i for i, h in enumerate(hdr) if h == "item")
        i_desc = next(i for i, h in enumerate(hdr) if "description" in h)
        i_qty  = next(i for i, h in enumerate(hdr) if "seats" in h)
        i_rate = next(i for i, h in enumerate(hdr) if "rate" in h)
        i_net  = next(i for i, h in enumerate(hdr) if "net price" in h or "net" in h)
    except StopIteration:
        return results

    for row in rows[header_idx + 1:]:
        if len(row) <= max(i_item, i_desc, i_qty, i_rate, i_net):
            continue
        sku  = row[i_item].strip()
        desc = row[i_desc].strip()
        if not sku:
            continue
        # ESET sometimes packs "ITEM | DESCRIPTION | billing_period | seats | rate | net"
        # Try to find numeric qty by scanning from right
        qty  = _num(row[i_qty])
        rate = _num(row[i_rate])
        net  = _num(row[i_net])
        # Drop pure-header or divider rows
        if qty is None and net is None:
            continue
        results.append({"partner": None, "sku": sku, "description": desc,
                         "quantity": qty, "unit_price": rate, "amount": net})
    return results


def _parse_exium(text: str, file_path: str) -> list[dict]:
    """
    Exium is invoiced through Netgear (EX-* product codes).
    Table: | ITEM # | DESCRIPTION | PART# | QTY SHIPPED | UNIT PRICE | EXTENDED PRICE |
    vendor_product_sku = DESCRIPTION (EX-CGW, EX-SASE-PRO, etc.)
    """
    results: list[dict] = []
    rows = _parse_markdown_table(text)
    header_idx = None
    for i, row in enumerate(rows):
        h = [c.lower() for c in row]
        if any("description" in c for c in h) and any("qty" in c for c in h):
            header_idx = i
            break
    if header_idx is None:
        return results

    hdr = [c.lower() for c in rows[header_idx]]
    try:
        i_desc = next(i for i, h in enumerate(hdr) if "description" in h)
        i_qty  = next(i for i, h in enumerate(hdr) if "qty" in h)
        i_up   = next(i for i, h in enumerate(hdr) if "unit price" in h)
        i_ext  = next(i for i, h in enumerate(hdr) if "extended" in h or "ext" in h)
    except StopIteration:
        return results

    for row in rows[header_idx + 1:]:
        if len(row) <= max(i_desc, i_qty, i_up, i_ext):
            continue
        desc = row[i_desc].strip()
        if not desc or not re.match(r"^EX-", desc, re.IGNORECASE):
            continue  # skip totals row and non-product rows
        qty  = _num(row[i_qty])
        up   = _num(row[i_up])
        amt  = _num(row[i_ext])
        results.append({"partner": None, "sku": desc, "description": desc,
                         "quantity": qty, "unit_price": up, "amount": amt})
    return results


def _parse_keepit(text: str, file_path: str) -> list[dict]:
    """
    Table: | SKU | Description | Qty. | Unit Price | Net Amount | Total incl. VAT |
    European numeric format: 1.234,56 (dot=thousands separator, comma=decimal)
    """
    results: list[dict] = []
    rows = _parse_markdown_table(text)
    header_idx = None
    for i, row in enumerate(rows):
        h = [c.lower() for c in row]
        if any("sku" in c for c in h) and any("qty" in c or "quantity" in c for c in h):
            header_idx = i
            break
    if header_idx is None:
        return results

    hdr = [c.lower() for c in rows[header_idx]]
    try:
        i_sku  = next(i for i, h in enumerate(hdr) if "sku" in h)
        i_desc = next(i for i, h in enumerate(hdr) if "description" in h)
        i_qty  = next(i for i, h in enumerate(hdr) if "qty" in h or "quantity" in h)
        i_up   = next(i for i, h in enumerate(hdr) if "unit price" in h or "unit" in h)
        i_amt  = next(i for i, h in enumerate(hdr) if "total incl" in h or "total" in h)
    except StopIteration:
        return results

    for row in rows[header_idx + 1:]:
        if len(row) <= max(i_sku, i_desc, i_qty, i_up, i_amt):
            continue
        sku  = row[i_sku].strip()
        desc = row[i_desc].strip()
        if not sku or sku.upper().startswith("VAT") or sku.upper() == "SKU":
            continue
        qty  = _num(row[i_qty])
        up   = _num(row[i_up])
        amt  = _num(row[i_amt])
        if qty is None and amt is None:
            continue
        results.append({"partner": None, "sku": sku, "description": desc,
                         "quantity": qty, "unit_price": up, "amount": amt})
    return results


def _parse_proofpoint(text: str, file_path: str) -> list[dict]:
    """
    Table: | Item number | Description | Quantity | Unit | Unit price | Amount |
    Unit price is often blank in the PDF (Proofpoint reports Amount only).
    Derive unit_price = amount / quantity when unit_price is blank.
    """
    results: list[dict] = []
    rows = _parse_markdown_table(text)
    header_idx = None
    for i, row in enumerate(rows):
        h = [c.lower() for c in row]
        if any("item number" in c for c in h) and any("quantity" in c for c in h):
            header_idx = i
            break
    if header_idx is None:
        return results

    hdr = [c.lower() for c in rows[header_idx]]
    try:
        i_item = next(i for i, h in enumerate(hdr) if "item number" in h)
        i_desc = next(i for i, h in enumerate(hdr) if "description" in h)
        i_qty  = next(i for i, h in enumerate(hdr) if "quantity" in h)
        i_up   = next(i for i, h in enumerate(hdr) if "unit price" in h)
        i_amt  = next(i for i, h in enumerate(hdr) if h == "amount" or "amount" in h)
    except StopIteration:
        return results

    for row in rows[header_idx + 1:]:
        if len(row) <= max(i_item, i_desc, i_qty, i_up, i_amt):
            continue
        sku  = row[i_item].strip()
        desc = row[i_desc].strip()
        if not sku or sku.startswith("-"):
            continue
        qty  = _num(row[i_qty])
        up   = _num(row[i_up])
        amt  = _num(row[i_amt])
        # Derive unit_price when blank
        if up is None and qty and qty != 0 and amt is not None:
            up = round(amt / qty, 6)
        results.append({"partner": None, "sku": sku, "description": desc,
                         "quantity": qty, "unit_price": up, "amount": amt})
    return results


def _parse_sentinelone(text: str, file_path: str) -> list[dict]:
    """
    Table format (two-row product entries):
      Row 1: | Product Code + description | | Start | End | INV Qty | Rate | Amount |
      Row 2: | Continuation of description | | | | | | |
    Product code = first word on the line (alphanumeric like S1ES-CMP-EN-T8-SA)
    Description = rest of cell after product code.
    Some pages switch to: | Product Code | Start | End | INV Qty | Rate | Amount |
    """
    results: list[dict] = []
    rows = _parse_markdown_table(text)

    # Find header row
    header_idx = None
    for i, row in enumerate(rows):
        h = [c.lower() for c in row]
        if any("product code" in c for c in h) and any("inv qty" in c or "rate" in c for c in h):
            header_idx = i
            break
        if any("product code" in c for c in h) and any("amount" in c for c in h):
            header_idx = i
            break
    if header_idx is None:
        return results

    hdr = [c.lower() for c in rows[header_idx]]
    # Identify column positions flexibly
    try:
        i_prod = 0  # Product code always first
        # INV Qty may be 4th or 5th depending on whether start/end dates are present
        i_qty  = next(i for i, h in enumerate(hdr) if "inv qty" in h or (i > 1 and "qty" in h))
        i_rate = next(i for i, h in enumerate(hdr) if "rate" in h)
        i_amt  = next(i for i, h in enumerate(hdr) if "amount" in h)
    except StopIteration:
        return results

    for row in rows[header_idx + 1:]:
        if len(row) <= max(i_prod, i_qty, i_rate, i_amt):
            continue
        prod_cell = row[i_prod].strip()
        if not prod_cell:
            continue

        # Extract product code: token matching alphanumeric-dash pattern at start
        m = re.match(r"^([A-Z0-9][A-Z0-9\-]{3,})", prod_cell)
        if not m:
            # Page-2 continuation: some rows have product code in col 0 without description
            # e.g. "S1ES-CTL-EN-T9-SA" on its own line then description on next cell
            # Skip description-only continuation rows (they start with lowercase or spaces)
            continue
        sku  = m.group(1)
        desc = prod_cell[len(sku):].strip()
        # Description may continue in row[1] if the table structure splits across cells
        if not desc and len(row) > 1:
            desc = row[1].strip()

        qty  = _num(row[i_qty])
        rate = _num(row[i_rate])
        amt  = _num(row[i_amt])
        if qty is None and amt is None:
            continue
        results.append({"partner": None, "sku": sku, "description": desc,
                         "quantity": qty, "unit_price": rate, "amount": amt})
    return results


def _parse_webroot(text: str, file_path: str) -> list[dict]:
    """
    Webroot invoice format: table with item number at top of description cell.
    Table: rows where first cell contains a numeric SKU code; description follows.
    Columns inferred: | Item/SKU | Description | Qty | Unit Price | Price/Amount |
    """
    results: list[dict] = []
    rows = _parse_markdown_table(text)
    header_idx = None
    for i, row in enumerate(rows):
        h = [c.lower() for c in row]
        if any("qty" in c for c in h) and any("price" in c or "amount" in c for c in h):
            header_idx = i
            break
    if header_idx is None:
        return results

    hdr = [c.lower() for c in rows[header_idx]]
    try:
        i_desc = next(i for i, h in enumerate(hdr) if "description" in h or "item" in h)
        i_qty  = next(i for i, h in enumerate(hdr) if "qty" in h or "quantity" in h)
        i_up   = next(i for i, h in enumerate(hdr) if "unit" in h and "price" in h)
        i_amt  = next(i for i, h in enumerate(hdr) if h in ("price", "amount", "total", "ext"))
    except StopIteration:
        return results

    for row in rows[header_idx + 1:]:
        if len(row) <= max(i_desc, i_qty, i_up, i_amt):
            continue
        cell = row[i_desc].strip()
        if not cell:
            continue
        # SKU = first token (numeric or alphanumeric code), description = rest
        parts = cell.split("\n", 1)
        if len(parts) == 2:
            sku  = parts[0].strip()
            desc = parts[1].strip()
        else:
            # Try splitting on first whitespace after a code-like token
            m = re.match(r"^([A-Z0-9\-]+)\s+(.*)", cell, re.DOTALL)
            if m:
                sku, desc = m.group(1), m.group(2).strip()
            else:
                sku  = cell
                desc = cell
        qty  = _num(row[i_qty])
        up   = _num(row[i_up])
        amt  = _num(row[i_amt])
        if qty is None and amt is None:
            continue
        results.append({"partner": None, "sku": sku, "description": desc,
                         "quantity": qty, "unit_price": up, "amount": amt})
    return results


# ---------------------------------------------------------------------------
# Vendor router
# ---------------------------------------------------------------------------

PER_VENDOR_PARSERS: dict[str, Callable[[str, str], list[dict]]] = {
    "acronis":     _parse_acronis,
    "auvik":       _parse_auvik,
    "bitdefender": _parse_bitdefender,
    "eset":        _parse_eset,
    "exium":       _parse_exium,
    "keepit":      _parse_keepit,
    "proofpoint":  _parse_proofpoint,
    "sentinelone": _parse_sentinelone,
    "webroot":     _parse_webroot,
}


def _canonical_vendor(raw: str) -> str | None:
    if not raw:
        return None
    lc = raw.lower()
    for key, label in VENDOR_NAME_MAP.items():
        if key in lc:
            return label
    return raw.strip()


def _get_parser(vendor_label: str | None) -> Callable[[str, str], list[dict]]:
    if not vendor_label:
        return lambda t, f: []
    for key, fn in PER_VENDOR_PARSERS.items():
        if key in vendor_label.lower():
            return fn
    return lambda t, f: []


# ---------------------------------------------------------------------------
# Data fetch + transform
# ---------------------------------------------------------------------------

def fetch_raw(cur, vendor_filter: str | None, month_filter: str | None,
              from_month: str) -> list[tuple]:
    clauses = [f"FILE_PATH >= '{from_month.replace('-', '_')[:7].replace('-', '_')}/'"
               if not month_filter else ""]
    clauses = []
    if from_month and not month_filter:
        # file_path starts with YYYY_MM/ so '>= 2026_01/' works lexicographically
        tok = from_month[:7].replace("-", "_")
        clauses.append(f"FILE_PATH >= '{tok}/'")
    if vendor_filter:
        clauses.append(f"VENDOR_NAME ILIKE '%{vendor_filter.replace(chr(39), chr(39)*2)}%'")
    if month_filter:
        tok = month_filter[:7].replace("-", "_")
        clauses.append(f"FILE_PATH ILIKE '%{tok}%'")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT VENDOR_NAME, FILE_PATH, PARSED_DOCUMENT FROM NETSUITE.DBO.PARSED_VENDOR_DATA {where} ORDER BY FILE_PATH"
    print(f"Fetching invoice files ... ", end="", flush=True)
    cur.execute(sql)
    rows = cur.fetchall()
    print(f"{len(rows):,} files.")
    return rows


def parse_all(rows: list[tuple]) -> pd.DataFrame:
    records: list[dict] = []
    skipped = 0
    for vendor_name, file_path, parsed_doc in rows:
        vendor  = _canonical_vendor(vendor_name)
        billing = _month_from_path(file_path)
        if not billing:
            skipped += 1
            continue
        text   = _extract_text(parsed_doc)
        parser = _get_parser(vendor)
        items  = parser(text, file_path)
        for item in items:
            records.append({
                "BILLING_MONTH":      billing,
                "VENDOR":             vendor,
                "PARTNER":            item.get("partner"),
                "VENDOR_PRODUCT_SKU": (item.get("sku") or "UNKNOWN").strip(),
                "DESCRIPTION":        (item.get("description") or "").strip(),
                "QUANTITY":           item.get("quantity"),
                "UNIT_PRICE":         item.get("unit_price"),
                "AMOUNT":             item.get("amount"),
                "FILE_PATH":          file_path,
            })
    if skipped:
        print(f"  Skipped {skipped} files (no YYYY_MM/ billing month in path).")
    if not records:
        return pd.DataFrame(columns=[
            "BILLING_MONTH", "VENDOR", "PARTNER", "VENDOR_PRODUCT_SKU",
            "DESCRIPTION", "QUANTITY", "UNIT_PRICE", "AMOUNT", "FILE_PATH",
        ])
    df = pd.DataFrame(records)
    # Filter out rows with no meaningful data
    df = df[df["VENDOR_PRODUCT_SKU"].notna() & (df["VENDOR_PRODUCT_SKU"] != "UNKNOWN") |
            df["AMOUNT"].notna()]
    return df.reset_index(drop=True)


def print_summary(df: pd.DataFrame) -> None:
    if df.empty:
        print("\nNo line items extracted.")
        return
    by_vendor = (
        df.groupby("VENDOR", dropna=False)
        .agg(
            rows=("VENDOR", "size"),
            months=("BILLING_MONTH", "nunique"),
            partners=("PARTNER", "nunique"),
            skus=("VENDOR_PRODUCT_SKU", "nunique"),
            has_price=("UNIT_PRICE", lambda x: (x.notna() & (x > 0)).sum()),
            total_amount=("AMOUNT", "sum"),
        )
        .reset_index()
    )
    print(f"\n{'VENDOR':<15} {'ROWS':>7} {'MONTHS':>7} {'SKUS':>6} {'HAS_PRICE':>10} {'TOTAL_AMOUNT':>15}")
    print("-" * 68)
    for _, r in by_vendor.iterrows():
        amt = f"${r.total_amount:>13,.0f}" if pd.notna(r.total_amount) else "            N/A"
        print(f"{r.VENDOR:<15} {int(r.rows):>7,} {int(r.months):>7} {int(r.skus):>6,} "
              f"{int(r.has_price):>10,} {amt}")
    print(f"\nTotal rows: {len(df):,}")


# ---------------------------------------------------------------------------
# Validate against live table
# ---------------------------------------------------------------------------

def validate_against_live(cur, new_df: pd.DataFrame) -> None:
    print("\n" + "=" * 75)
    print("VALIDATION: parsed output vs live THIRD_PARTY_RECON_VENDOR_INVOICES")
    print("=" * 75)
    try:
        cur.execute("""
            SELECT VENDOR,
                   COUNT(*)                           AS row_cnt,
                   COUNT(DISTINCT VENDOR_PRODUCT_SKU) AS sku_cnt,
                   COUNT(DISTINCT BILLING_MONTH)      AS month_cnt,
                   SUM(AMOUNT)                        AS total_amount
            FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
            GROUP BY VENDOR ORDER BY VENDOR
        """)
        live = {r[0]: {"rows": int(r[1]), "skus": int(r[2]), "months": int(r[3]),
                       "amount": float(r[4] or 0)}
                for r in cur.fetchall()}
    except Exception as e:
        print(f"  Could not query live table: {e}")
        return

    new_map: dict = {}
    for vendor, grp in new_df.groupby("VENDOR", dropna=False):
        new_map[str(vendor)] = {
            "rows":   len(grp),
            "skus":   grp["VENDOR_PRODUCT_SKU"].nunique(),
            "amount": float(grp["AMOUNT"].sum()),
        }

    all_vendors = sorted(set(list(live.keys()) + list(new_map.keys())))
    print(f"\n{'VENDOR':<15} {'LIVE rows':>10} {'NEW rows':>10} {'ROW diff':>10} "
          f"{'LIVE $':>13} {'NEW $':>13} {'$ diff':>13}")
    print("-" * 90)
    ok = True
    for v in all_vendors:
        l  = live.get(v, {})
        n  = new_map.get(v, {})
        lr = l.get("rows", 0)
        nr = n.get("rows", 0)
        la = l.get("amount", 0.0)
        na = n.get("amount", 0.0)
        rd = nr - lr
        ad = na - la
        flag = "  " if abs(rd) == 0 and abs(ad) < 100 else "* "
        if flag.strip():
            ok = False
        print(f"{flag}{v:<13} {lr:>10,} {nr:>10,} {rd:>+10,} "
              f"${la:>12,.0f} ${na:>12,.0f} ${ad:>+12,.0f}")
    print()
    if ok:
        print("  All vendors match. Parser output is consistent with live table.")
    else:
        print("  * = differences. Review before writing. (New data > live = expected if new months loaded.)")

    # Show live table at a glance
    print("\nLive THIRD_PARTY_RECON_VENDOR_INVOICES:")
    cur.execute("""
        SELECT VENDOR, COUNT(*) AS rc,
               MIN(BILLING_MONTH) AS first_mo, MAX(BILLING_MONTH) AS last_mo,
               COUNT(DISTINCT VENDOR_PRODUCT_SKU) AS skus, SUM(AMOUNT) AS total
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
        GROUP BY VENDOR ORDER BY VENDOR
    """)
    print(f"{'VENDOR':<15} {'ROWS':>7} {'FIRST':>10} {'LAST':>10} {'SKUS':>6} {'TOTAL':>15}")
    print("-" * 68)
    for r in cur.fetchall():
        amt = f"${float(r[5]):>13,.0f}" if r[5] is not None else "            N/A"
        print(f"{r[0]:<15} {r[1]:>7,}  {str(r[2])[:10]}  {str(r[3])[:10]}  {r[4]:>6,} {amt}")
    print("=" * 75)


# ---------------------------------------------------------------------------
# Write to Snowflake
# ---------------------------------------------------------------------------

def write_to_snowflake(conn, cur, df: pd.DataFrame, append: bool) -> None:
    from snowflake.connector.pandas_tools import write_pandas

    if not append:
        print(f"\nRecreating {TARGET_TABLE} ({len(df):,} rows) ...")
        cur.execute(f"""
            CREATE OR REPLACE TABLE {TARGET_TABLE} (
                BILLING_MONTH      DATE,
                VENDOR             VARCHAR,
                PARTNER            VARCHAR,
                VENDOR_PRODUCT_SKU VARCHAR,
                DESCRIPTION        VARCHAR,
                QUANTITY           NUMBER(18,6),
                UNIT_PRICE         NUMBER(18,6),
                AMOUNT             NUMBER(18,6),
                FILE_PATH          VARCHAR
            )
        """)
    else:
        print(f"\nAppending {len(df):,} rows to {TARGET_TABLE} ...")

    success, n_chunks, n_rows, _ = write_pandas(
        conn, df,
        table_name="THIRD_PARTY_RECON_VENDOR_INVOICES",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
        auto_create_table=False,
        overwrite=False,
    )
    if success:
        print(f"  Loaded {n_rows:,} rows in {n_chunks} chunk(s).")
    else:
        raise RuntimeError("write_pandas reported failure.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run",  action="store_true", help="Parse and summarise; do NOT write.")
    p.add_argument("--validate", action="store_true", help="Compare vs live table; implies --dry-run.")
    p.add_argument("--inspect",  action="store_true", help="Print raw PDF text for matching rows.")
    p.add_argument("--append",   action="store_true", help="Append to existing table instead of replacing.")
    p.add_argument("--vendor",   default=None, help="Filter: VENDOR_NAME ILIKE '%%V%%'.")
    p.add_argument("--month",    default=None, help="Filter: file_path ILIKE '%%YYYY-MM%%'.")
    p.add_argument("--from",     dest="from_month", default=DEFAULT_FROM_MONTH,
                   help=f"Process months >= YYYY-MM (default: {DEFAULT_FROM_MONTH}).")
    args = p.parse_args()

    conn = get_snowflake_connection(
        role="DEVELOPER", warehouse="REPORTING_WH",
        database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
    )
    cur = conn.cursor()

    rows = fetch_raw(cur, args.vendor, args.month, args.from_month)

    if args.inspect:
        for vendor_name, fpath, doc in rows[:5]:
            print(f"\n{'='*70}\nVENDOR: {vendor_name}\nFILE:   {fpath}\n{'='*70}")
            text = _extract_text(doc)
            print(text[:4000] if text else "(empty)")
        if len(rows) > 5:
            print(f"\n... {len(rows)-5} more files. Narrow with --vendor and --month.")
        conn.close()
        return

    df = parse_all(rows)
    print_summary(df)

    if args.validate:
        validate_against_live(cur, df)
        print("\n[VALIDATE] No rows written.")
    elif args.dry_run:
        print("\n[DRY RUN] No rows written.")
    else:
        if df.empty:
            print("\nNothing to write.")
        else:
            write_to_snowflake(conn, cur, df, append=args.append)
            print("Done.")

    conn.close()


if __name__ == "__main__":
    main()
