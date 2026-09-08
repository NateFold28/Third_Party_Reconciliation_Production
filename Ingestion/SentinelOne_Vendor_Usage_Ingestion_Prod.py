"""Ingest SentinelOne ConnectWise usage XLSX files into Snowflake.

Source layout:
    <SOURCE_ROOT>/MM_MON_YYYY/ConnectWise Usage_*.xlsx

Each workbook holds a per-site paid-detail sheet in wide form (one column per
agent product). This script melts the wide agent columns into long rows and
resolves the canonical vendor product SKU (sku_match_group) in a single step
so the target table has the same grain and schema as all other vendor pipelines.

Canonical vendor usage schema (shared across all vendors):

    BILLING_MONTH, VENDOR, VENDOR_PARTNER_NAME, VENDOR_PRODUCT_SKU, MODIFIER,
    QUANTITY, UNIT_PRICE, AMOUNT, CURRENCY

VENDOR_PRODUCT_SKU resolution rules (baked in at ingestion, no downstream remap):
    * ``Total Active Agents per site`` rows: VENDOR_PRODUCT_SKU = Sku value
      (Complete / Control / Core). The raw "Total Active Agents" label is
      replaced by the tier name so downstream joins use the sku_match_group
      directly.
    * ``Data Retention`` rows: VENDOR_PRODUCT_SKU = "Data Retention - " + Retention Days
      (e.g. "Data Retention - 180 Days").
    * All other agent columns (Ranger, Purple AI, etc.): VENDOR_PRODUCT_SKU =
      cleaned product name (already the sku_match_group in the SKU map).

Semantics:
    * Vendor grand-total footer rows (blank Site Account Name / Sku) are filtered.
    * Partners whose agent columns sum to zero across every row are excluded.
    * UNIT_PRICE is loaded from the governed invoice-rate seed by
      VENDOR_PRODUCT_SKU / sku_match_group, and AMOUNT = QUANTITY * UNIT_PRICE.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import openpyxl
import pandas as pd

# ---------------------------------------------------------------------------
# Paths & Snowflake target
# ---------------------------------------------------------------------------

SENTINELONE_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = next((p for p in (SENTINELONE_ROOT, *SENTINELONE_ROOT.parents) if (p / "TEMPLATES").exists()), SENTINELONE_ROOT)
OUTPUT_DIR = SENTINELONE_ROOT / "outputs"

DEFAULT_SOURCE_ROOT = Path(
    r"C:\Users\Nate.Fold\OneDrive - ConnectWise, Inc"
    r"\THIRD_PARTY_RECONCILIATION\2026 - Vendor Files\SentinelOne"
)

TARGET_DATABASE = "ANALYTICS_DEV"
TARGET_SCHEMA = "DBT_NFOLD_TRANSFORMATION"
TARGET_TABLE = "THIRD_PARTY_RECON_VENDOR_USAGE_PROD"
FQN = f"{TARGET_DATABASE}.{TARGET_SCHEMA}.{TARGET_TABLE}"
TARGET_VENDOR = "SentinelOne"

# ---------------------------------------------------------------------------
# Vendor workbook contract
# ---------------------------------------------------------------------------

MONTH_FOLDER_RE = re.compile(r"^(?P<mm>\d{2})_[A-Z]{3}_(?P<yyyy>\d{4})$", re.IGNORECASE)

EXPECTED_SHEET_NAME = "Rich CW Paid Site Detail Report"
HEADER_SEARCH_ROWS = 7           # look for the header within the top N rows
SCHEMA_MATCH_THRESHOLD = 3       # >= this many canonical headers must match

SITE_ACCOUNT_COL = "Site Account Name"
SKU_COL = "Sku"
CREATED_DATE_COL = "Created Date"
ACCOUNT_COL = "Account Name"
SITE_TYPE_COL = "Site Type"
SITE_ID_COL = "Site ID"
TOTAL_AGENTS_COL = "Total Active Agents per site"
RETENTION_COL = "Retention Days"

# Retention column labels drift across workbooks; anything starting with
# "retention" (case-insensitive, non-alphanumerics stripped) collapses to the
# canonical column name.
RETENTION_ALIASES: frozenset[str] = frozenset(
    {"retentiondays", "retentiondata", "retentiondescription", "retentiondesc", "retention"}
)

# Cleaned product-label used when the base-agent roll-up is emitted as a row.
TOTAL_AGENTS_PRODUCT_LABEL = "Total Active Agents"

# Agent columns that don't follow the "*Agent(s)*" naming convention.
KNOWN_AGENT_EXTRAS: tuple[str, ...] = ("Ranger AD Full", "Ranger AD Protect Full")
EXPECTED_AGENT_COLUMNS: tuple[str, ...] = (
    TOTAL_AGENTS_COL,
    "Ranger Active Agents",
    "Vigilance Active Agents",
    "RSO Active Agents",
    "Watchtower Active Agents",
    "Cloud Funnel Active Agents",
    "Forensics Active Agents",
    "Ranger Insights Active Agents",
    "Ranger AD Full",
    "Ranger AD Protect Full",
    "Singularity Identity Agents",
    "Purple AI Agents",
    "Threat Intelligence Agents",
    "Data Retention Agents",
)

# Canonical vendor usage schema â€” shared across all third-party pipelines.
TEMPLATE_COLUMNS: tuple[str, ...] = (
    "BILLING_MONTH",
    "VENDOR",
    "VENDOR_PARTNER_NAME",
    "VENDOR_PRODUCT_SKU",
    "MODIFIER",
    "QUANTITY",
    "UNIT_PRICE",
    "AMOUNT",
    "CURRENCY",
    "ADDITIONAL_INFO",
)

AUDIT_COLUMNS: tuple[str, ...] = (
    "SOURCE_FOLDER",
    "SOURCE_FILE",
    "SOURCE_CONTENT_HASH",
    "BILLING_MONTH",
    "SNAPSHOT_DATE",
    "SOURCE_ROWS",
    "PARTNERS",
    "SITES",
    "OUTPUT_ROWS",
    "DUPLICATE_SITE_IDS",
    "FOOTER_TOTAL_CHECK",
    "MISSING_RATE_ROWS",
    "MISSING_RATE_PRODUCTS",
    "IGNORED_FILES",
    "ERROR_MESSAGE",
    "INGESTED_AT",
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _norm(value: object) -> str:
    """Lowercase and strip non-alphanumerics so headers match tolerantly."""
    return re.sub(r"[^a-z0-9]", "", str(value if value is not None else "").lower())


def _clean_product_name(col_name: object) -> str:
    """Convert an agent column header into a product/sku label.

    Examples:
        "Ranger Active Agents"          -> "Ranger"
        "Purple AI Agents"              -> "Purple AI"
        "Ranger AD Full"                -> "Ranger AD"
        "Ranger AD Protect Full"        -> "Ranger AD Protect"
        "Total Active Agents per site"  -> "Total Active Agents"
    """
    s = re.sub(r"\s+", " ", str(col_name).replace("\xa0", " ")).strip()
    if _norm(s) == _norm(TOTAL_AGENTS_COL):
        return TOTAL_AGENTS_PRODUCT_LABEL
    s = re.sub(r"\s+Active Agents$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+Agents$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+Full$", "", s, flags=re.IGNORECASE)
    return s.strip()


def _to_first_of_month(value: object) -> dt.date | None:
    """Truncate a Created Date value to the first day of its month."""
    if value is None or bool(pd.isna(value)):
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        return dt.date(value.year, value.month, 1)
    parsed = pd.to_datetime(str(value), errors="coerce")
    if pd.isna(parsed):
        return None
    ts: pd.Timestamp = parsed  # type: ignore[assignment]
    return dt.date(ts.year, ts.month, 1)


def _sku_rate_key(vendor_product_sku: object) -> str:
    """Map the ingestion product label to the invoice-rate seed key."""
    product = str(vendor_product_sku if vendor_product_sku is not None else "").strip()
    product_upper = product.upper()
    if product_upper.startswith("DATA RETENTION"):
        match = re.search(r"\b(30|90|180|365)\b", product_upper)
        if match:
            return f"DATA_RETENTION_{match.group(1)}"
    return re.sub(r"_+", "_", re.sub(r"[^A-Z0-9]+", "_", product_upper)).strip("_")


def _load_usage_product_alias_map() -> dict[str, str]:
    """Return usage product key -> canonical sku_match_group key.

    Example: RSO -> REMOTEOPS.
    """
    import sys as _sys
    _sys.path.insert(0, str(WORKSPACE_ROOT))
    candidates: dict[str, set[str]] = {}
    try:
        from TEMPLATES.Python.connection import get_snowflake_connection as _conn
        conn = _conn(role="DEVELOPER", warehouse="REPORTING_WH",
                     database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION")
        rows = conn.cursor().execute("""
            SELECT DISTINCT VENDOR_PRODUCT, SKU_MATCH_KEY
            FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SKU_MAP_PROD
            WHERE UPPER(COALESCE(VENDOR, '')) = 'SENTINELONE'
              AND NULLIF(TRIM(VENDOR_PRODUCT), '') IS NOT NULL
              AND NULLIF(TRIM(SKU_MATCH_KEY), '') IS NOT NULL
        """).fetchall()
        conn.close()
        for vendor_product, sku_match_key in rows:
            vp_key = _sku_rate_key(vendor_product)
            sm_key = _sku_rate_key(sku_match_key)
            if vp_key and sm_key:
                candidates.setdefault(vp_key, set()).add(sm_key)
    except Exception as e:
        print(f"[WARN] Could not load SentinelOne usage alias map ({e}).", flush=True)

    aliases: dict[str, str] = {}
    for vp_key, options in candidates.items():
        # Prefer business/canonical keys over internal S1_* variants.
        picked = sorted(options, key=lambda k: (k.startswith("S1_"), k))[0]
        aliases[vp_key] = picked
    return aliases


def _resolve_month_rate(
    canonical_key: str,
    bill_month: dt.date,
    rate_history: dict[str, dict[dt.date, float]],
) -> float | None:
    """Return exact-month rate, else most recent prior-month rate."""
    month_map = rate_history.get(canonical_key)
    if not month_map:
        return None
    if bill_month in month_map:
        return month_map[bill_month]
    eligible = [m for m in month_map.keys() if m <= bill_month]
    if not eligible:
        return None
    return month_map[max(eligible)]


def load_invoice_rate_history() -> dict[str, dict[dt.date, float]]:
    """Return canonical key -> {billing_month: unit_price} from vendor invoices.

    Uses invoice month-specific rates and supports prior-month fallback.
    Canonical keys are sourced from THIRD_PARTY_RECON_SKU_MAP_PROD
    (invoice SKU -> SKU_MATCH_KEY).
    """
    import sys as _sys
    _sys.path.insert(0, str(WORKSPACE_ROOT))
    try:
        from TEMPLATES.Python.connection import get_snowflake_connection as _conn
        conn = _conn(role="DEVELOPER", warehouse="REPORTING_WH",
                     database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION")
        rows = conn.cursor().execute("""
            WITH inv AS (
                SELECT
                    i.BILLING_MONTH::DATE AS BILLING_MONTH,
                    i.VENDOR_PRODUCT_SKU,
                    NULLIF(i.UNIT_PRICE, 0) AS UNIT_PRICE,
                    i.QUANTITY,
                    i.AMOUNT
                FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES i
                WHERE i.VENDOR ILIKE '%sentinelone%'
                  AND i.UNIT_PRICE IS NOT NULL
                  AND i.UNIT_PRICE > 0
            ),
            sku_map_prod AS (
                SELECT DISTINCT
                    UPPER(TRIM(VENDOR_SKU)) AS VENDOR_INVOICE_SKU_KEY,
                    SKU_MATCH_KEY
                FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SKU_MAP_PROD
                WHERE UPPER(COALESCE(VENDOR, '')) = 'SENTINELONE'
                  AND NULLIF(TRIM(VENDOR_SKU), '') IS NOT NULL
                  AND NULLIF(TRIM(SKU_MATCH_KEY), '') IS NOT NULL
            )
            SELECT
                inv.BILLING_MONTH,
                sku_map_prod.SKU_MATCH_KEY,
                CASE
                    WHEN COUNT(DISTINCT inv.UNIT_PRICE) = 1 THEN MAX(inv.UNIT_PRICE)
                    WHEN SUM(IFF(inv.QUANTITY IS NOT NULL AND inv.QUANTITY > 0, inv.QUANTITY, 0)) > 0
                        THEN SUM(IFF(inv.AMOUNT IS NOT NULL AND inv.QUANTITY IS NOT NULL AND inv.QUANTITY > 0, inv.AMOUNT, 0))
                             / SUM(IFF(inv.QUANTITY IS NOT NULL AND inv.QUANTITY > 0, inv.QUANTITY, 0))
                    ELSE NULL
                END AS UNIT_PRICE
            FROM inv
                        JOIN sku_map_prod
                            ON UPPER(TRIM(inv.VENDOR_PRODUCT_SKU)) = sku_map_prod.VENDOR_INVOICE_SKU_KEY
            GROUP BY 1,2
        """).fetchall()
        conn.close()
        if rows:
            rates: dict[str, dict[dt.date, float]] = {}
            for billing_month, sku_group, price in rows:
                key = _sku_rate_key(sku_group)
                if key and billing_month is not None and price is not None and float(price) > 0:
                    month_first = dt.date(billing_month.year, billing_month.month, 1)
                    rates.setdefault(key, {})[month_first] = float(price)
            print(
                f"[INFO] Loaded SentinelOne monthly rates from VENDOR_INVOICES "
                f"for {len(rates)} canonical keys.",
                flush=True,
            )
            return rates
        print("[WARN] No SentinelOne rows found in VENDOR_INVOICES.", flush=True)
    except Exception as e:
        print(
            f"[WARN] Could not load SentinelOne rates from VENDOR_INVOICES ({e}). "
            "UNIT_PRICE will be NULL for this run.",
            flush=True,
        )
    return {}


# ---------------------------------------------------------------------------
# Filesystem discovery
# ---------------------------------------------------------------------------

def discover_month_folders(source_root: Path) -> dict[str, Path]:
    """Return ``{YYYY-MM: path}`` for every ``MM_MON_YYYY`` folder under root."""
    folders: dict[str, Path] = {}
    for child in source_root.iterdir():
        if not child.is_dir():
            continue
        m = MONTH_FOLDER_RE.match(child.name)
        if m:
            folders[f"{m.group('yyyy')}-{m.group('mm')}"] = child
    return dict(sorted(folders.items()))


def locate_usage_file(month_folder: Path) -> Path | None:
    """Return the sole authoritative ConnectWise usage workbook."""
    candidates = sorted(
        path
        for path in month_folder.iterdir()
        if path.is_file()
        and not path.name.startswith("~$")
        and path.suffix.lower() in {".xlsx", ".xlsm"}
        and re.fullmatch(
            r"ConnectWise Usage_[^\\/]+\.xls[xm]",
            path.name,
            flags=re.IGNORECASE,
        )
    )
    if not candidates:
        return None
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one ConnectWise Usage workbook in "
            f"{month_folder.name}; found {len(candidates)}: "
            f"{[path.name for path in candidates]}"
        )
    return candidates[0]


def _read_file_bytes(path: Path) -> bytes:
    """Read the workbook, falling back to a PowerShell copy on OneDrive locks."""
    try:
        return path.read_bytes()
    except PermissionError:
        with tempfile.NamedTemporaryFile(suffix=path.suffix, delete=False) as handle:
            tmp_path = Path(handle.name)
        try:
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    f"Copy-Item -LiteralPath '{path}' -Destination '{tmp_path}' -Force",
                ],
                check=True,
                capture_output=True,
            )
            return tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Workbook parsing
# ---------------------------------------------------------------------------

_REQUIRED_HEADERS = {
    _norm(ACCOUNT_COL),
    _norm(SITE_ACCOUNT_COL),
    _norm(SKU_COL),
    _norm(SITE_TYPE_COL),
    _norm(SITE_ID_COL),
    _norm(CREATED_DATE_COL),
    _norm(RETENTION_COL),
    _norm(TOTAL_AGENTS_COL),
}


def _find_usage_sheet(workbook: openpyxl.Workbook, path: Path) -> tuple[str, int, list[str]] | None:
    """Locate the sheet and header row that carry the CW usage schema."""
    sheet_order = [EXPECTED_SHEET_NAME] if EXPECTED_SHEET_NAME in workbook.sheetnames else []
    sheet_order += [name for name in workbook.sheetnames if name not in sheet_order]

    for sheet_name in sheet_order:
        ws = workbook[sheet_name]
        max_col = ws.max_column or 40
        for r in range(1, HEADER_SEARCH_ROWS + 1):
            headers = [
                (str(ws.cell(row=r, column=c).value).strip()
                 if ws.cell(row=r, column=c).value is not None else "")
                for c in range(1, max_col + 1)
            ]
            normalized = {_norm(h) for h in headers if h}
            if _REQUIRED_HEADERS <= normalized:
                return sheet_name, r, headers

    raise RuntimeError(f"{path.name}: no sheet contains the complete SentinelOne usage schema.")


def _canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename known columns to canonical names (whitespace/case tolerant)."""
    exact = {
        _norm(c): c
        for c in (
            ACCOUNT_COL,
            SITE_ACCOUNT_COL,
            SKU_COL,
            SITE_TYPE_COL,
            SITE_ID_COL,
            CREATED_DATE_COL,
            *EXPECTED_AGENT_COLUMNS,
        )
    }
    rename_map: dict[str, str] = {}
    for col in df.columns:
        n = _norm(col)
        canonical = exact.get(n)
        # Retention aliases -- ignore columns whose label mentions "agent".
        if canonical is None and "agent" not in str(col).lower() and (
            n in RETENTION_ALIASES or n.startswith("retention")
        ):
            canonical = RETENTION_COL
        if canonical and col != canonical:
            rename_map[str(col)] = canonical
    return df.rename(columns=rename_map)


