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
            INVOICE_ID         VARCHAR
            INVOICE_DESCRIPTION VARCHAR
            NETSUITE_TRANSACTION_ID VARCHAR
            NETSUITE_URL       VARCHAR
      PARTNER            VARCHAR
            SOURCE_STREAM      VARCHAR
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
NETSUITE_BILL_URL_PREFIX = (
    "https://6230579.app.netsuite.com/app/accounting/transactions/vendbill.nl?id="
)

VENDOR_NAME_MAP: dict[str, str] = {
    "acronis":     "Acronis",
    "auvik":       "Auvik",
    "bitdefender": "Bitdefender",
    "eset":        "ESET",
    "exium":       "Exium",
    "keepit":      "KeepIT",
    "opentext":    "Webroot",
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


def _invoice_id(file_path: str, text: str, source_record_id: object) -> str:
    """Return a stable vendor invoice identifier without vendor-specific schema."""
    filename = str(file_path or "").rsplit("/", 1)[-1]
    patterns = (
        r"(?i)(INV[A-Z]*-?\d+[A-Z]?)",
        r"(?i)(CF[AM]I\d+)",
        r"(?i)\bBilling\s+Doc\.\s*#\s*:\s*([0-9]{5,})",
        r"(?i)(?:INVOICE[_ -])([0-9]{5,})",
        r"(?i)\bNumber\s*:?\s*([A-Z0-9-]{5,})",
        r"(?i)\bInvoice\s*(?:No\.?|Number|#)\s*:?\s*([A-Z0-9-]{5,})",
    )
    for source in (filename, text[:5000]):
        for pattern in patterns:
            match = re.search(pattern, source)
            if match:
                return match.group(1).upper()
    return f"SOURCE-{source_record_id}" if source_record_id is not None else filename


def _invoice_description(vendor: str | None, text: str, file_path: str) -> str:
    """Derive a concise invoice label for filtering and app display."""
    if str(vendor or "").upper() == "WEBROOT":
        return "Main"
    if str(vendor or "").upper() == "KEEPIT":
        if re.search(r"TAKEOUT", text, flags=re.IGNORECASE):
            return "Takeout"
        if re.search(r"\bMAIN\b", text, flags=re.IGNORECASE):
            return "Main"
    filename = str(file_path or "").rsplit("/", 1)[-1]
    return re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)


def _netsuite_url(source_record_id: object) -> str | None:
    """Return the direct NetSuite vendor-bill URL for a numeric transaction ID."""
    transaction_id = str(source_record_id or "").strip()
    if not re.fullmatch(r"\d+", transaction_id):
        return None
    return f"{NETSUITE_BILL_URL_PREFIX}{transaction_id}&whence="


def _webroot_source_stream(text: str) -> str | None:
    """Identify which Webroot usage stream an OpenText invoice bills."""
    header = text.split("|", 1)[0]
    if re.search(r"\b10551253\b|Continuum\s+Holdco", header, flags=re.IGNORECASE):
        return "CMS"
    if re.search(r"\b10309662\b|ConnectWise\s+LLC", header, flags=re.IGNORECASE):
        return "CW"
    return None


def _month_from_service_period(text: str | None) -> str | None:
    """Extract YYYY-MM-01 from service period text like 'Apr 1 2026 - Apr 30 2026'."""
    if not text:
        return None
    m = re.search(r"([A-Za-z]{3,9}\s+\d{1,2}\s+\d{4})", str(text))
    if not m:
        return None
    dtv = pd.to_datetime(m.group(1), errors="coerce")
    if pd.isna(dtv):
        return None
    return f"{int(dtv.year):04d}-{int(dtv.month):02d}-01"


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


def _num_sentinelone_qty(s: object) -> float | None:
    """Parse SentinelOne quantities with robust thousands-separator handling."""
    if s is None:
        return None
    t = str(s).strip().lstrip("$").replace(" ", "")
    if not t or t in ("-", "—"):
        return None

    # Explicit thousands separators written as 1,234 or 1.234 should be whole units.
    if re.match(r"^\d{1,3}([,.]\d{3})+$", t):
        t = re.sub(r"[,.]", "", t)
        try:
            return float(t)
        except ValueError:
            return None

    return _num(t)


