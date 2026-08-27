from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class _RatePoint:
    billing_month: pd.Timestamp
    canonical_key: str
    unit_price: float


def _norm_key(value: Any) -> str:
    text = str(value or "").strip().upper()
    return " ".join(text.split())


def _month_start(value: Any) -> pd.Timestamp | None:
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(year=ts.year, month=ts.month, day=1)


def _compute_monthly_rates(conn, vendor_name: str) -> tuple[dict[tuple[str, pd.Timestamp], float], dict[str, list[tuple[pd.Timestamp, float]]]]:
    vendor_like = f"%{vendor_name}%"
    sql = """
        WITH sku_map AS (
            SELECT DISTINCT
                UPPER(TRIM(COALESCE(SKU_MATCH_KEY, ''))) AS CANONICAL_KEY,
                UPPER(TRIM(COALESCE(VENDOR_SKU, ''))) AS VENDOR_INVOICE_SKU_KEY,
                UPPER(TRIM(COALESCE(CW_SKU, ''))) AS CW_SKU_KEY,
                UPPER(TRIM(COALESCE(VENDOR_PRODUCT, ''))) AS VENDOR_PRODUCT_KEY
            FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SKU_MAP_PROD
            WHERE VENDOR ILIKE %s
              AND NULLIF(TRIM(COALESCE(SKU_MATCH_KEY, '')), '') IS NOT NULL
        ),
        invoice_raw AS (
            SELECT
                DATE_TRUNC('MONTH', BILLING_MONTH)::DATE AS BILLING_MONTH,
                UPPER(TRIM(COALESCE(VENDOR_PRODUCT_SKU, ''))) AS INVOICE_SKU_KEY,
                UNIT_PRICE,
                QUANTITY,
                AMOUNT
            FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
            WHERE VENDOR ILIKE %s
                            AND (
                                        (UNIT_PRICE IS NOT NULL AND UNIT_PRICE > 0)
                                        OR (QUANTITY IS NOT NULL AND QUANTITY > 0 AND AMOUNT IS NOT NULL)
                                    )
        ),
        invoice_enriched AS (
            SELECT
                r.BILLING_MONTH,
                COALESCE(
                    m1.CANONICAL_KEY,
                    m2.CANONICAL_KEY,
                    m3.CANONICAL_KEY,
                    r.INVOICE_SKU_KEY
                ) AS CANONICAL_KEY,
                r.UNIT_PRICE,
                r.QUANTITY,
                r.AMOUNT
            FROM invoice_raw r
            LEFT JOIN sku_map m1 ON r.INVOICE_SKU_KEY = m1.VENDOR_INVOICE_SKU_KEY
            LEFT JOIN sku_map m2 ON r.INVOICE_SKU_KEY = m2.CW_SKU_KEY
            LEFT JOIN sku_map m3 ON r.INVOICE_SKU_KEY = m3.VENDOR_PRODUCT_KEY
        )
        SELECT
            BILLING_MONTH,
            CANONICAL_KEY,
            CASE
                WHEN COUNT(DISTINCT UNIT_PRICE) = 1 THEN MAX(UNIT_PRICE)
                WHEN SUM(IFF(QUANTITY IS NOT NULL AND QUANTITY > 0 AND AMOUNT IS NOT NULL, QUANTITY, 0)) > 0
                    THEN SUM(IFF(QUANTITY IS NOT NULL AND QUANTITY > 0 AND AMOUNT IS NOT NULL, AMOUNT, 0))
                         / SUM(IFF(QUANTITY IS NOT NULL AND QUANTITY > 0 AND AMOUNT IS NOT NULL, QUANTITY, 0))
                ELSE NULL
            END AS UNIT_PRICE
        FROM invoice_enriched
        GROUP BY 1, 2
    """
    rows = conn.cursor().execute(sql, (vendor_like, vendor_like)).fetchall()
    exact: dict[tuple[str, pd.Timestamp], float] = {}
    history: dict[str, list[tuple[pd.Timestamp, float]]] = defaultdict(list)
    for billing_month, canonical_key, unit_price in rows:
        key = _norm_key(canonical_key)
        month = _month_start(billing_month)
        if not key or month is None or unit_price is None:
            continue
        price = float(unit_price)
        if price <= 0:
            continue
        exact[(key, month)] = price
        history[key].append((month, price))
    for sku_key in list(history.keys()):
        history[sku_key].sort(key=lambda item: item[0])
    return exact, history