def _identify_agent_columns(columns: list[str]) -> list[str]:
    """Return all raw column names that represent per-site agent counts.

    Includes the ``Total Active Agents per site`` roll-up alongside per-product
    columns and the ``Ranger AD Full`` / ``Ranger AD Protect Full`` carve-outs.
    """
    excluded = {_norm(c) for c in (SITE_ACCOUNT_COL, SKU_COL, CREATED_DATE_COL, RETENTION_COL)}
    extras = {_norm(c) for c in KNOWN_AGENT_EXTRAS}

    agents: list[str] = []
    for h in columns:
        if not h:
            continue
        n = _norm(h)
        if n in excluded:
            continue
        if "agent" in str(h).lower() or n in extras:
            agents.append(h)
    return agents


def _read_workbook(path: Path) -> pd.DataFrame:
    """Read the CW usage sheet from ``path`` into a raw DataFrame."""
    file_bytes = _read_file_bytes(path)
    content_hash = hashlib.sha256(file_bytes).hexdigest()
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    try:
        match = _find_usage_sheet(wb, path)
        if match is None:
            return pd.DataFrame()

        sheet_name, header_row, headers = match
        headers = [h if h else f"col_{i}" for i, h in enumerate(headers)]
        ws = wb[sheet_name]

        records: list[dict[str, object]] = []
        for source_row, row in enumerate(
            ws.iter_rows(min_row=header_row + 1, values_only=True),
            start=header_row + 1,
        ):
            if not row or all(cell is None for cell in row):
                continue
            record = {headers[i]: row[i] if i < len(row) else None for i in range(len(headers))}
            record["_SOURCE_ROW_NUMBER"] = source_row
            records.append(record)
    finally:
        wb.close()

    frame = pd.DataFrame(records)
    frame.attrs.update(source_sheet=sheet_name, source_content_hash=content_hash)
    return frame