def _month_from_keepit_description(desc: str | None, file_path: str) -> str | None:
    """Infer KeepIT billing month from description/service-period text."""
    if not desc:
        return None
    text = str(desc)

    # Most precise form in newer KeepIT files.
    # Example: "invoice billing period is from 01/01/2026 through 31/01/2026"
    m = re.search(
        r"billing\s+period\s+is\s+from\s+(\d{1,2}/\d{1,2}/\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        dtv = pd.to_datetime(m.group(1), format="%d/%m/%Y", errors="coerce")
        if not pd.isna(dtv):
            return f"{int(dtv.year):04d}-{int(dtv.month):02d}-01"

    # Older files often carry: "For December 2025 - Takeout" or "For February- Main".
    m = re.search(r"\bfor\s+([A-Za-z]{3,9})(?:\s+(\d{4}))?\s*-", text, flags=re.IGNORECASE)
    if not m:
        return None

    month_token = m.group(1)
    year_token = m.group(2)
    mo = pd.to_datetime(month_token[:3], format="%b", errors="coerce")
    if pd.isna(mo):
        return None

    if year_token:
        year = int(year_token)
    else:
        fallback = _month_from_path(file_path)
        if not fallback:
            return None
        year = int(fallback[:4])
        fallback_month = int(fallback[5:7])
        # NetSuite's January export contains the prior December invoice. When
        # the description omits a year, keep the inferred month chronologically
        # at or before the export folder instead of assigning next December.
        if int(mo.month) > fallback_month:
            year -= 1

    return f"{year:04d}-{int(mo.month):02d}-01"


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
        # Acronis quantities are US-formatted and often include comma thousands
        # separators with no decimal portion (e.g., 12,101). Using the
        # sentinel-style quantity parser avoids misreading these as 12.101.
        qty   = _num_sentinelone_qty(row[i_qty])
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
    seen_keys: set[tuple[str | None, str, float | None, float | None, float | None, str | None]] = set()
    current_partner: str | None = None

    def _add_row(
        *,
        partner: str | None,
        sku: str,
        qty: float | None,
        up: float | None,
        total: float | None,
        service_period: str | None,
    ) -> None:
        sku_clean = (sku or "").strip()
        if not sku_clean or total is None or qty is None or up is None:
            return
        # Document Intelligence can wrap the final digit of a large quantity
        # onto the next table row (for example ``2,358,64`` then ``4``). Auvik
        # invoice lines are quantity × unit price, so recover only a near-whole
        # implied quantity that is materially larger than the parsed value.
        if abs(up) > 1e-12:
            implied_qty = total / up
            rounded_qty = round(implied_qty)
            if (
                abs(implied_qty - rounded_qty) < 0.05
                and abs(total - qty * up) > max(0.02, abs(total) * 0.001)
                and abs(rounded_qty) > abs(qty) * 1.5
            ):
                qty = float(rounded_qty)
        # Canonical dedupe key guards against overlap between markdown-table
        # extraction and freeform page-continuation extraction.
        key = (
            (partner or "").strip() or None,
            sku_clean,
            qty,
            up,
            total,
            _month_from_service_period(service_period),
        )
        if key in seen_keys:
            return
        seen_keys.add(key)
        results.append(
            {
                "partner": partner,
                "sku": sku_clean,
                "description": sku_clean,
                "quantity": qty,
                "unit_price": up,
                "amount": total,
                "billing_month": _month_from_service_period(service_period),
            }
        )

    # Some Auvik OCR pages emit valid table rows without a leading pipe, e.g.
    # "ANM Essentials - Evergreen | | Apr 1 2026 - Apr 30 2026 | ...".
    # Recover those rows before markdown-table parsing.
    normalized_lines: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if (
            s
            and "|" in s
            and not s.startswith("|")
            and re.search(r"[A-Za-z]{3,9}\s+\d{1,2}\s+\d{4}\s*-\s*[A-Za-z]{3,9}\s+\d{1,2}\s+\d{4}", s)
        ):
            normalized_lines.append(f"| {s}")
        else:
            normalized_lines.append(ln)
    rows = _parse_markdown_table("\n".join(normalized_lines))
    for row in rows:
        if not row:
            continue
        first = row[0].strip()

        # Account header row: first cell = "Account: <partner name>"
        acc_match = re.match(r"Account:\s*(.+)", first, re.IGNORECASE)
        if acc_match:
            acc_payload = acc_match.group(1).strip()
            acc_lines = [ln.strip() for ln in acc_payload.splitlines() if ln.strip()]
            current_partner = acc_lines[0] if acc_lines else acc_payload

            # Some Auvik rows collapse "Account: <name>" and first SKU into
            # one cell separated by a newline. Preserve that first SKU line.
            if len(acc_lines) > 1 and len(row) >= 4:
                inline_sku = " ".join(acc_lines[1:]).strip()
                _add_row(
                    partner=current_partner,
                    sku=inline_sku,
                    qty=_num_sentinelone_qty(row[-3]),
                    up=_num(row[-2]),
                    total=_num(row[-1]),
                    service_period=row[-4],
                )
            continue

        # Skip: header rows, dividers, and invoice metadata rows
        first_lc = first.lower()
        if "charge description" in first_lc or not first:
            continue
        # Skip invoice/remittance metadata rows: these are non-billing tables at top/bottom of Auvik invoices
        if re.match(r"^(account name|account number|bank|routing|iban|swift|wire|remit|"
                    r"invoice number|invoice date|due date|payment terms|payment method|po number|"
                r"bill to|ship to|subtotal|tax total|total|please|canada gst/hst|gst/hst)",
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
        qty   = _num_sentinelone_qty(row[-3])
        service_period = row[-4] if len(row) >= 4 else None
        _add_row(
            partner=current_partner,
            sku=first,
            qty=qty,
            up=up,
            total=total,
            service_period=service_period,
        )

    # Fallback for page-break continuations where a line item may appear
    # outside markdown table rows (no '|' delimiters), e.g. trailing overage
    # line followed by Amount Total footer.
    raw_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    fallback_partner: str | None = None
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        account_match = re.search(r"Account:\s*([^|]+)", line, re.IGNORECASE)
        if account_match:
            fallback_partner = account_match.group(1).strip()

        # Only treat non-table text as fallback candidates to avoid duplicates.
        if "|" not in line and i + 4 < len(raw_lines):
            period = raw_lines[i + 1]
            qty_txt = raw_lines[i + 2]
            up_txt = raw_lines[i + 3]
            total_txt = raw_lines[i + 4]
            if _month_from_service_period(period) and _num(qty_txt) is not None and _num(up_txt) is not None and _num(total_txt) is not None:
                first_lc = line.lower()
                if not re.match(
                    r"^(amount total|subtotal|tax|total|currency|invoice|due date|payment|po number|use of auvik|account name|bank|routing|iban|swift)",
                    first_lc,
                ):
                    _add_row(
                        partner=fallback_partner,
                        sku=line,
                        qty=_num_sentinelone_qty(qty_txt),
                        up=_num(up_txt),
                        total=_num(total_txt),
                        service_period=period,
                    )
                    i += 5
                    continue
        i += 1
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
        qty  = _num(row[i_qty])
        # Skip repeated page-header rows ("Item number" / "Description") which
        # have no numeric quantity.
        if qty is None:
            continue
        raw_desc = row[i_desc].strip()
        # Strip "Order number : ..." and everything after
        desc = re.split(r"\s*/\s*" + re.escape(sku), raw_desc, maxsplit=1)[0].strip()
        desc = re.split(r"Order\s+number\s*:", desc, flags=re.IGNORECASE)[0].strip()
        # Clean up slash-separated duplicate SKU entries "BP_2773 : : 1 : 315150 : BP_2773"
        desc = re.split(r"\s*:\s*:\s*\d+\s*:", desc, maxsplit=1)[0].strip()
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

    # ESET has multiple invoice table layouts (US/AU/UK) and may repeat headers
    # across pages. We detect each header and parse rows until the next header.
    active_layout: str | None = None
    idx_item: int | None = None
    idx_desc: int | None = None

    for row in rows:
        cells = [str(c if c is not None else "").strip() for c in row]
        if not cells:
            continue
        lowered = [c.lower() for c in cells]

        # Layout A (US/AU): ITEM | DESCRIPTION | ... | RATE | NET PRICE
        if any(c == "item" for c in lowered) and any("description" in c for c in lowered) and any("rate" in c for c in lowered):
            active_layout = "item_desc"
            idx_item = next((i for i, c in enumerate(lowered) if c == "item"), None)
            idx_desc = next((i for i, c in enumerate(lowered) if "description" in c), None)
            continue

        # Layout B (UK): DESCRIPTION | Quantity (SEAT DAYS) | RATE | NET PRICE
        if any("description" in c for c in lowered) and any("quantity" in c or "seat" in c for c in lowered) and any("net price" in c or c == "net" for c in lowered):
            active_layout = "desc_only"
            idx_item = None
            idx_desc = next((i for i, c in enumerate(lowered) if "description" in c), None)
            continue

        if active_layout is None:
            continue
        if idx_desc is None or idx_desc >= len(cells):
            continue

        # Skip summary and schema rows.
        first_cell = cells[idx_desc] if idx_desc is not None else cells[0]
        if not first_cell:
            continue
        if re.match(r"^(subscription name|invoice summary|subtotal|tax amount|total)\b", first_cell, flags=re.IGNORECASE):
            continue

        # Robust numeric extraction: take right-most 3 numeric values as
        # quantity, unit price, net amount respectively.
        numeric_values: list[float] = []
        for c in cells:
            v = _num(c)
            if v is not None:
                numeric_values.append(v)
        if len(numeric_values) < 3:
            continue
        qty, rate, net = numeric_values[-3], numeric_values[-2], numeric_values[-1]

        if active_layout == "item_desc" and idx_item is not None and idx_item < len(cells):
            sku = cells[idx_item].strip()
            desc = cells[idx_desc].strip()
            partner = None
            if "," in desc:
                partner = desc.split(",", 1)[0].strip() or None
            if not sku:
                continue
        else:
            # UK layout puts partner and product in DESCRIPTION as
            # "<line_no> <partner>, <product>".
            raw_desc = cells[idx_desc].strip()
            raw_desc = re.sub(r"^\d+\s+", "", raw_desc)
            partner = None
            product = raw_desc
            if "," in raw_desc:
                left, right = raw_desc.split(",", 1)
                partner = left.strip() or None
                product = right.strip() or raw_desc
            sku = f"MSP - {product}" if not product.upper().startswith("MSP -") else product
            desc = raw_desc

        results.append({
            "partner": partner,
            "sku": sku,
            "description": desc,
            "quantity": qty,
            "unit_price": rate,
            "amount": net,
        })

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
        i_up   = next(i for i, h in enumerate(hdr) if "unit price" in h)
        i_amt  = next(i for i, h in enumerate(hdr) if "total incl" in h or "total" in h)
    except StopIteration:
        return results

    seen_keys: set[tuple] = set()

    for row in rows[header_idx + 1:]:
        if len(row) <= max(i_sku, i_desc, i_qty, i_up, i_amt):
            continue
        sku  = row[i_sku].strip()
        desc = row[i_desc].strip()
        sku_upper = sku.upper()
        if (
            not sku
            or sku_upper.startswith("VAT")
            or sku_upper == "SKU"
            or re.match(r"^\d+(?:\.\d+)?%$", sku)
            or not sku_upper.startswith("KI-")
        ):
            continue
        qty  = _num_sentinelone_qty(row[i_qty])
        up   = _num(row[i_up])
        amt  = _num(row[i_amt])
        if qty and up and amt and qty > 0:
            # KeepIT tables sometimes parse thousand-separated quantities as decimals
            # (e.g. 58.849 instead of 58849). Correct when arithmetic indicates a
            # 10^3 quantity scale error.
            if qty < 1000 and amt >= 1000:
                if up >= 100 and abs((qty * up) - amt) <= max(0.01, abs(amt) * 0.01):
                    qty *= 1000
                    up /= 1000
                elif abs((qty * 1000 * up) - amt) <= max(0.01, abs(amt) * 0.02):
                    qty *= 1000
        if qty and up and amt and qty > 0:
            expected = qty * up
            if expected > 0:
                ratio = amt / expected
                if ratio > 0:
                    # KeepIT OCR occasionally shifts decimal place; correct by powers of 10.
                    for power in (1, 2, 3):
                        if abs(ratio - (10 ** power)) < 0.02:
                            up *= 10 ** power
                            break
                        if abs(ratio - (10 ** (-power))) < 0.02:
                            up *= 10 ** (-power)
                            break
        # KeepIT occasionally emits retrospective price-adjustment invoices as
        # amount-only lines (no qty / no unit price). These do not reconcile to
        # raw usage-seat feeds and should not be treated as usage line items.
        if qty is None and up is None:
            continue
        if qty is None and amt is None:
            continue

        inferred_month = _month_from_keepit_description(desc, file_path)
        dedupe_key = (
            inferred_month,
            sku_upper,
            desc.upper(),
            None if qty is None else round(float(qty), 6),
            None if up is None else round(float(up), 6),
            None if amt is None else round(float(amt), 6),
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        results.append({
            "partner": None,
            "sku": sku,
            "description": desc,
            "quantity": qty,
            "unit_price": up,
            "amount": amt,
            "billing_month": inferred_month,
        })
    return results


def _parse_proofpoint(text: str, file_path: str) -> list[dict]:
    """
    Proofpoint invoices use multi-line description cells in their markdown tables.
    Each item block looks like:
      |  PP-xxx | <description (may span many lines)>
      Contract end: DATE | QTY | EA | (unit_price) | $AMOUNT |
    or single-line:
      |  PP-xxx | Description | QTY | EA | | $AMOUNT |

    We scan the full text with a regex (DOTALL) to handle both formats.
    """
    results: list[dict] = []
    # Matches: | PP-SKU | <anything multi-line> | qty | EA | unit_price_or_blank | $amount |
    pattern = re.compile(
        r'\|\s+(PP-[\w-]+)\s*\|'          # | PP-SKU |
        r'(.*?)'                           # description (non-greedy, multi-line)
        r'\|\s*([\d,]+(?:\.\d+)?)\s*'     # | qty (may have commas)
        r'\|\s*EA\s*\|'                    # | EA |
        r'[^|]*\|'                         # unit_price column (skip, usually blank)
        r'\s*\$?([\d,]+(?:\.\d+)?)\s*\|', # | $amount |
        re.DOTALL,
    )
    for m in pattern.finditer(text):
        sku  = m.group(1).strip()
        # Collapse multi-line description; strip trailing contract dates
        raw_desc = re.sub(r'\s+', ' ', m.group(2)).strip().rstrip('|').strip()
        desc = re.split(r'Contract\s+start\s*:', raw_desc, flags=re.IGNORECASE)[0].strip()
        desc = desc.rstrip('|').strip()
        qty  = _num(m.group(3))
        amt  = _num(m.group(4))
        if qty is None:
            continue
        up = round(amt / qty, 6) if (amt is not None and qty != 0) else None
        results.append({"partner": None, "sku": sku, "description": desc,
                        "quantity": qty, "unit_price": up, "amount": amt})
    return results


def _parse_sentinelone(text: str, file_path: str) -> list[dict]:
    """
    SentinelOne invoices have TWO table formats across pages:

    Page 1 (7-column header — extra empty column after Product Code):
      | Product Code | (empty) | Start Date | End Date | INV Qty | Rate | Amount |
      Data rows: SKU + multi-line description packed into cell 0; all 7 cols present.

    Page 2 (6-column header — no empty column):
      | Product Code | Start Date | End Date | INV Qty | Rate | Amount |
      Data rows: SKU in one row; description in the NEXT row (all other cells blank).

    Strategy: re-detect the header every time we see a "Product Code ... INV Qty" row
    so column indices stay correct per-page.
    """
    results: list[dict] = []

    # Page-1 rows can wrap the Product Code cell over multiple lines before the
    # trailing qty/rate/amount columns. Collapse each wrapped row into one line.
    normalized_lines: list[str] = []
    block_parts: list[str] = []
    in_wrapped_row = False
    row_start_re = re.compile(r"^\|\s*[A-Z0-9]+(?:-[A-Z0-9]+)+\s*$")
    row_end_re = re.compile(r"\|\s*\$?[\d,]+(?:\.\d+)?\s*\|\s*$")

    for ln in text.splitlines():
        s = ln.rstrip()
        if not in_wrapped_row:
            if row_start_re.match(s):
                in_wrapped_row = True
                block_parts = [s]
            else:
                normalized_lines.append(ln)
            continue

        block_parts.append(s)
        if row_end_re.search(s):
            collapsed = " ".join(part.strip() for part in block_parts if part.strip())
            normalized_lines.append(collapsed)
            in_wrapped_row = False
            block_parts = []

    if block_parts:
        normalized_lines.append(" ".join(part.strip() for part in block_parts if part.strip()))

    rows = _parse_markdown_table("\n".join(normalized_lines))

    header_idx: tuple[int, int, int, int] | None = None
    last_line_item_idx: int | None = None

    for row in rows:
        cells = [str(c if c is not None else "").strip() for c in row]
        if not cells:
            continue

        lowered = [c.lower() for c in cells]

        # Re-detect table headers per page; page 1 and page 2 use different widths.
        if (
            any("product code" in c for c in lowered)
            and any("inv qty" in c or c == "qty" for c in lowered)
            and any(c == "rate" for c in lowered)
            and any("amount" in c for c in lowered)
        ):
            try:
                i_code = next(i for i, c in enumerate(lowered) if "product code" in c)
                i_qty = next(i for i, c in enumerate(lowered) if "inv qty" in c or c == "qty")
                i_rate = next(i for i, c in enumerate(lowered) if c == "rate")
                i_amt = next(i for i, c in enumerate(lowered) if "amount" in c)
                header_idx = (i_code, i_qty, i_rate, i_amt)
                last_line_item_idx = None
            except StopIteration:
                header_idx = None
            continue

        if header_idx is None:
            continue

        i_code, i_qty, i_rate, i_amt = header_idx
        max_i = max(i_code, i_qty, i_rate, i_amt)
        if len(cells) <= max_i:
            continue

        code_cell = cells[i_code]
        qty = _num_sentinelone_qty(cells[i_qty])
        rate = _num(cells[i_rate])
        amt = _num(cells[i_amt])

        # Continuation rows on page 2 carry description only in Product Code column.
        if qty is None and rate is None and amt is None:
            if last_line_item_idx is not None and code_cell:
                desc_text = re.sub(r"\s+", " ", code_cell).strip()
                if desc_text and not re.match(r"^(subtotal|tax total|total)\b", desc_text, flags=re.IGNORECASE):
                    prior_desc = results[last_line_item_idx].get("description") or ""
                    joined = f"{prior_desc} {desc_text}".strip() if prior_desc else desc_text
                    results[last_line_item_idx]["description"] = joined
            continue

        if qty is None:
            # Not a real invoice line item.
            continue

        # Product Code cell may include SKU + multiline description on page 1.
        code_text = re.sub(r"\s+", " ", code_cell).strip()
        m_sku = re.match(r"^([A-Z0-9]+(?:-[A-Z0-9]+)+)\b\s*(.*)$", code_text)
        if not m_sku:
            # Prevent false captures like 'MSSP Overage'.
            continue
        sku = m_sku.group(1).strip()
        desc = m_sku.group(2).strip()

        # OCR occasionally yields shifted separators (e.g., 737,484 -> 737.484).
        # If qty*rate is wildly off amount, apply a power-of-10 correction.
        if qty and rate and amt and amt > 0:
            rel_err = abs((qty * rate) - amt) / amt
            if rel_err > 0.95:
                for factor in (10.0, 100.0, 1000.0):
                    candidate = qty * factor
                    cand_err = abs((candidate * rate) - amt) / amt
                    if cand_err < 0.02:
                        qty = candidate
                        break

        results.append({
            "partner": None,
            "sku": sku,
            "description": desc,
            "quantity": qty,
            "unit_price": rate,
            "amount": amt,
        })
        last_line_item_idx = len(results) - 1

    return results


def _parse_webroot(text: str, file_path: str) -> list[dict]:
    """Parse OpenText invoices that carry the Webroot product portfolio."""
    results: list[dict] = []
    source_stream = _webroot_source_stream(text)
    rows = _parse_markdown_table(text)
    header_idx = None
    for i, row in enumerate(rows):
        h = [c.strip().lower() for c in row]
        if (
            any(c in ("qty", "quantity") for c in h)
            and "description" in h
            and "unit price" in h
            and "price" in h
        ):
            header_idx = i
            break
    if header_idx is None:
        return results

    hdr = [c.strip().lower() for c in rows[header_idx]]
    try:
        i_desc = hdr.index("description")
        i_qty = next(i for i, h in enumerate(hdr) if h in ("qty", "quantity"))
        i_up = hdr.index("unit price")
        i_amt = hdr.index("price")
    except StopIteration:
        return results
    except ValueError:
        return results

    for row in rows[header_idx + 1:]:
        if len(row) <= max(i_desc, i_qty, i_up, i_amt):
            continue
        cell = row[i_desc].strip()
        if not cell:
            continue

        # OpenText uses a stable ten-digit SKU followed by the product label,
        # service period, contract metadata, and end-user metadata. Preserve
        # only the actual product label in DESCRIPTION.
        match = re.match(r"^(\d{10})\s+(.+)$", cell, flags=re.DOTALL)
        if not match:
            continue
        sku = match.group(1)
        desc = re.split(
            r"\s+\d{4}-\d{2}-\d{2}\s+to\s+\d{4}-\d{2}-\d{2}\b",
            match.group(2),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()

        qty = _num_sentinelone_qty(row[i_qty])
        up = _num(row[i_up])
        amount_text = re.sub(r"(?i)\s+[A-Z]{3}\s*$", "", row[i_amt].strip())
        amt = _num(amount_text)
        if qty is None or up is None or amt is None:
            continue
        results.append({
            "partner": None,
            "source_stream": source_stream,
            "sku": sku,
            "description": desc,
            "quantity": qty,
            "unit_price": up,
            "amount": amt,
        })
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
    sql = f"SELECT TRANSACTION_ID, VENDOR_NAME, FILE_PATH, PARSED_DOCUMENT FROM NETSUITE.DBO.PARSED_VENDOR_DATA {where} ORDER BY FILE_PATH"
    print(f"Fetching invoice files ... ", end="", flush=True)
    cur.execute(sql)
    rows = cur.fetchall()
    print(f"{len(rows):,} files.")
    return rows


def parse_all(rows: list[tuple]) -> pd.DataFrame:
    records: list[dict] = []
    skipped = 0
    for source_record_id, vendor_name, file_path, parsed_doc in rows:
        vendor  = _canonical_vendor(vendor_name)
        billing = _month_from_path(file_path)
        if not billing:
            skipped += 1
            continue
        text   = _extract_text(parsed_doc)
        invoice_id = _invoice_id(file_path, text, source_record_id)
        invoice_description = _invoice_description(vendor, text, file_path)
        netsuite_transaction_id = str(source_record_id or "").strip() or None
        parser = _get_parser(vendor)
        items  = parser(text, file_path)
        for item in items:
            records.append({
                "BILLING_MONTH":      item.get("billing_month") or billing,
                "VENDOR":             vendor,
                "INVOICE_ID":         invoice_id,
                "INVOICE_DESCRIPTION": invoice_description,
                "NETSUITE_TRANSACTION_ID": netsuite_transaction_id,
                "NETSUITE_URL":       _netsuite_url(source_record_id),
                "PARTNER":            item.get("partner"),
                "SOURCE_STREAM":      item.get("source_stream"),
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
            "BILLING_MONTH", "VENDOR", "INVOICE_ID", "INVOICE_DESCRIPTION",
            "NETSUITE_TRANSACTION_ID", "NETSUITE_URL",
            "PARTNER", "SOURCE_STREAM", "VENDOR_PRODUCT_SKU",
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
                INVOICE_ID         VARCHAR,
                INVOICE_DESCRIPTION VARCHAR,
                NETSUITE_TRANSACTION_ID VARCHAR,
                NETSUITE_URL       VARCHAR,
                PARTNER            VARCHAR,
                SOURCE_STREAM      VARCHAR,
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