def _load_usage_key_map(conn, vendor_name: str) -> dict[str, str]:
    sql = """
        SELECT DISTINCT
            UPPER(TRIM(COALESCE(SKU_MATCH_KEY, ''))) AS CANONICAL_KEY,
            UPPER(TRIM(COALESCE(VENDOR_SKU, ''))) AS VENDOR_INVOICE_SKU_KEY,
            UPPER(TRIM(COALESCE(CW_SKU, ''))) AS CW_SKU_KEY,
            UPPER(TRIM(COALESCE(VENDOR_PRODUCT, ''))) AS VENDOR_PRODUCT_KEY
        FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SKU_MAP_PROD
        WHERE VENDOR ILIKE %s
          AND NULLIF(TRIM(COALESCE(SKU_MATCH_KEY, '')), '') IS NOT NULL
    """
    rows = conn.cursor().execute(sql, (f"%{vendor_name}%",)).fetchall()
    alias_map: dict[str, str] = {}
    for canonical_key, vendor_invoice_key, cw_key, vendor_product_key in rows:
        canonical = _norm_key(canonical_key)
        if not canonical:
            continue
        for candidate in (vendor_invoice_key, cw_key, vendor_product_key):
            key = _norm_key(candidate)
            if key and key not in alias_map:
                alias_map[key] = canonical
    return alias_map


def _resolve_rate(
    sku_key: str,
    billing_month: pd.Timestamp,
    exact: dict[tuple[str, pd.Timestamp], float],
    history: dict[str, list[tuple[pd.Timestamp, float]]],
) -> float | None:
    direct = exact.get((sku_key, billing_month))
    if direct is not None:
        return direct
    best_price = None
    for month, price in history.get(sku_key, []):
        if month <= billing_month:
            best_price = price
        else:
            break
    return best_price


def fill_missing_prices_dynamic(df: pd.DataFrame, vendor_name: str, conn=None) -> pd.DataFrame:
    """Fill missing UNIT_PRICE and/or AMOUNT dynamically.

    Rules:
    - If source has UNIT_PRICE/AMOUNT, keep them unchanged.
    - Else use exact invoice month + mapped canonical SKU.
    - Else use most recent prior month invoice rate for that canonical SKU.
    - AMOUNT is derived as QUANTITY * resolved UNIT_PRICE only when missing.
    """
    if df.empty:
        return df

    owned_conn = conn is None
    sf_conn = conn
    try:
        if owned_conn:
            from TEMPLATES.Python.connection import get_snowflake_connection

            sf_conn = get_snowflake_connection(
                role="DEVELOPER",
                warehouse="REPORTING_WH",
                database="ANALYTICS_DEV",
                schema="DBT_NFOLD_TRANSFORMATION",
            )

        exact, history = _compute_monthly_rates(sf_conn, vendor_name)
        if not exact:
            return df
        usage_alias_map = _load_usage_key_map(sf_conn, vendor_name)
    except Exception as exc:
        print(f"[WARN] fill_missing_prices_dynamic failed for {vendor_name}: {exc}", flush=True)
        return df
    finally:
        if owned_conn and sf_conn is not None:
            sf_conn.close()

    out = df.copy()
    for col in ("UNIT_PRICE", "AMOUNT"):
        if col not in out.columns:
            out[col] = None

    month_series = pd.to_datetime(out["BILLING_MONTH"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    sku_series = out["VENDOR_PRODUCT_SKU"].astype(str).map(_norm_key)
    unit_missing = out["UNIT_PRICE"].isna() | (pd.to_numeric(out["UNIT_PRICE"], errors="coerce").fillna(0) == 0)
    amount_missing = out["AMOUNT"].isna() | (pd.to_numeric(out["AMOUNT"], errors="coerce").fillna(0) == 0)
    mask = unit_missing | amount_missing

    filled = 0
    for idx in out[mask].index:
        billing_month = month_series.iloc[out.index.get_loc(idx)]
        if pd.isna(billing_month):
            continue
        raw_sku = sku_series.iloc[out.index.get_loc(idx)]
        canonical_key = usage_alias_map.get(raw_sku, raw_sku)
        unit_price = _resolve_rate(canonical_key, billing_month, exact, history)
        if unit_price is None:
            continue
        qty = pd.to_numeric(out.at[idx, "QUANTITY"], errors="coerce")
        if unit_missing.iloc[out.index.get_loc(idx)]:
            out.at[idx, "UNIT_PRICE"] = float(unit_price)
        if amount_missing.iloc[out.index.get_loc(idx)] and not pd.isna(qty):
            out.at[idx, "AMOUNT"] = float(qty) * float(unit_price)
        filled += 1

    if filled:
        print(f"[INFO] fill_missing_prices_dynamic: filled {filled:,} rows for {vendor_name}.", flush=True)
    return out