def _clean_text(series: pd.Series) -> pd.Series:
    return (
        series.where(series.notna(), "")
        .astype(str)
        .str.strip()
        .replace({"None": "", "nan": "", "NaN": ""})
    )


def parse_usage_workbook(
    path: Path,
    *,
    expected_month: str | None = None,
    rate_history: dict[str, dict[dt.date, float]] | None = None,
    usage_alias_map: dict[str, str] | None = None,
    ignored_files: tuple[str, ...] = (),
    ingested_at: dt.datetime | None = None,
) -> pd.DataFrame:
    """Parse a CW usage workbook into the ``SENTINELONE_USAGE`` template."""
    raw = _read_workbook(path)
    if raw.empty:
        raise RuntimeError(f"{path.name} contains no usage rows.")

    source_sheet = str(raw.attrs.get("source_sheet", ""))
    source_content_hash = str(raw.attrs.get("source_content_hash", ""))

    df = _canonicalize_columns(raw)

    required = [ACCOUNT_COL, SITE_ACCOUNT_COL, SKU_COL, SITE_TYPE_COL, SITE_ID_COL,
                CREATED_DATE_COL, RETENTION_COL, *EXPECTED_AGENT_COLUMNS]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"{path.name} is missing required columns: {sorted(missing)}")

    for text_col in (ACCOUNT_COL, SITE_ACCOUNT_COL, SKU_COL, SITE_TYPE_COL, SITE_ID_COL, RETENTION_COL):
        df[text_col] = _clean_text(df[text_col])

    df["BILLING_MONTH"] = df[CREATED_DATE_COL].apply(_to_first_of_month)

    unexpected_accounts = sorted(set(df[ACCOUNT_COL]) - {"", "Connectwise", "Total"})
    if unexpected_accounts:
        raise RuntimeError(f"{path.name} contains unexpected Account Name values: {unexpected_accounts}")

    agent_cols = _identify_agent_columns(list(df.columns))
    missing_agent_cols = sorted(set(EXPECTED_AGENT_COLUMNS) - set(agent_cols))
    if missing_agent_cols:
        raise RuntimeError(f"{path.name} is missing expected usage products: {missing_agent_cols}")
    for c in agent_cols:
        converted = pd.to_numeric(df[c], errors="coerce")
        invalid = int((df[c].notna() & converted.isna()).sum())
        if invalid:
            raise RuntimeError(f"{path.name} has {invalid} nonnumeric values in {c!r}.")
        df[c] = converted.fillna(0.0)

    footer = df[df[ACCOUNT_COL].str.casefold() == "total"]
    if len(footer) != 1:
        raise RuntimeError(f"{path.name} must contain exactly one Total row; found {len(footer)}.")

    potential_detail = df[df[ACCOUNT_COL].str.casefold() == "connectwise"].copy()
    incomplete = ((potential_detail[SITE_ACCOUNT_COL] == "")
                  | (potential_detail[SKU_COL] == "")
                  | potential_detail["BILLING_MONTH"].isna())
    positive_incomplete = incomplete & potential_detail[agent_cols].sum(axis=1).gt(0)
    if positive_incomplete.any():
        source_rows = potential_detail.loc[positive_incomplete, "_SOURCE_ROW_NUMBER"].astype(int).tolist()
        raise RuntimeError(
            f"{path.name} has positive usage with a missing partner, SKU, or billing date "
            f"at source rows {source_rows[:20]}."
        )

    unexpected_site_types = sorted(set(
        potential_detail.loc[
            ~incomplete & ~potential_detail[SITE_TYPE_COL].str.casefold().eq("paid"),
            SITE_TYPE_COL,
        ]
    ))
    if unexpected_site_types:
        raise RuntimeError(f"{path.name} contains unexpected site types: {unexpected_site_types}")

    df = potential_detail[~incomplete & potential_detail[SITE_TYPE_COL].str.casefold().eq("paid")].copy()
    if df.empty:
        raise RuntimeError(f"{path.name} contains no valid paid usage rows.")

    resolved_months = sorted(pd.to_datetime(df["BILLING_MONTH"]).dt.strftime("%Y-%m").unique())
    if expected_month is not None and resolved_months != [expected_month]:
        raise RuntimeError(
            f"{path.name} resolves to billing months {resolved_months}, but the folder is {expected_month}."
        )
    snapshot_dates = sorted(pd.to_datetime(df[CREATED_DATE_COL], errors="coerce").dt.date.unique())
    if len(snapshot_dates) != 1:
        raise RuntimeError(f"{path.name} must contain one snapshot date; found {snapshot_dates}.")
    snapshot_date = snapshot_dates[0]

    duplicate_site_ids = int(df.duplicated(SITE_ID_COL, keep=False).sum())
    if duplicate_site_ids:
        raise RuntimeError(f"{path.name} contains {duplicate_site_ids} rows with duplicate Site ID values.")

    footer_failures = {
        column: round(float(df[column].sum() - footer[column].iloc[0]), 6)
        for column in agent_cols
        if abs(float(df[column].sum() - footer[column].iloc[0])) > 0.000001
    }
    if footer_failures:
        raise RuntimeError(f"{path.name} detail rows do not match the Total row: {footer_failures}")

    # Drop partners (Site Account Name) whose usage AGGREGATED across every
    # row and every agent column sums to zero. A partner only qualifies as
    # "no usage" if every single agent column on every single one of its rows
    # is zero -- individual zero rows on an otherwise-live partner are kept
    # so add-on-only patterns (e.g. Ranger without Total Active Agents on a
    # given row) are never silently discarded. Per-(site, sku, product) rows
    # with QUANTITY=0 are filtered later by the melt step, so nothing empty
    # is emitted downstream.
    site_totals = df.groupby(SITE_ACCOUNT_COL)[agent_cols].sum()
    live_sites = site_totals[site_totals.sum(axis=1) > 0].index
    pre_sites = df[SITE_ACCOUNT_COL].nunique()
    pre_rows = len(df)
    df = df[df[SITE_ACCOUNT_COL].isin(live_sites)].copy()
    dropped_sites = pre_sites - df[SITE_ACCOUNT_COL].nunique()
    dropped_rows = pre_rows - len(df)
    if dropped_sites:
        print(
            f"  Dropped {dropped_sites:,} partners (rows={dropped_rows:,}) "
            "with zero usage across every agent column."
        )
    if df.empty:
        raise RuntimeError(f"{path.name} contains no positive usage.")

    id_cols = ["BILLING_MONTH", SITE_ACCOUNT_COL, SKU_COL, RETENTION_COL]
    melted = df[id_cols + agent_cols].melt(
        id_vars=id_cols,
        value_vars=agent_cols,
        var_name="RAW_PRODUCT",
        value_name="QUANTITY",
    )
    # Keep only strictly positive quantities so we emit:
    #   * one Total Active Agents row per partner+sku (when total > 0)
    #   * per-product add-on rows only where the module is enabled (> 0)
    melted = melted[melted["QUANTITY"] > 0].copy()
    if melted.empty:
        raise RuntimeError(f"{path.name} produced no positive usage.")

    melted["_CLEANED_PRODUCT"] = melted["RAW_PRODUCT"].apply(_clean_product_name)

    # -----------------------------------------------------------------------
    # Resolve VENDOR_PRODUCT_SKU (= sku_match_group) at ingestion time.
    # This eliminates the downstream CASE logic that used ENTITY and
    # RETENTION_DESC to re-identify the billing group.
    # -----------------------------------------------------------------------
    is_total_agents = melted["_CLEANED_PRODUCT"] == TOTAL_AGENTS_PRODUCT_LABEL
    is_data_retention = melted["_CLEANED_PRODUCT"].str.contains("Data Retention", case=False, na=False)

    # Default: cleaned product name IS the sku_match_group (Ranger, Purple AI, etc.)
    melted["VENDOR_PRODUCT_SKU"] = melted["_CLEANED_PRODUCT"]

    # Total Active Agents -> use the SKU/entity value (Complete / Control / Core)
    melted.loc[is_total_agents, "VENDOR_PRODUCT_SKU"] = (
        melted.loc[is_total_agents, SKU_COL].astype(str).str.strip()
    )

    # Data Retention -> combine with retention days descriptor
    ret_filled = melted.loc[is_data_retention, RETENTION_COL].astype(str).str.strip().replace("", "Unknown")
    melted.loc[is_data_retention, "VENDOR_PRODUCT_SKU"] = "Data Retention - " + ret_filled

    agg = (
        melted.groupby(
            ["BILLING_MONTH", SITE_ACCOUNT_COL, "VENDOR_PRODUCT_SKU"],
            dropna=False,
        )
        .agg(QUANTITY=("QUANTITY", "sum"))
        .reset_index()
        .rename(columns={SITE_ACCOUNT_COL: "VENDOR_PARTNER_NAME"})
    )
    if rate_history is None:
        rate_history = load_invoice_rate_history()
    if usage_alias_map is None:
        usage_alias_map = _load_usage_product_alias_map()
    agg["VENDOR"] = "SentinelOne"
    agg["MODIFIER"] = None

    def _resolve_row_rate(row: pd.Series) -> float | None:
        key = _sku_rate_key(row.get("VENDOR_PRODUCT_SKU"))
        bm = row.get("BILLING_MONTH")
        if pd.isna(bm):
            return None
        month_first = dt.date(bm.year, bm.month, 1)

        # Prefer direct key first; only use alias when direct key has no rate.
        direct = _resolve_month_rate(key, month_first, rate_history)
        if direct is not None:
            return direct

        canonical_key = usage_alias_map.get(key)
        if canonical_key:
            return _resolve_month_rate(canonical_key, month_first, rate_history)
        return None

    agg["UNIT_PRICE"] = agg.apply(_resolve_row_rate, axis=1)
    agg["AMOUNT"] = agg["QUANTITY"] * agg["UNIT_PRICE"]
    agg["CURRENCY"] = "USD"

    agg["ADDITIONAL_INFO"] = agg.apply(
        lambda _row: json.dumps(
            {
                "source_file": path.name,
                "source_sheet": source_sheet,
                "source_content_hash": source_content_hash,
                "snapshot_date": snapshot_date.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        axis=1,
    )

    result = agg[list(TEMPLATE_COLUMNS)].reset_index(drop=True)
    result.attrs["source_audit"] = {
        "SOURCE_FOLDER": path.parent.name,
        "SOURCE_FILE": path.name,
        "SOURCE_CONTENT_HASH": source_content_hash,
        "BILLING_MONTH": dt.date.fromisoformat(f"{expected_month or resolved_months[0]}-01"),
        "SNAPSHOT_DATE": snapshot_date,
        "SOURCE_ROWS": len(df),
        "PARTNERS": int(df[SITE_ACCOUNT_COL].nunique()),
        "SITES": int(df[SITE_ID_COL].nunique()),
        "OUTPUT_ROWS": len(result),
        "DUPLICATE_SITE_IDS": duplicate_site_ids,
        "FOOTER_TOTAL_CHECK": "PASS",
        "MISSING_RATE_ROWS": int(result["UNIT_PRICE"].isna().sum()),
        "MISSING_RATE_PRODUCTS": json.dumps(
            sorted(result.loc[result["UNIT_PRICE"].isna(), "VENDOR_PRODUCT_SKU"].unique()),
            separators=(",", ":"),
        ),
        "IGNORED_FILES": json.dumps(ignored_files, separators=(",", ":")),
        "ERROR_MESSAGE": None,
        "INGESTED_AT": ingested_at or dt.datetime.now(dt.UTC).replace(tzinfo=None),
    }

    return result


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def parse_month(
    source_root: Path,
    month: str,
    *,
    rate_history: dict[str, dict[dt.date, float]] | None = None,
    usage_alias_map: dict[str, str] | None = None,
    ingested_at: dt.datetime | None = None,
) -> pd.DataFrame:
    """Parse a single ``YYYY-MM`` month folder and return the template frame."""
    month_folder = discover_month_folders(source_root).get(month)
    if month_folder is None:
        raise FileNotFoundError(f"No folder found for {month} under {source_root}")

    usage_path = locate_usage_file(month_folder)
    if usage_path is None:
        raise FileNotFoundError(f"No ConnectWise Usage*.xlsx found in {month_folder}")

    try:
        ignored_files = tuple(sorted(
            path.name for path in month_folder.iterdir()
            if path.is_file() and not path.name.startswith("~$") and path != usage_path
        ))
        frame = parse_usage_workbook(
            usage_path,
            expected_month=month,
            rate_history=rate_history,
            usage_alias_map=usage_alias_map,
            ignored_files=ignored_files,
            ingested_at=ingested_at,
        )
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            # XML parser on a corrupt file can raise KeyboardInterrupt via
            # pyexpat signal handling. Log and skip rather than halt the run.
            print(
                f"[{month}] WARN: XML parse interrupted on {usage_path.name} "
                f"(likely corrupt/malformed XLSX). Skipping â€” replace source file."
            )
            return pd.DataFrame(columns=list(TEMPLATE_COLUMNS))
        raise

    if frame.empty:
        print(f"[{month}] {usage_path.name}: no rows produced.")
        return frame

    resolved = sorted(pd.to_datetime(frame["BILLING_MONTH"]).dt.date.astype(str).unique())
    print(
        f"[{month} folder] {usage_path.name}: "
        f"billing_month(s)={resolved}, rows={len(frame):,}, "
        f"quantity={frame['QUANTITY'].sum():,.0f}, "
        f"partners={frame['VENDOR_PARTNER_NAME'].nunique():,}, "
        f"products={frame['VENDOR_PRODUCT_SKU'].nunique():,}"
    )
    return frame


def write_audit(df: pd.DataFrame, label: str) -> Path:
    """Emit a per-month/product audit CSV alongside the load."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    audit = (
        df.groupby(["BILLING_MONTH", "VENDOR_PRODUCT_SKU"], dropna=False)
        .agg(
            row_count=("VENDOR", "size"),
            quantity=("QUANTITY", "sum"),
            distinct_partners=("VENDOR_PARTNER_NAME", "nunique"),
        )
        .reset_index()
    )
    path = OUTPUT_DIR / f"sentinelone_usage_ingest_audit_{label}.csv"
    audit.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)
    print(f"Wrote audit: {path}")
    return path


# ---------------------------------------------------------------------------
# Snowflake load
# ---------------------------------------------------------------------------

def _fetch_existing_vendor_months() -> set[str]:
    """Return existing billing months for SentinelOne already in the target table."""
    sys.path.insert(0, str(WORKSPACE_ROOT))
    from TEMPLATES.Python.connection import get_snowflake_connection

    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database=TARGET_DATABASE,
        schema=TARGET_SCHEMA,
    )
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT DISTINCT BILLING_MONTH FROM {FQN} "
            "WHERE UPPER(COALESCE(VENDOR, '')) = UPPER(%s)",
            (TARGET_VENDOR,),
        )
        return {
            (row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0]))
            for row in cur.fetchall()
            if row and row[0] is not None
        }
    except Exception:
        # If lookup fails (table missing, etc.), proceed with normal parse/load flow.
        return set()
    finally:
        conn.close()

def snowflake_ddl() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {FQN} (
    BILLING_MONTH       DATE,
    VENDOR              VARCHAR,
    VENDOR_PARTNER_NAME VARCHAR,
    VENDOR_PRODUCT_SKU  VARCHAR,
    MODIFIER            VARCHAR,
    QUANTITY            NUMBER(18, 4),
    UNIT_PRICE          NUMBER(18, 6),
    AMOUNT              NUMBER(18, 6),
    CURRENCY            VARCHAR,
    ADDITIONAL_INFO     VARCHAR
);
"""


def load_snowflake(
    df: pd.DataFrame,
    *,
    reset: bool = False,
) -> None:
    """Stage and transactionally publish SentinelOne usage."""
    sys.path.insert(0, str(WORKSPACE_ROOT))
    from snowflake.connector.pandas_tools import write_pandas
    from TEMPLATES.Python.connection import get_snowflake_connection

    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database=TARGET_DATABASE,
        schema=TARGET_SCHEMA,
    )
    usage_stage = f"_S1_USAGE_STAGE_{uuid.uuid4().hex[:12].upper()}"
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {TARGET_DATABASE}.{TARGET_SCHEMA}")

        cur.execute(snowflake_ddl())
        cur.execute(f"ALTER TABLE {FQN} ADD COLUMN IF NOT EXISTS ADDITIONAL_INFO VARCHAR")

        cur.execute(
            f"SELECT DISTINCT BILLING_MONTH FROM {FQN} "
            "WHERE UPPER(COALESCE(VENDOR, '')) = UPPER(%s)",
            (TARGET_VENDOR,),
        )
        existing = {
            (row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0]))
            for row in cur.fetchall()
        }
        incoming = sorted(pd.to_datetime(df["BILLING_MONTH"]).dt.date.astype(str).unique())
        new_months = incoming if reset else [m for m in incoming if m not in existing]
        skipped = [] if reset else [m for m in incoming if m in existing]

        if skipped:
            print(f"Skipping months already loaded: {skipped}")
        if not new_months:
            print("Nothing to load; every month already exists.")
            return

        load_df = df[df["BILLING_MONTH"].astype(str).isin(new_months)].reset_index(drop=True)
        cur.execute(f"CREATE TEMP TABLE {TARGET_DATABASE}.{TARGET_SCHEMA}.{usage_stage} LIKE {FQN}")
        success, _chunks, rows, output = write_pandas(
            conn, load_df, usage_stage,
            database=TARGET_DATABASE, schema=TARGET_SCHEMA, quote_identifiers=False,
        )
        if not success or rows != len(load_df):
            raise RuntimeError(f"Snowflake write_pandas failed: {output}")

        month_list = ", ".join(f"'{month}'::DATE" for month in new_months)
        cur.execute("BEGIN")
        if reset:
            cur.execute(
                f"DELETE FROM {FQN} WHERE UPPER(COALESCE(VENDOR, '')) = UPPER(%s)",
                (TARGET_VENDOR,),
            )
        else:
            cur.execute(
                f"DELETE FROM {FQN} WHERE UPPER(COALESCE(VENDOR, '')) = UPPER(%s) "
                f"AND BILLING_MONTH IN ({month_list})",
                (TARGET_VENDOR,),
            )
        usage_columns = ", ".join(TEMPLATE_COLUMNS)
        cur.execute(
            f"INSERT INTO {FQN} ({usage_columns}) SELECT {usage_columns} "
            f"FROM {TARGET_DATABASE}.{TARGET_SCHEMA}.{usage_stage}"
        )
        conn.commit()
        print(f"Published {rows:,} rows for months {new_months} into {FQN}.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest SentinelOne monthly vendor usage into Snowflake.",
    )
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT),
                        help="Root folder containing MM_MON_YYYY month folders.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--month", help="Billing month folder to load, e.g. 2026-05.")
    group.add_argument("--all-months", action="store_true", help="Load every discovered month.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and audit; skip Snowflake load.")
    parser.add_argument("--reset", action="store_true", help=f"Drop {TARGET_TABLE} before loading.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    source_root = Path(args.source_root)
    if not source_root.exists():
        raise FileNotFoundError(f"Source root does not exist: {source_root}")

    months = list(discover_month_folders(source_root).keys()) if args.all_months else [args.month]

    # Fast no-op path: skip expensive workbook parsing for months already loaded.
    if not args.dry_run and not args.reset:
        existing_months = _fetch_existing_vendor_months()
        if existing_months:
            pending_months: list[str] = []
            skipped_months: list[str] = []
            for month in months:
                month_first = f"{month}-01"
                if month_first in existing_months:
                    skipped_months.append(month)
                else:
                    pending_months.append(month)
            if skipped_months:
                print(f"Skipping already-loaded months before parse: {skipped_months}")
            if not pending_months:
                print("All requested months already loaded; nothing to parse or load.")
                return
            months = pending_months

    rate_history = load_invoice_rate_history()
    usage_alias_map = _load_usage_product_alias_map()
    ingested_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    frames = [
        parse_month(
            source_root, m, rate_history=rate_history,
            usage_alias_map=usage_alias_map, ingested_at=ingested_at,
        )
        for m in months
    ]
    audit_rows = [frame.attrs["source_audit"] for frame in frames if "source_audit" in frame.attrs]
    skipped = [m for m, f in zip(months, frames) if f.empty]
    if skipped:
        print(f"WARNING: {len(skipped)} month(s) skipped (parse failed or no rows): {skipped}")
    non_empty = [f for f in frames if not f.empty]
    all_rows = (
        pd.concat(non_empty, ignore_index=True)
        if non_empty
        else pd.DataFrame(columns=list(TEMPLATE_COLUMNS))
    )

    if all_rows.empty:
        print("No rows produced. Nothing to write.")
        return

    print(
        f"TOTAL rows={len(all_rows):,}, "
        f"quantity={all_rows['QUANTITY'].sum():,.0f}, "
        f"partners={all_rows['VENDOR_PARTNER_NAME'].nunique():,}, "
        f"products={all_rows['VENDOR_PRODUCT_SKU'].nunique():,}"
    )

    label = "all_months" if args.all_months else args.month.replace("-", "_")
    write_audit(all_rows, label)
    source_audit_path = OUTPUT_DIR / f"sentinelone_source_audit_{label}.csv"
    pd.DataFrame(audit_rows, columns=list(AUDIT_COLUMNS)).to_csv(
        source_audit_path, index=False, quoting=csv.QUOTE_MINIMAL
    )
    print(f"Wrote source audit: {source_audit_path}")

    if args.dry_run:
        print("Dry run complete. Snowflake load skipped.")
        return

    load_snowflake(all_rows, reset=args.reset)


if __name__ == "__main__":
    main()

