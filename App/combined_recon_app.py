"""3rd-Party Vendor Reconciliation Suite - Streamlit app.

Design goals
------------
* Mirror the C:/Users/Nate.Fold/OneDrive - ConnectWise, Inc/SentinelOne POC/
  App_Formatting/Vendor_Recon_Dashboard_v2.html mockup as closely as
  Streamlit allows: hero header, controls strip, RYG matrix, exception
  detail cards, vendor deep dive, profitability by vendor, and an
  iterative AI Analyst chat.
* The combined production pipeline loads each vendor from the shared
  THIRD_PARTY_RECON_* marts. Vendor-specific ingestion and reconciliation
  stay separate upstream; this app consumes the converged contract.
* Any layout that is easier to express as HTML uses ``st.markdown`` with
  the shared CSS. Interactive queues use ``st.dataframe`` for sort /
  filter / download affordances.
"""

from __future__ import annotations

import json
import html
import os
import re
import calendar
from functools import cached_property
from typing import Any, Callable

import pandas as pd
import numpy as np
import streamlit as st

try:
    import altair as alt  # ships with Streamlit
except Exception:  # pragma: no cover -- fall back to Streamlit's native charts
    alt = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SCHEMA = "ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION"
APP_TITLE = "Third Party Vendor Reconciliation"
APP_SUBTITLE = "Combined Recon Production Suite"
# Cache TTLs are intentionally long. The freshness key (LAST_ALTERED across the
# recon tables) is the primary cache-invalidator — it changes the moment the
# pipeline rebuilds any table, which busts every downstream @st.cache_data
# entry that carries `freshness` as a parameter. TTLs act as a safety net only.
# Previously TTLs were 30s / 10s which forced Snowflake re-queries every few
# reruns even when the underlying data hadn't moved.
DATA_TTL_SECONDS = int(os.getenv("THIRD_PARTY_RECON_DASHBOARD_TTL_SECONDS", "1800"))
FRESHNESS_TTL_SECONDS = int(os.getenv("THIRD_PARTY_RECON_DASHBOARD_FRESHNESS_TTL_SECONDS", "120"))
SALESFORCE_ACCOUNT_URL_PREFIX = (
    "https://connectwise20.lightning.force.com/lightning/r/Account/"
)
SALESFORCE_ACCOUNT_DISPLAY_REGEX = r"[?&]cws_id=([^&]+)"

# Increment when VendorSlice shape or taxonomy changes to bust all caches.
# v20: removed stale pre-agg table dependency; app now reads ONLY from THIRD_PARTY_RECON_OUTPUT_PROD.
# v21: cycle-aware TRT — API_QUANTITY = point-in-time on vendor snapshot day
#      (S1/BD 21st, Webroot 19th), AVG_API_QUANTITY = daily avg across the
#      (prev_snapshot, snapshot] cycle. Rule 5 now partner-month grain.
# v22: TRT source now uses seed__product_categorization SKU filter (matches
#      manual recon Excel queries). Added Auvik cycle vendor (21st).
#      Webroot restricted to is_server='' (SAT/DNS product line). SF_ID
#      resolved via curated partner_map primary + Zuora bridge.
# v23: 2026-08-21 latency pass — pipeline pre-computes ACTION_NEEDED,
#      IS_FINANCE_QUEUE, IS_OPS_QUEUE, IS_TIMING_QUEUE, IS_LEAKAGE and CASE_ID
#      so the app skips per-row Python classification. Single UNION query
#      loads every vendor in one round-trip. Cache TTLs bumped so freshness
#      key alone drives invalidation.
# v24: TRT API backfill expanded to Proofpoint (21st cycle snapshot).
#      API_QUANTITY and AVG_API_QUANTITY now populate for Proofpoint rows.
# v26: ESET is quantity-first and now carries contract-cost overlay dollars.
# v27: adds vendor invoice vs raw vendor usage SKU-level intra-vendor control.
# v28: replaces diagnostic status columns with health, clear-rate, parity, and margin metrics.
# v29: 2026-08-28 adds API_AMOUNT / AVG_API_AMOUNT / API_AVG_MINUS_POINT_AMOUNT
#      to OUTPUT_PROD (API seat count x VENDOR_UNIT_PRICE for point-in-time vs
#      cycle-average) and renders a per-SKU API-$ variance table in the
#      vendor deep-dive tab beneath the invoice-vs-raw-usage panel.
# v30: adds direct NetSuite invoice links and precomputed Salesforce account
#      links while preserving the ACT number as the displayed account label.
SLICE_SCHEMA_VERSION = "v32"


def _salesforce_account_links(frame: pd.DataFrame) -> pd.Series:
    """Return precomputed Salesforce URLs, falling back to the ACT identifier."""
    account_ids = frame.get(
        "SF_ID", pd.Series("", index=frame.index, dtype="object")
    ).fillna("").astype(str)
    urls = frame.get(
        "SALESFORCE_ACCOUNT_URL", pd.Series("", index=frame.index, dtype="object")
    ).fillna("").astype(str)
    return urls.where(urls.str.startswith(SALESFORCE_ACCOUNT_URL_PREFIX), account_ids)


def _salesforce_link_column(label: str = "Account") -> Any:
    """Configure a URL cell to display its embedded ACT account number."""
    return st.column_config.LinkColumn(
        label,
        display_text=SALESFORCE_ACCOUNT_DISPLAY_REGEX,
        help="Open the mapped Salesforce account in Lightning.",
        disabled=True,
    )

# Reconciliation check keys shown on every vendor row.
CHECKS = [
    ("account", "Account Match"),
    ("seats", "Seat Count"),
    ("sku", "SKU Match"),
    ("price", "Negative Margin Accounts"),
]

# Glossary shown under the Vendor Reconciliation Status matrix.
COLUMN_GLOSSARY: list[tuple[str, str]] = [
    ("Vendor Health", "Overall red/yellow/green status using margin, seat parity, and reconciliation clear rate for the current filter."),
    ("Reconciliation Clear Rate", "Clear rows divided by total reconciliation rows in the selected view."),
    ("Seat Parity", "CW billed seats divided by vendor-reported seats, plus the net CW-minus-vendor seat difference."),
    ("Margin", "Gross margin percentage and dollars: CW billed revenue minus vendor cost."),
]

# Plain-English glossary of the seven mutually exclusive canonical outcomes.
OUTCOME_FLAG_GLOSSARY: list[tuple[str, str]] = [
    ("Clear",
    "Strict monetary clear: vendor amount > $0, CW amount > $0, and CW amount >= vendor amount."),
    ("Marketplace Billing Delay",
    "Prior-period marketplace billing timing artifact expected to self-resolve next cycle. No action required."),
    ("Unmapped Partner",
    "Vendor partner or product cannot be resolved to the governed account/SKU mapping."),
    ("API Usage, Insufficient CW Billing",
    "Point-in-time API quantity > 0, vendor amount > $0, and CW amount is below vendor amount."),
    ("Vendor Billing, No CW Billing",
    "No point-in-time API signal, vendor amount > $0, and CW amount = $0."),
    ("CW Billing, No Vendor Billing",
    "Non-zero CW billing or credit while no positive vendor charge exists."),
    ("Vendor Billing, Insufficient CW Billing",
    "No point-in-time API signal, vendor amount > $0 and CW amount > $0, with CW amount below vendor amount."),
]

# Plain-English glossary of canonical EXCEPTION_TYPE buckets.
EXCEPTION_TYPE_GLOSSARY: list[tuple[str, str]] = [
    ("Marketplace Billing Delay", "Prior-period marketplace billing timing lag expected to self-resolve."),
    ("Unmapped Partner", "Vendor partner or product cannot be resolved to the governed account/SKU mapping."),
    ("API Usage, Insufficient CW Billing", "Point-in-time API quantity > 0, vendor amount > $0, and CW amount < vendor amount."),
    ("Vendor Billing, No CW Billing", "No point-in-time API signal, vendor amount > $0, and CW amount = $0."),
    ("CW Billing, No Vendor Billing", "Non-zero CW billing or credit while no positive vendor charge exists."),
    ("Vendor Billing, Insufficient CW Billing", "No point-in-time API signal, vendor amount > $0, CW amount > $0, and CW amount < vendor amount."),
    ("Clear", "Strict monetary clear: vendor amount > $0, CW amount > $0, and CW amount >= vendor amount."),
]
CHIP_LABELS = {"g": "Match", "y": "Review", "r": "Exception"}
HEALTH_LABELS = {"g": "Healthy", "y": "Review", "r": "Unhealthy"}
RANK = {"g": 0, "y": 1, "r": 2}

st.set_page_config(page_title="3rd-Party Recon Suite", layout="wide")


# ---------------------------------------------------------------------------
# CSS - one place, all styling
# ---------------------------------------------------------------------------

try:
    from streamlit.config import set_option
    set_option("theme.base", "dark")
    set_option("theme.primaryColor", "#38BDF8")
    set_option("theme.backgroundColor", "#070B16")
    set_option("theme.secondaryBackgroundColor", "#131B2E")
    set_option("theme.textColor", "#F8FAFC")
except Exception:
    pass

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
    /* Palette ported from Production_Forecast_App_V2 for consistency across CW apps */
    --cw-bg-0:#070B16;
    --cw-bg-1:#0B1220;
    --cw-bg-2:#131B2E;
    --cw-bg-3:#1B2540;
    --cw-line:rgba(148,163,184,0.20);
    --cw-line-strong:rgba(148,163,184,0.32);
    --cw-text-0:#F8FAFC;
    --cw-text-1:#CBD5E1;
    --cw-text-2:#94A3B8;
    --cw-accent:#38BDF8;
    --cw-accent-safe:#7DD3FC;
    --cw-green:#10B981;
    --cw-green-soft:rgba(16,185,129,0.14);
    --cw-gold:#F5B94A;
    --cw-gold-soft:rgba(245,185,74,0.14);
    --cw-red:#F43F5E;
    --cw-red-soft:rgba(244,63,94,0.14);

    /* Legacy aliases so existing markup keeps rendering */
    --navy:var(--cw-text-0);
    --blue:var(--cw-accent);
    --ink:var(--cw-text-0);
    --grey:var(--cw-text-2);
    --line:var(--cw-line);
    --bg:var(--cw-bg-0);
    --card:var(--cw-bg-2);
    --soft:var(--cw-bg-3);
    --green:var(--cw-green);
    --amber:var(--cw-gold);
    --red:var(--cw-red);
    --gbg:var(--cw-green-soft);
    --abg:var(--cw-gold-soft);
    --rbg:var(--cw-red-soft);
}

html, body, .stApp {
    font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif !important;
    background:var(--cw-bg-0) !important;
    color:var(--cw-text-0) !important;
    -webkit-font-smoothing:antialiased;
    -moz-osx-font-smoothing:grayscale;
    text-rendering:optimizeLegibility;
}
[data-testid="stAppViewContainer"],
[data-testid="stAppViewBlockContainer"],
header[data-testid="stHeader"] {background:var(--cw-bg-0) !important;}
.block-container {max-width:1560px; padding:0.6rem 28px 60px !important;}

div[data-testid="stMetricValue"],
[data-testid="stDataFrame"], [data-testid="stDataEditor"], [data-testid="stTable"],
.stDataFrame, table, tbody, td, th {
    font-variant-numeric:tabular-nums lining-nums;
    font-feature-settings:"tnum" 1,"lnum" 1;
}

h1, h2, h3 {color:var(--cw-text-0) !important; letter-spacing:-0.015em; font-weight:700; font-family:'Inter',sans-serif;}
h2 {font-size:1.35rem; margin:22px 0 10px; font-weight:700;}
h3 {font-size:1.1rem; margin:18px 0 8px; font-weight:700;}
[data-testid="stMarkdownContainer"] p, label, .stCaption {color:var(--cw-text-1) !important;}
a, a:hover {color:var(--cw-accent-safe) !important;}

/* ============================================================
   Unified typography scale (aligned with Renewal_Risk_System)
   - Big numbers:    1.5rem   (KPIs, strip tiles, card amounts)
   - Section labels: 0.68rem uppercase (KPI label, strip label)
   - Body:           0.92rem  (markdown, card detail)
   - Sub / caption:  0.82rem  (kpi sub, cellcap, note, foot)
   ============================================================ */
.kpi .v, .strip .m .mv, .card .amt,
div[data-testid="stMetricValue"] > div,
div[data-testid="stMetricValue"] {
    font-size:1.5rem !important;
    font-weight:700 !important;
    color:var(--cw-text-0) !important;
    letter-spacing:-0.02em !important;
    line-height:1.15 !important;
}
.kpi .l, .strip .m .ml,
div[data-testid="stMetricLabel"] > div,
div[data-testid="stMetricLabel"] p {
    font-size:0.68rem !important;
    font-weight:600 !important;
    color:var(--cw-text-2) !important;
    text-transform:uppercase !important;
    letter-spacing:0.06em !important;
    line-height:1.35 !important;
    margin-top:6px !important;
}
.kpi .s {
    font-size:0.8rem !important;
    color:var(--cw-text-2) !important;
    line-height:1.5;
}
.card .d, .note, .foot {
    font-size:0.82rem !important;
    color:var(--cw-text-2) !important;
    line-height:1.55;
}
.stand, .banner {
    font-size:0.87rem !important;
    color:var(--cw-text-1) !important;
    line-height:1.55;
}
.cellcap {
    font-size:0.72rem !important;
    color:var(--cw-text-2) !important;
    line-height:1.4;
}
.card .who {
    font-size:0.95rem !important;
    font-weight:700 !important;
    color:var(--cw-text-0) !important;
    margin-top:8px;
}
[data-testid="stMarkdownContainer"] p {
    font-size:0.92rem !important;
    line-height:1.6 !important;
}

/* Hero */
.hero {
    margin:0.15rem 0 1rem;
    background:radial-gradient(120% 180% at 0% 0%, #1D2D4E 0%, #131D33 35%, #0F172A 100%);
    border:1px solid rgba(148,163,184,0.28);
    border-radius:14px;
    box-shadow:0 10px 32px rgba(0,0,0,0.38);
    color:var(--cw-text-0);
    padding:1.1rem 1.5rem 1rem;
    position:relative; overflow:hidden;
}
.hero::before {
    content:''; position:absolute; top:0; left:0; right:0; height:2px;
    background:linear-gradient(90deg, transparent 8%, var(--cw-accent) 42%, var(--cw-gold) 62%, transparent 92%);
}
.hero h1 {margin:0; font-size:1.7rem; font-weight:700; color:var(--cw-text-0) !important; letter-spacing:-0.02em; line-height:1.1;}
.hero .sub {color:var(--cw-text-2); font-size:0.85rem; margin-top:0.3rem;}
.badge {
    display:inline-block; background:var(--cw-green-soft); color:#6EE7B7;
    border:1px solid rgba(16,185,129,0.40);
    font-size:11px; font-weight:700; padding:2px 8px; border-radius:10px;
    margin-left:10px; vertical-align:middle; text-transform:uppercase; letter-spacing:0.04em;
}

/* Controls */
.controls {
    background:linear-gradient(160deg,var(--cw-bg-2) 0%,var(--cw-bg-1) 100%);
    border:1px solid var(--cw-line);
    border-radius:12px; padding:14px 18px; margin:14px 0;
    box-shadow:0 4px 18px rgba(0,0,0,0.20);
}
.ctl-lbl {
    font-size:11px; font-weight:600; color:var(--cw-text-2);
    text-transform:uppercase; letter-spacing:.06em; margin-bottom:6px;
}

/* Streamlit widgets in dark */
div[data-baseweb="select"] > div,
div[data-baseweb="input"] > div,
div[data-baseweb="textarea"] > div,
input[type="text"], input[type="number"], input[type="date"], textarea, select {
    background:linear-gradient(160deg,var(--cw-bg-2) 0%,var(--cw-bg-1) 100%) !important;
    border:1px solid var(--cw-line-strong) !important;
    color:var(--cw-text-0) !important;
    border-radius:8px !important;
}
div[data-baseweb="select"] input,
div[data-baseweb="input"] input,
div[data-baseweb="textarea"] textarea,
input::placeholder, textarea::placeholder {color:var(--cw-text-2) !important;}
div[data-baseweb="popover"] {background:var(--cw-bg-1) !important; border:1px solid var(--cw-line) !important;}
/* Multi-select pill tags (Vendor / Year / Billing Month filters).
   BaseWeb renders the pill as either <span data-baseweb="tag"> or
   <div data-baseweb="tag"> with an inline background-color style set from
   its theme. We override with high specificity + !important and force all
   descendant text/icons white so the label is fully readable. */
div[data-baseweb="tag"],
span[data-baseweb="tag"],
[data-baseweb="select"] [data-baseweb="tag"],
[data-baseweb="select"] [role="listitem"] {
    background:#0B3A66 !important;
    background-color:#0B3A66 !important;
    color:#FFFFFF !important;
    border:1px solid #1E6BB0 !important;
    font-weight:600 !important;
}
div[data-baseweb="tag"] *,
span[data-baseweb="tag"] *,
[data-baseweb="select"] [data-baseweb="tag"] * {
    color:#FFFFFF !important;
    fill:#FFFFFF !important;
    background-color:transparent !important;
}

/* Radio strip (used for the billing-month picker) */
div[role="radiogroup"] label {
    background:var(--cw-bg-1); border:1px solid var(--cw-line);
    color:var(--cw-text-1) !important; border-radius:8px; padding:4px 10px;
    margin-right:4px !important;
}
div[role="radiogroup"] label:hover {border-color:var(--cw-accent);}

/* Buttons */
.stDownloadButton > button, .stButton > button {
    border-radius:8px !important;
    border:1px solid var(--cw-line-strong) !important;
    background:var(--cw-bg-2) !important;
    color:var(--cw-text-1) !important;
    font-weight:500 !important; font-size:0.85rem;
    transition:all 0.15s ease;
    box-shadow:0 2px 8px rgba(0,0,0,0.12) !important;
}
.stDownloadButton > button:hover, .stButton > button:hover {
    border-color:var(--cw-accent) !important;
    color:var(--cw-text-0) !important;
    background:rgba(56,189,248,0.10) !important;
    box-shadow:0 4px 14px rgba(56,189,248,0.18) !important;
}

/* KPI grids */
.kpis {display:grid; grid-template-columns:repeat(5,1fr); gap:14px; margin:14px 0;}
.kpi {
    background:linear-gradient(160deg,var(--cw-bg-2) 0%,var(--cw-bg-1) 100%);
    border:1px solid var(--cw-line);
    border-radius:12px; padding:14px 16px;
    box-shadow:0 4px 18px rgba(0,0,0,0.20);
}
.kpi .v {font-size:1.5rem; font-weight:700; color:var(--cw-text-0); line-height:1.15; letter-spacing:-0.02em;}
.kpi .l {font-size:0.68rem; color:var(--cw-text-2); margin-top:6px; text-transform:uppercase; letter-spacing:0.06em; font-weight:600;}
.kpi .s {font-size:0.78rem; color:var(--cw-text-2); margin-top:3px; line-height:1.45;}
.kpi[title] {cursor:help;}
.kpi[title]:hover {border-color:var(--cw-accent) !important; box-shadow:0 4px 22px rgba(56,189,248,0.20) !important;}

/* Strip */
.strip {
    background:linear-gradient(160deg,var(--cw-bg-2) 0%,var(--cw-bg-1) 100%);
    border:1px solid var(--cw-line); border-radius:14px;
    padding:0; margin:16px 0;
    display:grid; grid-template-columns:repeat(4,1fr); gap:0;
    box-shadow:0 4px 14px rgba(0,0,0,0.18);
    position:relative; overflow:hidden;
}
.strip::before {
    content:''; position:absolute; top:0; left:0; right:0; height:2px;
    background:linear-gradient(90deg,var(--cw-red) 0%,var(--cw-gold) 33%,var(--cw-accent) 66%,var(--cw-green) 100%);
    z-index:1;
}
.strip .m {padding:22px 20px; border-right:1px solid var(--cw-line); cursor:help;}
.strip .m[title]:hover {background:rgba(56,189,248,0.06);}
.strip .m:last-child {border-right:none;}
.strip .m .mv {font-size:1.6rem; font-weight:700; color:var(--cw-text-0); letter-spacing:-0.02em; line-height:1.15;}
.strip .m .ml {font-size:0.7rem; color:var(--cw-text-2); text-transform:uppercase; letter-spacing:0.06em; font-weight:600; margin-top:7px; line-height:1.3;}

/* Scorecard (leakage metrics inside tab) */
.scorecard {
    display:grid; grid-template-columns:repeat(4,1fr); gap:0;
    border:1px solid var(--cw-line); border-radius:12px;
    background:linear-gradient(160deg,var(--cw-bg-2) 0%,var(--cw-bg-1) 100%);
    margin:0 0 20px; overflow:hidden;
    box-shadow:0 4px 14px rgba(0,0,0,0.18);
}
.scorecard .sc {padding:16px 18px; border-right:1px solid var(--cw-line);}
.scorecard .sc:last-child {border-right:none;}
.scorecard .sc .sv {font-size:1.55rem; font-weight:700; color:var(--cw-text-0); letter-spacing:-0.02em; line-height:1.15;}
.scorecard .sc .sl {font-size:0.72rem; font-weight:700; color:var(--cw-text-1); margin-top:5px; text-transform:uppercase; letter-spacing:0.05em;}
.scorecard .sc .sd {font-size:0.72rem; color:var(--cw-text-2); margin-top:2px; line-height:1.35;}

/* Cards */
.cards {display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; margin:16px 0;}
.cards.cards-2x2 {
    grid-template-columns:repeat(2,minmax(320px,540px));
    justify-content:center;
    margin:18px auto;
    max-width:1160px;
}
.card {background:linear-gradient(140deg,var(--cw-bg-2) 0%,var(--cw-bg-1) 100%); border:1px solid var(--cw-line); border-left:5px solid var(--cw-accent); border-radius:14px; padding:22px 24px; box-shadow:0 2px 10px rgba(0,0,0,0.22); cursor:default;}
.card[title] {cursor:help;}
.card[title]:hover {border-color:var(--cw-line-strong) !important; box-shadow:0 6px 20px rgba(0,0,0,0.32) !important;}
.card.red   {border-left-color:var(--cw-red);}
.card.amber {border-left-color:var(--cw-gold);}
.card.green {border-left-color:var(--cw-green);}
.card .amt   {font-size:1.5rem; font-weight:700; color:var(--cw-text-0); letter-spacing:-0.02em; line-height:1.15;}
.card.red .amt   {color:#FB7185;}
.card.amber .amt {color:#FBBF24;}
.card.green .amt {color:#34D399;}
.card .who {font-weight:700; margin-top:8px; font-size:0.92rem; color:var(--cw-text-0); line-height:1.35;}
.card .d   {font-size:0.8rem; color:var(--cw-text-2); margin-top:5px; line-height:1.5;}

/* Chips */
.chip {
    display:inline-flex; align-items:center; gap:5px;
    font-size:0.68rem; font-weight:700; padding:3px 9px; border-radius:10px; white-space:nowrap;
    border:1px solid transparent; letter-spacing:0.03em; text-transform:uppercase;
}
.chip .dot {width:6px; height:6px; border-radius:50%; flex-shrink:0;}
.chip.g {background:var(--cw-green-soft); color:#6EE7B7; border-color:rgba(16,185,129,0.35);}
.chip.g .dot {background:var(--cw-green);}
.chip.y {background:var(--cw-gold-soft); color:#F5B94A; border-color:rgba(245,185,74,0.35);}
.chip.y .dot {background:var(--cw-gold);}
.chip.r {background:var(--cw-red-soft); color:#FB7185; border-color:rgba(244,63,94,0.35);}
.chip.r .dot {background:var(--cw-red);}

/* Standalone callout */
.stand {
    background:linear-gradient(170deg,var(--cw-bg-2) 0%,var(--cw-bg-1) 100%);
    border:1px solid var(--cw-line);
    border-left:5px solid var(--cw-accent);
    border-radius:10px; padding:14px 16px;
    font-size:13.5px; line-height:1.55; margin:10px 0;
    color:var(--cw-text-1);
}
.stand b {color:var(--cw-text-0);}

/* Banner (info/warning strip) */
.banner {
    background:linear-gradient(170deg,var(--cw-bg-2) 0%,var(--cw-bg-1) 100%);
    border:1px solid var(--cw-line);
    border-left:3px solid var(--cw-gold);
    border-radius:10px; padding:11px 14px; font-size:12.5px;
    color:var(--cw-text-1); margin:6px 0 10px;
}
.banner b, .banner strong {color:var(--cw-text-0);}

.note {font-size:12px; color:var(--cw-text-2); margin:8px 2px 14px; line-height:1.45;}
.foot {
    font-size:11px; color:var(--cw-text-2); margin-top:26px;
    border-top:1px solid var(--cw-line); padding-top:12px; line-height:1.5;
}

/* Reconciliation status and linked-invoice tables */
table.recon {
    width:100%; border-collapse:collapse;
    background:var(--cw-bg-2);
    border:1px solid var(--cw-line); border-radius:10px; overflow:hidden;
    font-size:0.85rem; color:var(--cw-text-1);
}
table.recon th, table.recon td {
    padding:9px 12px; text-align:left; border-bottom:1px solid var(--cw-line);
}
table.recon th {
    background:var(--cw-bg-3); color:var(--cw-text-0);
    font-weight:600; text-transform:uppercase; font-size:0.68rem; letter-spacing:0.06em;
    padding:10px 12px;
}
table.recon td {color:var(--cw-text-1); padding:10px 12px; font-size:0.88rem; line-height:1.5;}
table.recon tr:last-child td {border-bottom:none;}
table.recon tr:hover td {background:rgba(56,189,248,0.08);}
table.recon td.c, table.recon th.c {text-align:center;}
table.recon td.num, table.recon th.num {text-align:right; font-variant-numeric:tabular-nums;}
.cellcap {display:block; font-size:0.72rem; color:var(--cw-text-2); margin-top:3px; font-weight:400; line-height:1.35;}
.metricpair {display:block; font-variant-numeric:tabular-nums; line-height:1.25; white-space:normal;}
.metricpair .metric-main {
    display:block; color:var(--cw-text-0); font-size:1rem; font-weight:700;
}
.metricpair .metric-sub {
    display:block; color:var(--cw-text-2); font-size:0.72rem; font-weight:400; margin-top:2px;
}

/* Vendor deep-dive rows */
.det {
    background:linear-gradient(160deg,var(--cw-bg-2) 0%,var(--cw-bg-1) 100%);
    border:1px solid var(--cw-line);
    border-left:5px solid var(--cw-gold);
    border-radius:10px; padding:14px 16px; margin:12px 0;
    box-shadow:0 4px 18px rgba(0,0,0,0.18);
}
.det.r {border-left-color:var(--cw-red);}
.det.g {border-left-color:var(--cw-green);}
.det h3 {margin:0 0 2px; font-size:14.5px; color:var(--cw-text-0) !important;}
.det .sub2 {font-size:12px; color:var(--cw-text-2); margin-bottom:8px;}

/* Progress bar */
.bar {height:9px; border-radius:5px; background:var(--cw-bg-3); position:relative; overflow:hidden;
      border:1px solid var(--cw-line);}
.bar > span {position:absolute; top:0; bottom:0; left:0;}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    gap:4px; border-bottom:1px solid var(--cw-line) !important;
    padding-bottom:0; background:transparent !important;
    margin-bottom:0.4rem;
}
.stTabs [data-baseweb="tab"] {
    border-radius:0 !important; border:none !important;
    border-bottom:2px solid transparent !important;
    color:var(--cw-text-2) !important;
    font-weight:500 !important; font-size:0.88rem;
    padding:0.55rem 0.95rem 0.5rem;
    background:transparent !important;
    transition:color 0.15s ease;
    letter-spacing:0.01em;
    text-transform:none;
}
.stTabs [data-baseweb="tab"]:hover {color:var(--cw-text-1) !important;}
.stTabs [aria-selected="true"] {
    color:var(--cw-text-0) !important;
    font-weight:600 !important;
    border-bottom:2px solid var(--cw-accent) !important;
    background:transparent !important;
}

/* Dataframe */
[data-testid="stDataFrame"], [data-testid="stDataEditor"], [data-testid="stDataFrameGlideDataEditor"] {
    border:1px solid var(--cw-line) !important;
    border-radius:10px !important;
}
[data-testid="stDataFrame"] [role="columnheader"],
[data-testid="stDataEditor"] [role="columnheader"] {
    font-size:0.72rem !important;
    font-weight:600 !important;
    line-height:1.35 !important;
    letter-spacing:0.05em !important;
    text-transform:uppercase !important;
}
[data-testid="stDataFrame"] [role="gridcell"],
[data-testid="stDataEditor"] [role="gridcell"] {
    font-size:0.85rem !important;
    line-height:1.5 !important;
}
[data-testid="stElementToolbar"], [data-testid="stElementToolbarButtonContainer"] {
    background:var(--cw-bg-3) !important;
    border:1px solid var(--cw-line-strong) !important;
    border-radius:8px !important;
}
[data-testid="stElementToolbar"] button, [data-testid="stElementToolbarButtonContainer"] button {
    color:var(--cw-text-1) !important; background:transparent !important;
}
[data-testid="stElementToolbar"] svg, [data-testid="stElementToolbarButtonContainer"] svg {
    fill:var(--cw-text-1) !important; color:var(--cw-text-1) !important;
}

/* Month availability helper row */
.month-presence {
    margin-top:6px;
    display:flex;
    flex-wrap:wrap;
    gap:6px;
}
.month-pill {
    font-size:0.78rem;
    border-radius:999px;
    padding:2px 8px;
    border:1px solid var(--cw-line);
}
.month-pill.on {
    color:#6EE7B7;
    background:var(--cw-green-soft);
    border-color:rgba(16,185,129,0.35);
}
.month-pill.off {
    color:var(--cw-text-2);
    background:rgba(148,163,184,0.10);
    border-color:var(--cw-line);
}

/* Metric */
div[data-testid="stMetric"], div[data-testid="metric-container"] {
    background:linear-gradient(160deg,var(--cw-bg-2) 0%,var(--cw-bg-1) 100%) !important;
    border:1px solid var(--cw-line) !important;
    border-radius:12px !important;
    box-shadow:0 4px 18px rgba(0,0,0,0.20) !important;
    padding:0.75rem 0.95rem !important; min-height:84px;
}
div[data-testid="stMetricLabel"] > div, div[data-testid="stMetricLabel"] p {
    color:var(--cw-text-2) !important;
    font-size:0.68rem !important; font-weight:600 !important;
    text-transform:uppercase; letter-spacing:0.06em;
}
div[data-testid="stMetricValue"] > div, div[data-testid="stMetricValue"] {
    color:var(--cw-text-0) !important;
    font-size:1.5rem !important; font-weight:700 !important;
    letter-spacing:-0.02em !important;
}

/* Chat (AI Analyst tab) */
[data-testid="stChatMessage"] {
    background:linear-gradient(160deg,var(--cw-bg-2) 0%,var(--cw-bg-1) 100%) !important;
    border:1px solid var(--cw-line) !important;
    border-radius:12px !important;
    padding:0.7rem 0.9rem !important;
    margin-bottom:0.5rem !important;
    color:var(--cw-text-0) !important;
}
[data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li {color:var(--cw-text-1) !important;}
[data-testid="stChatInput"] textarea, div[data-testid="stChatInput"] {
    background:var(--cw-bg-1) !important;
    color:var(--cw-text-0) !important;
    border:1px solid var(--cw-line-strong) !important;
    border-radius:10px !important;
}

/* Alerts */
div.stAlert {
    background:linear-gradient(170deg,var(--cw-bg-2) 0%,var(--cw-bg-1) 100%) !important;
    border:1px solid var(--cw-line-strong) !important;
    border-radius:10px !important;
    box-shadow:0 4px 14px rgba(0,0,0,0.16) !important;
    color:var(--cw-text-1) !important;
}

/* Dividers */
hr, [data-testid="stDivider"] {
    border:none !important;
    height:1px !important;
    background:linear-gradient(90deg, transparent, var(--cw-line-strong), transparent) !important;
}

@media(max-width:900px) {
    .kpis {grid-template-columns:repeat(2,1fr);}
    .strip {grid-template-columns:repeat(3,1fr);}
    .cards {grid-template-columns:1fr;}
    .cards.cards-2x2 {
        grid-template-columns:1fr;
        max-width:none;
    }
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

@st.cache_data(ttl=DATA_TTL_SECONDS, show_spinner=False)
def run_query(sql: str, freshness_key: str = "") -> pd.DataFrame:
    """Execute a SQL query using the Snowflake-hosted Streamlit session connection."""
    _ = freshness_key
    conn = st.connection("snowflake", ttl=DATA_TTL_SECONDS)
    return conn.query(sql)


def upper_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).upper() for c in out.columns]
    return out


@st.cache_data(ttl=FRESHNESS_TTL_SECONDS, show_spinner=False)
def fetch_freshness_key() -> str:
    """Return a freshness key based on table LAST_ALTERED timestamps.

    Only references tables the current pipeline actually builds. Legacy
    THIRD_PARTY_RECON_SKU_MAP_PROD and THIRD_PARTY_RECON_PARTNER_MAP_PROD
    were removed \u2014 they never existed in the current pipeline and their
    absence returned stale freshness keys.
    """
    try:
        conn = st.connection("snowflake", ttl=FRESHNESS_TTL_SECONDS)
        df = conn.query("""
            SELECT COALESCE(
                LISTAGG(
                    TABLE_NAME || '=' || TO_VARCHAR(LAST_ALTERED, 'YYYY-MM-DD HH24:MI:SS.FF3'),
                    '|'
                ) WITHIN GROUP (ORDER BY TABLE_NAME),
                ''
            ) AS K
            FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = 'DBT_NFOLD_TRANSFORMATION'
              AND TABLE_NAME IN (
                  'THIRD_PARTY_RECON_DETAIL_PROD',
                  'THIRD_PARTY_RECON_OUTPUT_PROD',
                  'THIRD_PARTY_RECON_SUMMARY_PROD',
                  'THIRD_PARTY_RECON_VENDOR_USAGE_PROD',
                  'THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD'
              )
        """)
        if df.empty:
            return ""
        return str(df.iloc[0, 0] or "")
    except Exception:
        return ""


def _try_query(sql: str, freshness: str) -> pd.DataFrame:
    """Best-effort SELECT; return an empty DataFrame if the table doesn't exist yet."""
    try:
        return run_query(sql, freshness)
    except Exception:
        return pd.DataFrame()


def _normalize_detail(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure all columns the app references exist in the detail DataFrame.

    Any column not present in the source table is added with a sensible default
    (0 for numeric, empty string for text, False for flags). This prevents
    KeyErrors when the schema evolves or when some vendor _PROD tables were
    built before a column was added.
    """
    if df.empty:
        return df
    numeric_zero = [
        "VENDOR_QUANTITY", "VENDOR_UNIT_PRICE", "VENDOR_AMOUNT",
        "ZUORA_QUANTITY", "ZUORA_UNIT_PRICE", "ZUORA_AMOUNT",
        "MARKETPLACE_QUANTITY", "MARKETPLACE_UNIT_PRICE", "MARKETPLACE_AMOUNT",
        "TOTAL_BILLING_QUANTITY", "TOTAL_BILLING_UNIT_PRICE", "TOTAL_BILLING_AMOUNT",
        "QTY_DELTA", "ABS_QTY_DELTA", "AMOUNT_DELTA", "ABS_AMOUNT_DELTA",
        "MARKETPLACE_TIMING_QUANTITY", "VENDOR_SOURCE_ROW_COUNT",
        "CONTRACT_COST_BASIS_QUANTITY", "CONTRACT_COST_BASIS_AMOUNT", "CONTRACT_COST_RATE",
        "BILLING_VS_COST_DELTA_PER_SEAT", "BILLING_VS_COST_DOLLAR_IMPACT", "BILLING_VS_COST_PCT",
        "VENDOR_VS_CONTRACT_DELTA_PER_SEAT", "VENDOR_VS_CONTRACT_PCT",
        "VENDOR_VS_CONTRACT_DOLLAR_IMPACT",
        "MDR_BUNDLE_AMOUNT", "MDR_BUNDLE_QUANTITY",
        "STANDALONE_LICENSE_AMOUNT", "GROSS_MARGIN_PCT", "S1_LICENSE_MARGIN_PCT",
        "EST_DOLLAR_IMPACT",
        # Point-in-time vs. cycle-average API $ (OUTPUT_PROD 2026-08-28).
        # API_AMOUNT     = API_QUANTITY     * VENDOR_UNIT_PRICE
        # AVG_API_AMOUNT = AVG_API_QUANTITY * VENDOR_UNIT_PRICE
        # API_AVG_MINUS_POINT_AMOUNT = AVG_API_AMOUNT - API_AMOUNT
        "API_AMOUNT", "AVG_API_AMOUNT", "API_AVG_MINUS_POINT_AMOUNT",
    ]
    text_empty = [
        "VENDOR", "SF_ID", "SALESFORCE_ACCOUNT_ID", "SALESFORCE_ACCOUNT_URL",
        "CMS_ID", "CW_PARTNER_NAME", "CW_PARENT_COMPANY", "MATCHED_INVOICE_SKU",
        "VENDOR_PARTNER_NAME", "VENDOR_PRODUCT", "VENDOR_PRODUCT_SKU", "CW_SKU",
        "SKU_MATCH_GROUP", "SOURCE_VENDOR_PRODUCTS", "RETENTION_DESCS",
        "CW_SKUS", "ZUORA_SKUS", "MARKETPLACE_SKUS", "BILLING_SOURCE_MIX",
        "ZUORA_INV", "MP_INV",
        "VENDOR_INVOICE_SKU", "VENDOR_INVOICE_RATE_SOURCE",
        "PARTNER_MATCH_METHODS", "SKU_MAPPING_SOURCES",
        "CONTRACT_RATE_SOURCE_DOCS",
        "OUTCOME_FLAG", "INVESTIGATION_REASON", "BILLING_ACTION_REQUIRED",
        "BILLING_CATEGORY",
        "CONTRACT_PRICE_FLAG", "VENDOR_VS_CONTRACT_FLAG",
        "CANONICAL_OUTCOME", "CANONICAL_SEVERITY", "CANONICAL_ACTION", "CANONICAL_REASON",
        "EXCEPTION_TYPE",
        # Pipeline v23 precomputed columns — normalized here so old data
        # (built before the pipeline v23 refresh) still renders. When the
        # column exists the app skips per-row Python classification.
        "ACTION_NEEDED", "CASE_ID",
        # 2026-08-31 board-ready display columns — canonical single-name
        # replacements for pipe-delimited VENDOR_PRODUCT / VENDOR_PARTNER_NAME.
        # Fall back to empty string here for pre-refresh cached data; the
        # UI code prefers *_DISPLAY when present and falls through to the
        # raw fields when they aren't.
        "PRODUCT_DISPLAY", "PARTNER_DISPLAY_NAME",
    ]
    bool_false = [
        "DUPLICATE_BILLING_FLAG", "MARKETPLACE_TIMING_FLAG", "MATERIAL_BELOW_COST_FLAG",
        # Pipeline v23 boolean queue-membership flags.
        "IS_LEAKAGE", "IS_FINANCE_QUEUE", "IS_OPS_QUEUE",
        "IS_TIMING_QUEUE", "IS_CLEAR",
        # 2026-08-31 aggregator/distributor flag from PARTNER_CANONICAL CTE.
        "IS_AGGREGATOR_ACCOUNT",
    ]
    # Fast path: pipeline v23 emits every required column, so on the common
    # cache-miss we can skip the ~100 MB defensive copy entirely.
    missing_numeric = [c for c in numeric_zero if c not in df.columns]
    missing_text = [c for c in text_empty if c not in df.columns]
    missing_bool = [c for c in bool_false if c not in df.columns]
    if not (missing_numeric or missing_text or missing_bool):
        return df
    out = df.copy()
    for col in missing_numeric:
        out[col] = 0.0
    for col in missing_text:
        out[col] = ""
    for col in missing_bool:
        out[col] = False
    return out


def _normalize_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure all summary columns the app references exist with zero defaults."""
    if df.empty:
        return df
    numeric_zero = [
        "TOTAL_VENDOR_SEATS", "TOTAL_BILLING_SEATS",
        "TOTAL_VENDOR_AMOUNT", "TOTAL_BILLING_AMOUNT",
        "TOTAL_ROWS", "PERFECT_MATCH_ROWS",
    ]
    missing = [c for c in numeric_zero if c not in df.columns]
    if not missing:
        return df
    out = df.copy()
    for col in missing:
        out[col] = 0.0
    return out


def _load_combined_vendor(
    freshness: str,
    vendor_name: str,
    coverage_table: str | None,
    llm_summary_table: str | None,
) -> dict[str, pd.DataFrame]:
    """Load a single vendor's slice of the unified marts.

    Serves the per-vendor frames out of the shared cache populated by
    `_load_all_recon_frames`. Coverage / LLM summary lookups stay
    per-vendor (small tables). This collapses N vendor round-trips to
    Snowflake into ONE query for the shared summary + detail marts,
    which is the single biggest startup-latency win.
    """
    return _load_combined_vendor_impl(
        freshness, SLICE_SCHEMA_VERSION, vendor_name, coverage_table, llm_summary_table
    )


# Columns we drop client-side if the pipeline happens to emit them. Kept
# as a pandas-side filter (not a SQL EXCLUDE) so an unknown column name
# never silently kills the entire detail load — the SELECT stays valid
# and _drop_heavy_columns just no-ops when the column is absent.
_HEAVY_DETAIL_COLUMNS: tuple[str, ...] = (
    "VENDOR_CONTEXT",
    "VENDOR_CONTEXT_JSON",
    "VENDOR_METADATA",
)


def _drop_heavy_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    drop = [c for c in _HEAVY_DETAIL_COLUMNS if c in df.columns]
    return df.drop(columns=drop) if drop else df


@st.cache_data(ttl=DATA_TTL_SECONDS, show_spinner=False)
def _load_all_recon_frames(
    freshness: str, schema_version: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One round-trip: pull the WHOLE THIRD_PARTY_RECON_SUMMARY_PROD and
    THIRD_PARTY_RECON_OUTPUT_PROD, then slice per-vendor in Python.

    Before: N vendors × 2 queries = 2N Snowflake round-trips at startup
    (and every time the freshness key changed). Now: 2 queries total,
    regardless of how many vendors are wired up.
    """
    _ = schema_version  # cache-bust key only
    summary = _normalize_summary(upper_cols(
        _try_query(
            f"""
            SELECT *
            FROM {SCHEMA}.THIRD_PARTY_RECON_SUMMARY_PROD
            ORDER BY VENDOR, BILLING_MONTH
            """,
            freshness,
        )
    ))
    detail = _normalize_detail(_drop_heavy_columns(upper_cols(
        _try_query(
            f"""
            SELECT *
            FROM {SCHEMA}.THIRD_PARTY_RECON_OUTPUT_PROD
            """,
            freshness,
        )
    )))
    for df in (summary, detail):
        if not df.empty and "BILLING_MONTH" in df.columns:
            df["BILLING_MONTH"] = pd.to_datetime(df["BILLING_MONTH"])
    return summary, detail


@st.cache_data(ttl=DATA_TTL_SECONDS, show_spinner=False)
def _load_combined_vendor_impl(
    freshness: str,
    schema_version: str,
    vendor_name: str,
    coverage_table: str | None,
    llm_summary_table: str | None,
) -> dict[str, pd.DataFrame]:
    """Cached loader. schema_version is a cache-bust key; changing it forces a
    full re-query of Snowflake regardless of freshness timestamp.

    Pulls the summary + detail slice for `vendor_name` out of the shared
    all-vendor frame cache, then fetches the (small) vendor-specific
    coverage + LLM summary tables. Ghost-month trim is baked in so
    downstream frames stop at the last month with real vendor-file data.
    """
    all_summary, all_detail = _load_all_recon_frames(freshness, schema_version)
    if all_summary.empty or "VENDOR" not in all_summary.columns:
        summary = all_summary.iloc[0:0].copy()
    else:
        summary = all_summary[all_summary["VENDOR"] == vendor_name].reset_index(drop=True)
    if all_detail.empty or "VENDOR" not in all_detail.columns:
        detail = all_detail.iloc[0:0].copy()
    else:
        detail = all_detail[all_detail["VENDOR"] == vendor_name].reset_index(drop=True)
    coverage_sql = (
        f"SELECT * FROM {SCHEMA}.{coverage_table} ORDER BY BILLING_MONTH"
        if coverage_table
        else ""
    )
    coverage = upper_cols(
        _try_query(coverage_sql, freshness) if coverage_sql else pd.DataFrame()
    )
    llm_sql = (
        f"""
        SELECT RUN_TS, PROVIDER, MODEL, SUMMARY_GENERATION_SECONDS, SUMMARY_TEXT
        FROM {SCHEMA}.{llm_summary_table}
        ORDER BY RUN_TS DESC
        LIMIT 1
        """
        if llm_summary_table
        else ""
    )
    llm_summary = upper_cols(
        _try_query(llm_sql, freshness) if llm_sql else pd.DataFrame()
    )
    for df in (summary, detail, coverage):
        if not df.empty and "BILLING_MONTH" in df.columns:
            df["BILLING_MONTH"] = pd.to_datetime(df["BILLING_MONTH"])
    if coverage.empty and "BILLING_MONTH" not in coverage.columns:
        coverage = pd.DataFrame(columns=["BILLING_MONTH"])

    # Sole month-scope rule: restrict all frames to months where the
    # SUMMARY_PROD row shows DATA_LOAD_STATUS='LOADED' (or USAGE_ROW_COUNT>0
    # as a fallback if the status column is absent). VENDOR_SOURCE_ROW_COUNT
    # in DETAIL/OUTPUT_PROD is a literal `1` for every row (see
    # build_third_party_recon_output_prod.py line 447) so it cannot be used
    # to distinguish loaded from unloaded months. SUMMARY_PROD carries the
    # actual load signal from THIRD_PARTY_RECON_VENDOR_USAGE_PROD counts.
    if not summary.empty and "BILLING_MONTH" in summary.columns:
        _load_mask = None
        if "DATA_LOAD_STATUS" in summary.columns:
            _load_mask = summary["DATA_LOAD_STATUS"].astype(str).str.upper().eq("LOADED")
        elif "USAGE_ROW_COUNT" in summary.columns:
            _load_mask = pd.to_numeric(summary["USAGE_ROW_COUNT"], errors="coerce").fillna(0) > 0
        if _load_mask is not None:
            _vendor_file_months = set(pd.to_datetime(summary.loc[_load_mask, "BILLING_MONTH"]).unique())
            summary = summary[pd.to_datetime(summary["BILLING_MONTH"]).isin(_vendor_file_months)].reset_index(drop=True)
            if not detail.empty and "BILLING_MONTH" in detail.columns:
                detail = detail[pd.to_datetime(detail["BILLING_MONTH"]).isin(_vendor_file_months)].reset_index(drop=True)
            if not coverage.empty and "BILLING_MONTH" in coverage.columns:
                coverage = coverage[pd.to_datetime(coverage["BILLING_MONTH"]).isin(_vendor_file_months)].reset_index(drop=True)

    return {
        "summary": summary,
        "detail": detail,
        "coverage": coverage,
        "llm_summary": llm_summary,
    }


# ---------------------------------------------------------------------------
# Vendor registry - the seam for adding vendors beyond the POC.
# ---------------------------------------------------------------------------

VENDORS: list[dict[str, Any]] = [
    {
        "key": "proofpoint",
        "name": "Proofpoint",
        "category": "Email Security",
        "status": "active",
        "note": (
            "Reads the shared THIRD_PARTY_RECON marts after the Proofpoint "
            "vendor pipeline rebuilds its source detail and summary tables."
        ),
        "loader": lambda freshness: _load_combined_vendor(
            freshness,
            "Proofpoint",
            "PROOFPOINT_RAW_PARTNER_COVERAGE",
            "PROOFPOINT_INVESTIGATION_SUMMARY_OUTPUT",
        ),
    },
    {
        "key": "sentinelone",
        "name": "SentinelOne",
        "category": "Endpoint Security",
        "status": "active",
        "note": (
            "Reads the shared THIRD_PARTY_RECON marts after the SentinelOne "
            "vendor pipeline rebuilds its source detail and summary tables."
        ),
        "loader": lambda freshness: _load_combined_vendor(
            freshness,
            "SentinelOne",
            "SENTINELONE_RAW_PARTNER_COVERAGE",
            "SENTINELONE_INVESTIGATION_SUMMARY_OUTPUT",
        ),
    },
    {
        "key": "webroot",
        "name": "Webroot",
        "category": "Endpoint Security",
        "status": "active",
        "note": (
            "Reads the shared THIRD_PARTY_RECON marts after the Webroot "
            "vendor pipeline rebuilds WEBROOT_RECON_DETAIL_APP and its "
            "summary. TRT usage + RMM discount details are in vendor_context."
        ),
        "loader": lambda freshness: _load_combined_vendor(
            freshness,
            "Webroot",
            None,
            None,
        ),
    },
    {
        "key": "acronis",
        "name": "Acronis",
        "category": "Backup & DR",
        "status": "active",
        "note": (
            "Reads the shared THIRD_PARTY_RECON marts after the Acronis "
            "vendor pipeline rebuilds ACRONIS_RECON_DETAIL and summary. "
            "Marketplace timing detail is preserved in vendor_context."
        ),
        "loader": lambda freshness: _load_combined_vendor(
            freshness,
            "Acronis",
            None,
            None,
        ),
    },
    {
        "key": "keepit",
        "name": "KeepIT",
        "category": "SaaS Backup",
        "status": "active",
        "note": (
            "Reads the shared THIRD_PARTY_RECON marts after the KeepIT "
            "vendor pipeline rebuilds KEEPIT_RECON_DETAIL and summary. "
            "Source family, CMS IDs and takeout support are in vendor_context."
        ),
        "loader": lambda freshness: _load_combined_vendor(
            freshness,
            "KeepIT",
            None,
            None,
        ),
    },
    {
        "key": "auvik",
        "name": "Auvik",
        "category": "Network Monitoring",
        "status": "active",
        "note": "Uses shared combined marts after Auvik vendor pipeline refresh.",
        "loader": lambda freshness: _load_combined_vendor(freshness, "Auvik", None, None),
    },
    {
        "key": "bitdefender",
        "name": "Bitdefender",
        "category": "Endpoint Security",
        "status": "active",
        "note": "Uses shared combined marts after Bitdefender vendor pipeline refresh.",
        "loader": lambda freshness: _load_combined_vendor(freshness, "Bitdefender", None, None),
    },
    {
        "key": "eset",
        "name": "ESET",
        "category": "Endpoint Security",
        "status": "active",
        "note": "Uses shared combined marts after ESET vendor pipeline refresh.",
        "loader": lambda freshness: _load_combined_vendor(freshness, "ESET", None, None),
    },
    {
        "key": "exium",
        "name": "Exium",
        "category": "SASE",
        "status": "active",
        "note": "Uses shared combined marts after Exium vendor pipeline refresh.",
        "loader": lambda freshness: _load_combined_vendor(freshness, "Exium", None, None),
    },
]


def vendor_by_key(key: str) -> dict[str, Any]:
    """Return the vendor config. Prefers the active copy (which carries loaded ``data``)."""
    active = globals().get("active_vendors")
    if active:
        for v in active:
            if v["key"] == key:
                return v
    return next(v for v in VENDORS if v["key"] == key)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_num(value: Any, decimals: int = 0) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.{decimals}f}"


def fmt_money(value: Any, decimals: int = 0) -> str:
    if value is None or pd.isna(value):
        return "-"
    v = float(value)
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.{decimals}f}"


def fmt_short_money(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    v = float(value)
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.2f}M"
    if a >= 1_000:
        return f"{sign}${a / 1_000:.0f}K"
    return f"{sign}${a:,.0f}"


def fmt_pct(value: Any, decimals: int = 1) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:.{decimals}f}%"


def month_label(value: Any) -> str:
    return pd.to_datetime(value).strftime("%b %Y")


def full_month(value: Any) -> str:
    return pd.to_datetime(value).strftime("%B %Y")


def chip_html(status: str, label: str | None = None) -> str:
    txt = label or CHIP_LABELS[status]
    return f'<span class="chip {status}"><span class="dot"></span>{txt}</span>'


def kpi_html(value: str, label: str, sub: str = "", hint: str = "") -> str:
    title_attr = f' title="{hint}"' if hint else ""
    return (
        f'<div class="kpi"{title_attr}><div class="v">{value}</div>'
        f'<div class="l">{label}</div>'
        f'<div class="s">{sub}</div></div>'
    )


def strip_tile(value: str, label: str, hint: str = "") -> str:
    title_attr = f' title="{hint}"' if hint else ""
    return f'<div class="m"{title_attr}><div class="mv">{value}</div><div class="ml">{label}</div></div>'


def score_tile(value: str, label: str, sub: str = "") -> str:
    return (
        f'<div class="sc"><div class="sv">{value}</div>'
        f'<div class="sl">{label}</div>'
        f'<div class="sd">{sub}</div></div>'
    )


def card_html(color: str, amount: str, title: str, detail: str, hint: str = "") -> str:
    title_attr = f' title="{hint}"' if hint else ""
    return (
        f'<div class="card {color}"{title_attr}><div class="amt">{amount}</div>'
        f'<div class="who">{title}</div><div class="d">{detail}</div></div>'
    )


def render_cards(cards: list[str], layout: str = "default") -> None:
    classes = "cards cards-2x2" if layout == "2x2" else "cards"
    st.markdown(f'<div class="{classes}">{"".join(cards)}</div>', unsafe_allow_html=True)


def insight_cards_from_summary(summary_text: str, max_cards: int = 6) -> list[str]:
    """Parse AI summary text into compact insight cards when possible."""
    if not summary_text:
        return []

    lines = [ln.strip() for ln in summary_text.splitlines()]
    parsed: list[str] = []
    colors = ["red", "amber", "green"]
    i = 0
    while i < len(lines) and len(parsed) < max_cards:
        line = lines[i]
        if not line or line.startswith("#"):
            i += 1
            continue

        metric_like = bool(re.match(r"^(\$|\d+%|[-+]?\d+%|\d+\s*/\s*\d+)", line))
        if not metric_like:
            i += 1
            continue

        j = i + 1
        while j < len(lines) and not lines[j]:
            j += 1
        if j >= len(lines):
            break
        title = lines[j].replace("**", "")

        k = j + 1
        detail_parts: list[str] = []
        while k < len(lines) and lines[k]:
            detail_parts.append(lines[k])
            k += 1
        detail = " ".join(detail_parts) if detail_parts else "AI-generated portfolio insight."

        color = colors[len(parsed) % len(colors)]
        parsed.append(card_html(color, line, title, detail))
        i = k + 1

    return parsed


def normalize_summary_markdown(summary_text: str) -> str:
    """Normalize pipeline summary text so markdown doesn't mis-style metric values."""
    if not summary_text:
        return ""
    cleaned = summary_text.replace("\u00A0", " ")
    # Remove both paired and stray backticks to avoid accidental inline-code coloring.
    cleaned = re.sub(r"`([^`\n]+)`", r"\1", cleaned)
    cleaned = cleaned.replace("`", "")
    return cleaned


def fmt_est_timestamp(value: Any) -> str:
    """Format timestamps as US Eastern time for executive-facing footnotes."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "-"
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return "-"
    try:
        # Snowflake freshness strings are often timezone-naive but already Eastern.
        if getattr(ts, "tzinfo", None) is None:
            ts_est = ts.tz_localize("US/Eastern")
        else:
            ts_est = ts.tz_convert("US/Eastern")
    except Exception:
        return str(value)
    return ts_est.strftime("%Y-%m-%d %I:%M %p %Z")


def latest_freshness_timestamp(freshness_key: str) -> pd.Timestamp | None:
    """Extract the latest table timestamp from the internal cache fingerprint."""
    timestamps: list[pd.Timestamp] = []
    for table_entry in str(freshness_key or "").split("|"):
        _, separator, raw_timestamp = table_entry.partition("=")
        if not separator:
            continue
        try:
            parsed = pd.Timestamp(raw_timestamp.strip())
        except (TypeError, ValueError):
            continue
        if pd.isna(parsed):
            continue
        timestamps.append(parsed)
    return max(timestamps) if timestamps else None


# ---------------------------------------------------------------------------
# Reconciliation math (per-vendor detail slice)
# ---------------------------------------------------------------------------

def outcome_count(detail: pd.DataFrame, flag: str) -> int:
    if detail.empty or "EXCEPTION_TYPE" not in detail.columns:
        return 0
    return int((detail["EXCEPTION_TYPE"] == flag).sum())


def outcome_qty(detail: pd.DataFrame, flag: str) -> float:
    if detail.empty or "EXCEPTION_TYPE" not in detail.columns:
        return 0.0
    return float(detail.loc[detail["EXCEPTION_TYPE"] == flag, "ABS_QTY_DELTA"].fillna(0).sum())


# ---------------------------------------------------------------------------
# Display bucket constants (2026-08-12 v2 refresh)
# ---------------------------------------------------------------------------
# EXCEPTION_TYPE is computed once by the pipeline's strict canonical
# classifier. The app only validates and displays that authoritative value.

BUCKET_CLEAR                 = "Clear"
BUCKET_UNMAPPED              = "Unmapped Partner"
BUCKET_MARKETPLACE_TIMING    = "Marketplace Billing Delay"
BUCKET_TRT_NO_BILLING        = "API Usage, Insufficient CW Billing"
BUCKET_VENDOR_NO_CW_BILLING  = "Vendor Billing, No CW Billing"
BUCKET_CW_NO_VENDOR_BILLING  = "CW Billing, No Vendor Billing"
BUCKET_VENDOR_NO_CW          = "Vendor Billing, Insufficient CW Billing"

# Ordered list drives display ordering / iteration.
EXCEPTION_BUCKETS = [
    BUCKET_MARKETPLACE_TIMING,
    BUCKET_UNMAPPED,
    BUCKET_TRT_NO_BILLING,
    BUCKET_VENDOR_NO_CW_BILLING,
    BUCKET_CW_NO_VENDOR_BILLING,
    BUCKET_VENDOR_NO_CW,
    BUCKET_CLEAR,
]

# Bucket -> plain-English action string. Used in tables + queue tiles.
FLAG_DISPLAY_ACTION: dict[str, str] = {
    BUCKET_CLEAR: "None",
    BUCKET_UNMAPPED: "Data / Catalog: correct partner or SKU mapping",
    BUCKET_MARKETPLACE_TIMING: "No action \u2014 prior-month invoice expected next cycle",
    BUCKET_TRT_NO_BILLING: "Finance: close billing gap for TRT/API-confirmed usage",
    BUCKET_VENDOR_NO_CW_BILLING: "Finance / Sales: onboard billing \u2014 vendor charged CW with no CW rebill to partner",
    BUCKET_CW_NO_VENDOR_BILLING: "Ops: verify vendor-side attribution or retire the stale CW subscription",
    BUCKET_VENDOR_NO_CW: "Finance / Sales: close billing gap \u2014 vendor materially ahead of CW",
}

# Queue category groupings used by the Monthly Reconciliation action tiles.
FINANCE_QUEUE_CATEGORIES = [
    BUCKET_VENDOR_NO_CW_BILLING,
    BUCKET_VENDOR_NO_CW,
    BUCKET_TRT_NO_BILLING,
]
OPS_QUEUE_CATEGORIES = [
    BUCKET_CW_NO_VENDOR_BILLING,
    BUCKET_UNMAPPED,
]
TIMING_QUEUE_CATEGORIES = [BUCKET_MARKETPLACE_TIMING]
KNOWN_NO_ACTION_CATEGORIES: list[str] = []

# ---------------------------------------------------------------------------
# Revenue leakage classification (unified 10-flag taxonomy)
# ---------------------------------------------------------------------------
# TRUE LEAKAGE: vendor charges CW but CW under-bills (or no-bills) the customer.
# Uses canonical OUTCOME_FLAG / EXCEPTION_TYPE values from the pipeline.
LEAKAGE_FLAGS = [
    "Vendor Billing, No CW Billing",
    "API Usage, Insufficient CW Billing",
    "Vendor Billing, Insufficient CW Billing",
]
# Timing-only rows self-resolve; separate from actionable exceptions.
TIMING_ONLY_FLAGS = ["Marketplace Billing Delay"]


# ---------------------------------------------------------------------------
# Contract-price overlay helpers
# ---------------------------------------------------------------------------
# The recon layer attaches a governed per-seat contract cost (see
# SENTINELONE_CONTRACT_RATES). Every row carries CONTRACT_PRICE_FLAG plus the
# per-seat delta, dollar impact, and a MATERIAL_BELOW_COST_FLAG that filters
# out sub-penny/rounding noise so leadership only sees real discounting.

CONTRACT_PRICE_COLS = (
    "CONTRACT_PRICE_FLAG",
    "MATERIAL_BELOW_COST_FLAG",
    "BILLING_VS_COST_DOLLAR_IMPACT",
    "CONTRACT_COST_RATE",
    "TOTAL_BILLING_UNIT_PRICE",
)


def has_contract_price(detail: pd.DataFrame) -> bool:
    """Return True when the recon layer has the contract-price overlay attached
    with real values (not just the empty-string default from _normalize_detail)."""
    return (
        not detail.empty
        and "CONTRACT_PRICE_FLAG" in detail.columns
        and detail["CONTRACT_PRICE_FLAG"].ne("").any()
    )


def _contract_mask(detail: pd.DataFrame, flag: str, material_only: bool = False):
    mask = detail["CONTRACT_PRICE_FLAG"] == flag
    if material_only and "MATERIAL_BELOW_COST_FLAG" in detail.columns:
        mask &= detail["MATERIAL_BELOW_COST_FLAG"].fillna(False).astype(bool)
    return mask


def contract_above_cost_dollars(detail: pd.DataFrame) -> float:
    if not has_contract_price(detail):
        return 0.0
    return float(
        detail.loc[_contract_mask(detail, "ABOVE_COST"), "BILLING_VS_COST_DOLLAR_IMPACT"]
        .fillna(0)
        .sum()
    )


def contract_below_cost_dollars(detail: pd.DataFrame, material_only: bool = True) -> float:
    if not has_contract_price(detail):
        return 0.0
    return float(
        detail.loc[
            _contract_mask(detail, "BELOW_COST_DISCOUNT", material_only=material_only),
            "BILLING_VS_COST_DOLLAR_IMPACT",
        ]
        .fillna(0)
        .sum()
    )


def contract_below_cost_accounts(detail: pd.DataFrame, material_only: bool = True) -> int:
    if not has_contract_price(detail) or "SF_ID" not in detail.columns:
        return 0
    sub = detail.loc[
        _contract_mask(detail, "BELOW_COST_DISCOUNT", material_only=material_only),
        "SF_ID",
    ].astype(str)
    sub = sub[~sub.isin(["", "None", "nan", "NaT"])]
    return int(sub.nunique())


# Non-overlapping (safe-to-sum) contract-price leakage.
# Rate leakage and quantity leakage are ORTHOGONAL dimensions on the same row.
# When OUTCOME_FLAG != CLEAR (quantity issue) AND CONTRACT_PRICE_FLAG=BELOW_COST_DISCOUNT
# (rate issue) are both present, the row already contributes to the quantity-leakage
# bucket. To avoid double counting we treat the rate-leakage bucket for "combined"
# reporting as ONLY the material below-cost rows where OUTCOME_FLAG='CLEAR'.

def contract_below_cost_dollars_clean_only(
    detail: pd.DataFrame, material_only: bool = True
) -> float:
    """Below-cost loss $ restricted to rows with EXCEPTION_TYPE='Clear' (safe to add
    to quantity-leakage without double counting)."""
    if not has_contract_price(detail) or "EXCEPTION_TYPE" not in detail.columns:
        return 0.0
    mask = _contract_mask(detail, "BELOW_COST_DISCOUNT", material_only=material_only)
    mask &= detail["EXCEPTION_TYPE"].astype(str) == "Clear"
    return float(detail.loc[mask, "BILLING_VS_COST_DOLLAR_IMPACT"].fillna(0).sum())


# Label used in the recon team queue for rows surfacing via the vendor-over mask.
# Points to "Vendor Billing, Insufficient CW Billing" after bucket consolidation.
VENDOR_BILLING_OVER_CW_LABEL = BUCKET_VENDOR_NO_CW


def _classify_bucket_series(detail: pd.DataFrame) -> pd.Series:
    """Return a Series aligned to `detail.index` giving each row its canonical
    EXCEPTION_TYPE bucket. Rejects invalid publication data rather than deriving
    or silently remapping a replacement bucket in the app.
    """
    if detail.empty:
        return pd.Series([], dtype=object, index=detail.index)
    canonical = {
        BUCKET_CLEAR,
        BUCKET_UNMAPPED,
        BUCKET_MARKETPLACE_TIMING,
        BUCKET_TRT_NO_BILLING,
        BUCKET_VENDOR_NO_CW_BILLING,
        BUCKET_CW_NO_VENDOR_BILLING,
        BUCKET_VENDOR_NO_CW,
    }
    if "EXCEPTION_TYPE" not in detail.columns:
        raise ValueError("Published reconciliation data is missing EXCEPTION_TYPE")
    raw = detail["EXCEPTION_TYPE"].astype(str).str.strip()
    unknown_tokens = {"", "UNKNOWN", "NONE", "NULL", "NAN", "NAT"}
    invalid = raw.str.upper().isin(unknown_tokens) | ~raw.isin(canonical)
    if invalid.any():
        values = sorted(raw.loc[invalid].unique().tolist())
        raise ValueError(f"Invalid canonical EXCEPTION_TYPE value(s): {values}")
    return raw


# ---------------------------------------------------------------------------
# Bucket-based masks. Simple wrappers around the deterministic EXCEPTION_TYPE
# column since buckets are already mutually exclusive.
# ---------------------------------------------------------------------------


def _bucket_series(detail: pd.DataFrame) -> pd.Series:
    return _classify_bucket_series(detail).astype(str)


def combined_vendor_over_mask(detail: pd.DataFrame) -> pd.Series:
    """Rows where vendor is billing/using materially more than CW."""
    if detail.empty:
        return pd.Series([], dtype=bool, index=detail.index)
    return _bucket_series(detail) == BUCKET_VENDOR_NO_CW


# ---------------------------------------------------------------------------
# Canonical Revenue Leakage definition = Finance Queue bucket cohort.
# Each bucket is mutually exclusive so this is a simple isin() with no
# deduplication needed.
# ---------------------------------------------------------------------------

FINANCE_QUEUE_BUCKETS = frozenset({
    BUCKET_VENDOR_NO_CW_BILLING,
    BUCKET_VENDOR_NO_CW,
    BUCKET_TRT_NO_BILLING,
})


def finance_queue_mask(detail: pd.DataFrame) -> pd.Series:
    """Canonical Revenue Leakage row cohort (buckets 5 + 7)."""
    if detail.empty:
        return pd.Series([], dtype=bool, index=detail.index)
    # Fast path: pipeline v23+ pre-computes IS_FINANCE_QUEUE as a bool
    # column, so we just read it. Falls back to string bucket comparison
    # for historical data.
    if "IS_FINANCE_QUEUE" in detail.columns:
        col = detail["IS_FINANCE_QUEUE"]
        if col.dtype == bool:
            return col
        return col.fillna(False).astype(bool)
    return _bucket_series(detail).isin(FINANCE_QUEUE_BUCKETS)


def _revenue_leakage_dollars(detail: pd.DataFrame) -> float:
    if detail.empty or "AMOUNT_DELTA" not in detail.columns:
        return 0.0
    mask = finance_queue_mask(detail)
    amt = pd.to_numeric(detail.loc[mask, "AMOUNT_DELTA"], errors="coerce").fillna(0).abs()
    return float(amt.sum())


def _revenue_leakage_accounts(detail: pd.DataFrame) -> int:
    if detail.empty or "SF_ID" not in detail.columns:
        return 0
    mask = finance_queue_mask(detail)
    accts = detail.loc[mask, "SF_ID"].astype(str)
    accts = accts[~accts.isin(["", "None", "nan", "NaT"])]
    return int(accts.nunique())


def flag_dollars(detail: pd.DataFrame, flags: list[str]) -> float:
    """Absolute dollar impact for rows matching canonical EXCEPTION_TYPE flags."""
    if detail.empty or "EXCEPTION_TYPE" not in detail.columns:
        return 0.0
    mask = detail["EXCEPTION_TYPE"].astype(str).isin(flags)
    if "AMOUNT_DELTA" in detail.columns:
        return float(detail.loc[mask, "AMOUNT_DELTA"].fillna(0).abs().sum())
    if "TOTAL_BILLING_AMOUNT" in detail.columns and "VENDOR_AMOUNT" in detail.columns:
        sub = detail.loc[mask]
        return float((sub["VENDOR_AMOUNT"].fillna(0) - sub["TOTAL_BILLING_AMOUNT"].fillna(0)).abs().sum())
    return 0.0


def flag_accounts(detail: pd.DataFrame, flags: list[str]) -> int:
    """Distinct affected account count (SF_ID) for the given exception/outcome flags."""
    if detail.empty or "EXCEPTION_TYPE" not in detail.columns:
        return 0
    mask = detail["EXCEPTION_TYPE"].astype(str).isin(flags)
    if "SF_ID" in detail.columns:
        return int(detail.loc[mask, "SF_ID"].nunique())
    return int(mask.sum())


def flag_seats(detail: pd.DataFrame, flags: list[str]) -> float:
    """Sum of ABS_QTY_DELTA for rows matching the given exception/outcome flags."""
    if detail.empty or "EXCEPTION_TYPE" not in detail.columns:
        return 0.0
    mask = detail["EXCEPTION_TYPE"].astype(str).isin(flags)
    return float(detail.loc[mask, "ABS_QTY_DELTA"].fillna(0).sum())


def check_status(
    detail: pd.DataFrame,
    check_key: str,
    vendor_seats: float | None = None,
    billing_seats: float | None = None,
    partner_row_coverage: float | None = None,
) -> tuple[str, str]:
    """Account-centric RYG status for each reconciliation check.

    Leadership cares about *affected accounts* and *dollar exposure* — not raw
    row counts. Every description uses account-level language so the matrix
    reads as a summary of who is affected and how much is at risk.
    """
    denom = float(vendor_seats) if vendor_seats else 1.0

    if check_key == "account":
        # Count distinct accounts with no valid partner mapping
        unmatched = flag_accounts(detail, ["Unmapped Partner"])
        if unmatched == 0:
            return "g", "All accounts mapped"
        if unmatched < 10:
            return "y", f"{unmatched} unmatched accounts"
        return "r", f"{unmatched} unmatched accounts"

    if check_key == "seats":
        # Canonical flags where vendor usage exceeds or vendor is unbilled vs CW.
        under_flags = [
            "Vendor Billing, No CW Billing",
            "API Usage, Insufficient CW Billing",
            "Vendor Billing, Insufficient CW Billing",
        ]
        accts = flag_accounts(detail, under_flags)
        billed = float(billing_seats or 0.0)
        parity_pct = ((billed - denom) / denom) if denom else 0.0
        if accts == 0:
            return "g", f"No seat gap accounts (net {parity_pct * 100:+.1f}% seats vs vendor)"
        if accts < 10 or parity_pct > -0.03:
            return "y", f"{accts} accts with seat gaps; net {parity_pct * 100:+.1f}% seats vs vendor"
        return "r", f"{accts} accts with seat gaps; net {parity_pct * 100:+.1f}% seats vs vendor"
    if check_key == "sku":
        mapping_flags = [BUCKET_UNMAPPED]
        accts = flag_accounts(detail, mapping_flags)
        seats = flag_seats(detail, mapping_flags)
        pct = seats / denom if denom else 0.0
        if accts == 0:
            return "g", "No SKU catalog gaps"
        if pct < 0.03:
            return "y", f"{accts} accounts with SKU gaps"
        return "r", f"{accts} accounts with SKU gaps"

    if check_key == "price":
        # Duplicate billing + stale CW subscriptions + contract discount signal.
        duplicate_raw = detail.get("DUPLICATE_BILLING", detail.get("DUPLICATE_BILLING_FLAG"))
        if duplicate_raw is None:
            dup_accts = 0
        else:
            duplicate_mask = duplicate_raw.astype(str).str.upper().isin({"Y", "TRUE", "1"})
            account_col = "SF_ID" if "SF_ID" in detail.columns else "PARTNER_DISPLAY_NAME"
            dup_accts = int(detail.loc[duplicate_mask, account_col].nunique())
        over_accts = flag_accounts(detail, [
            "CW Billing, No Vendor Billing",
        ])
        below_accts = contract_below_cost_accounts(detail, material_only=True)
        below_dollars = contract_below_cost_dollars(detail, material_only=True)

        parts: list[str] = []
        if below_accts:
            parts.append(
                f"{below_accts} discounted accts ({fmt_short_money(abs(below_dollars))})"
            )
        if dup_accts:
            parts.append(f"{dup_accts} duplicate accts")
        if over_accts:
            parts.append(f"{over_accts} overbilled accts")

        if not parts:
            return "g", "No discounts, duplicates, or overbilling"

        # Severity: any account below cost is a review flag; a few large ones
        # (or many below-cost accounts) escalates to red.
        severity = "y"
        if below_accts >= 10 or abs(below_dollars) >= 5000 or dup_accts > 5 or over_accts > 10:
            severity = "r"
        return severity, "; ".join(parts)

    return "g", ""


def vendor_health_status(
    clear_rate: float | None,
    seat_parity: float | None,
    margin_pct: float | None,
    margin_amount: float | None,
) -> tuple[str, str]:
    """Overall vendor health from the three business-facing controls.

    Rules:
    - Healthy: margin > 0%, seat parity within 100% +/-10%, clear rate >= 90%.
    - Review: margin > 0% and either seat parity is within +/-20% or clear rate is 70-90%.
    - Unhealthy: margin < 0%, seat parity outside +/-30%, or clear rate < 70%.
    """
    cr = float(clear_rate) if clear_rate is not None and not pd.isna(clear_rate) else None
    sp = float(seat_parity) if seat_parity is not None and not pd.isna(seat_parity) else None
    mp = float(margin_pct) if margin_pct is not None and not pd.isna(margin_pct) else None
    ma = float(margin_amount) if margin_amount is not None and not pd.isna(margin_amount) else None

    reasons: list[str] = []
    if ma is not None and ma < 0:
        reasons.append("negative margin dollars")
    if mp is not None and mp < 0:
        reasons.append("negative margin percent")
    if sp is None:
        reasons.append("seat parity unavailable")
    elif abs(sp - 1.0) > 0.30:
        reasons.append("seat parity outside +/-30%")
    if cr is None:
        reasons.append("clear rate unavailable")
    elif cr < 0.70:
        reasons.append("clear rate below 70%")

    if reasons:
        return "r", "; ".join(reasons)

    margin_positive = bool((mp is not None and mp > 0) and (ma is not None and ma > 0))
    seat_gap = abs((sp or 0.0) - 1.0)
    if margin_positive and seat_gap <= 0.10 and cr is not None and cr >= 0.90:
        return "g", "margin positive, seat parity within +/-10%, clear rate >= 90%"
    if margin_positive and (seat_gap <= 0.20 or (cr is not None and 0.70 <= cr < 0.90)):
        return "y", "margin positive; clear rate or seat parity needs review"
    return "y", "outside healthy thresholds"


def clear_rate_value(matched_rows: float | int | None, total_rows: float | int | None) -> float | None:
    total = float(total_rows or 0)
    if total <= 0:
        return None
    return float(matched_rows or 0) / total


def seat_parity_value(billing_seats: float | int | None, vendor_seats: float | int | None) -> float | None:
    vendor = float(vendor_seats or 0)
    if vendor <= 0:
        return None
    return float(billing_seats or 0) / vendor


def clear_rate_pair(matched_rows: float | int | None, total_rows: float | int | None) -> str:
    rate = clear_rate_value(matched_rows, total_rows)
    if rate is None:
        primary = "-"
    else:
        primary = f"{rate * 100:.1f}%"
    secondary = f"{fmt_num(matched_rows or 0)} of {fmt_num(total_rows or 0)} rows"
    return f'<span class="metric-main">{primary}</span><span class="metric-sub">{secondary}</span>'


def seat_parity_pair(billing_seats: float | int | None, vendor_seats: float | int | None) -> str:
    parity = seat_parity_value(billing_seats, vendor_seats)
    delta = float(billing_seats or 0) - float(vendor_seats or 0)
    if parity is None:
        primary = "-"
    else:
        primary = f"{parity * 100:.1f}%"
    secondary = f"{delta:+,.0f} seats"
    return f'<span class="metric-main">{primary}</span><span class="metric-sub">{secondary}</span>'


def margin_pair(margin_pct: float | None, margin_amount: float | int | None) -> str:
    pct = "-" if margin_pct is None or pd.isna(margin_pct) else f"{float(margin_pct) * 100:.1f}%"
    secondary = fmt_short_money(margin_amount or 0)
    return f'<span class="metric-main">{pct}</span><span class="metric-sub">{secondary}</span>'


def vendor_check_matrix(
    detail: pd.DataFrame,
    vendor_seats: float | None = None,
    billing_seats: float | None = None,
    partner_row_coverage: float | None = None,
) -> dict[str, tuple[str, str]]:
    return {
        key: check_status(detail, key, vendor_seats, billing_seats, partner_row_coverage)
        for key, _ in CHECKS
    }


def worst_status(matrix: dict[str, tuple[str, str]]) -> str:
    return max((v[0] for v in matrix.values()), key=lambda s: RANK[s])


def exception_rollup(detail: pd.DataFrame) -> pd.DataFrame:
    """Canonical exception rollup used by cards + Billing Exception Summary.

    This keeps the queue cards and table aligned to identical category math.
    """
    if detail.empty or "OUTCOME_FLAG" not in detail.columns:
        return pd.DataFrame(
            columns=[
                "Exception Type",
                "Affected Accounts",
                "Seat Variance",
                "EST_DOLLAR_IMPACT",
                "Action Needed",
            ]
        )

    # Every row already has a unique EXCEPTION_TYPE bucket assigned by
    # _classify_bucket_series (fast path: read straight from the SQL column
    # if the unified mart already assigned it — see 2026-08-13 offload).
    d = detail.copy()
    d["Exception Type"] = _classify_bucket_series(d)
    d = d[d["Exception Type"] != BUCKET_CLEAR].copy()
    if d.empty:
        return pd.DataFrame(
            columns=[
                "Exception Type",
                "Affected Accounts",
                "Seat Variance",
                "EST_DOLLAR_IMPACT",
                "Action Needed",
            ]
        )

    d["_acct"] = d.get("SF_ID", pd.Series(index=d.index)).astype(str)
    d.loc[d["_acct"].isin(["None", "nan", "NaT", ""]), "_acct"] = pd.NA
    amt_delta = pd.to_numeric(d.get("AMOUNT_DELTA", 0.0), errors="coerce").fillna(0).abs()
    vendor_amt = pd.to_numeric(d.get("VENDOR_AMOUNT", 0.0), errors="coerce").fillna(0).abs()
    if "EST_DOLLAR_IMPACT" in d.columns:
        est_impact = pd.to_numeric(d["EST_DOLLAR_IMPACT"], errors="coerce").fillna(0)
    else:
        est_impact = pd.Series(0.0, index=d.index)
    # For unified mapping-gap rows, AMOUNT_DELTA can
    # be zero even when vendor-side cost exists. Use the max of available signals.
    d["EST_DOLLAR_IMPACT"] = pd.concat([est_impact, amt_delta, vendor_amt], axis=1).max(axis=1)

    roll = (
        d.groupby("Exception Type", dropna=False)
        .agg(
            **{
                "Affected Accounts": ("_acct", lambda s: int(pd.Series(s).dropna().nunique())),
                "Seat Variance": ("ABS_QTY_DELTA", lambda s: float(pd.Series(s).fillna(0).sum())),
                "EST_DOLLAR_IMPACT": ("EST_DOLLAR_IMPACT", "sum"),
            }
        )
        .reset_index()
    )

    # Hide empty categories.
    roll = roll[
        (roll["Affected Accounts"] > 0)
        | (roll["Seat Variance"] > 0)
        | (roll["EST_DOLLAR_IMPACT"] > 0)
    ].copy()

    roll["Action Needed"] = roll["Exception Type"].map(
        lambda x: FLAG_DISPLAY_ACTION.get(str(x), "Review required")
    )

    return roll.sort_values("EST_DOLLAR_IMPACT", ascending=False)


# ---------------------------------------------------------------------------
# Vendor slice - all metrics for a single vendor for a single period
# ---------------------------------------------------------------------------

class VendorSlice:
    """Cached rollup of a single vendor's data filtered to a period."""

    def __init__(
        self,
        vendor: dict[str, Any],
        summary_all: pd.DataFrame,
        detail_all: pd.DataFrame,
        coverage_all: pd.DataFrame,
        selected_month: pd.Timestamp | None,
    ):
        self.vendor = vendor
        self.name: str = vendor["name"]
        self.category: str = vendor["category"]

        # Belt-and-suspenders scope: vendor-month usage presence is the only
        # month inclusion rule.
        if not summary_all.empty and "BILLING_MONTH" in summary_all.columns:
            _vs_mask = None
            if "DATA_LOAD_STATUS" in summary_all.columns:
                _vs_mask = summary_all["DATA_LOAD_STATUS"].astype(str).str.upper().eq("LOADED")
            elif "USAGE_ROW_COUNT" in summary_all.columns:
                _vs_mask = pd.to_numeric(summary_all["USAGE_ROW_COUNT"], errors="coerce").fillna(0) > 0
            if _vs_mask is not None:
                vendor_loaded_months = set(pd.to_datetime(summary_all.loc[_vs_mask, "BILLING_MONTH"]).unique())
                summary_all = summary_all[pd.to_datetime(summary_all["BILLING_MONTH"]).isin(vendor_loaded_months)].reset_index(drop=True)
                if not detail_all.empty and "BILLING_MONTH" in detail_all.columns:
                    detail_all = detail_all[pd.to_datetime(detail_all["BILLING_MONTH"]).isin(vendor_loaded_months)].reset_index(drop=True)
                if not coverage_all.empty and "BILLING_MONTH" in coverage_all.columns:
                    coverage_all = coverage_all[pd.to_datetime(coverage_all["BILLING_MONTH"]).isin(vendor_loaded_months)].reset_index(drop=True)

        self.summary_all = summary_all
        self.detail_all = detail_all
        self.coverage_all = coverage_all
        self.selected_month = selected_month

        # Avoid the .copy() on the all-months hot path — nothing downstream
        # mutates these frames in place, and the copy is O(rows × cols)
        # which dominates VendorSlice construction time for the big detail
        # frame. The single-month branch keeps the previous behavior so
        # any future caller that mutates gets a private slice.
        if selected_month is None:
            self.summary = summary_all
            self.detail = detail_all
            self.coverage = coverage_all
        else:
            self.summary = summary_all[summary_all["BILLING_MONTH"] == selected_month]
            self.detail = detail_all[detail_all["BILLING_MONTH"] == selected_month]
            self.coverage = coverage_all[coverage_all["BILLING_MONTH"] == selected_month]

        def _sum_col(df: pd.DataFrame, col: str) -> float:
            """Sum a column if it exists; return 0.0 otherwise."""
            if df.empty or col not in df.columns:
                return 0.0
            return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())

        s = self.summary
        self.vendor_seats = _sum_col(s, "TOTAL_VENDOR_SEATS")
        self.billing_seats = _sum_col(s, "TOTAL_BILLING_SEATS")
        self.vendor_amount = _sum_col(s, "TOTAL_VENDOR_AMOUNT")
        self.billing_amount = _sum_col(s, "TOTAL_BILLING_AMOUNT")
        self.gross_margin = self.billing_amount - self.vendor_amount
        self.gross_margin_pct = (
            self.gross_margin / self.billing_amount if self.billing_amount else 0.0
        )
        self.total_rows = int(_sum_col(s, "TOTAL_ROWS"))
        self.matched_rows = int(_sum_col(s, "PERFECT_MATCH_ROWS"))
        # Fall back to computing from detail when summary columns are missing or zero
        if self.total_rows == 0 and not self.detail.empty:
            self.total_rows = len(self.detail)
        if self.matched_rows == 0 and not self.detail.empty:
            # Count Clear rows from canonical EXCEPTION_TYPE only.
            if "EXCEPTION_TYPE" in self.detail.columns:
                self.matched_rows = int((self.detail["EXCEPTION_TYPE"] == "Clear").sum())
        self.clear_rate = clear_rate_value(self.matched_rows, self.total_rows)
        self.seat_parity = seat_parity_value(self.billing_seats, self.vendor_seats)
        self.seat_delta = self.billing_seats - self.vendor_seats
        self.health_status, self.health_reason = vendor_health_status(
            self.clear_rate,
            self.seat_parity,
            self.gross_margin_pct,
            self.gross_margin,
        )

        c = self.coverage
        if not c.empty and "RAW_ROWS_AFTER_SCOPE" in c.columns and "MAPPED_ROWS" in c.columns:
            raw_rows = float(pd.to_numeric(c["RAW_ROWS_AFTER_SCOPE"], errors="coerce").fillna(0).sum())
            mapped_rows = float(pd.to_numeric(c["MAPPED_ROWS"], errors="coerce").fillna(0).sum())
            self.partner_row_coverage = mapped_rows / raw_rows if raw_rows else None
        else:
            self.partner_row_coverage = None

        # RYG matrix uses vendor-seat denominator + partner coverage so we
        # match the pipeline's own investigation-floor semantics.
        self.matrix = vendor_check_matrix(
            self.detail,
            vendor_seats=self.vendor_seats,
            billing_seats=self.billing_seats,
            partner_row_coverage=self.partner_row_coverage,
        )
        self.worst = worst_status(self.matrix)

    # ------------------------------------------------------------------
    # Hot columns/rollups. Cached per-instance so downstream renderers can
    # reuse them without repeating the same groupby/mask/map on every rerun.
    # This is the single biggest lever on filter-change latency; without it
    # every render recomputes EXCEPTION_TYPE/ACTION_NEEDED per row and
    # re-runs exception_rollup 2-3 times per script run.
    # ------------------------------------------------------------------
    @cached_property
    def detail_with_categories(self) -> pd.DataFrame:
        d = self.detail
        if d.empty:
            return d.assign(EXCEPTION_TYPE="", ACTION_NEEDED="")
        # Fast path: EXCEPTION_TYPE and ACTION_NEEDED are precomputed by
        # THIRD_PARTY_RECON_OUTPUT_PROD (v23+). Return the frame as-is so we
        # skip the per-row Python classification + copy that used to happen
        # on every filter change. The `.copy` fallback below only runs on
        # historical data built before the pipeline v23 refresh.
        has_type = (
            "EXCEPTION_TYPE" in d.columns and d["EXCEPTION_TYPE"].ne("").any()
        )
        has_action = (
            "ACTION_NEEDED" in d.columns and d["ACTION_NEEDED"].ne("").any()
        )
        if has_type and has_action:
            _classify_bucket_series(d)
            return d
        _cat = _classify_bucket_series(d).astype(str)
        _act = _cat.map(FLAG_DISPLAY_ACTION).fillna("Review required")
        return d.assign(EXCEPTION_TYPE=_cat.values, ACTION_NEEDED=_act.values)

    @cached_property
    def exception_rollup(self) -> pd.DataFrame:
        """Canonical exception rollup used by tab 1 queue math, the interactive
        exception table, and the exec summary."""
        return exception_rollup(self.detail)

    @cached_property
    def finance_leakage_qty(self) -> float:
        """Seats under-billed in the Finance queue (unified flags)."""
        return sum(outcome_qty(self.detail, f) for f in LEAKAGE_FLAGS)

    @cached_property
    def finance_leakage_dollars(self) -> float:
        """Dollar exposure for Finance-queue leakage rows."""
        return flag_dollars(self.detail, LEAKAGE_FLAGS)

    @cached_property
    def billing_ops_qty(self) -> float:
        return outcome_qty(self.detail, BUCKET_CW_NO_VENDOR_BILLING)

    @cached_property
    def sku_mismatch_dollars(self) -> float:
        """Dollar exposure for unified partner/SKU mapping failures."""
        return flag_dollars(self.detail, [BUCKET_UNMAPPED])

    @cached_property
    def timing_qty(self) -> float:
        return outcome_qty(self.detail, BUCKET_MARKETPLACE_TIMING)

    @cached_property
    def leakage_dollars(self) -> float:
        """Total dollar leakage across ALL leakage flags (Finance + SKU)."""
        return flag_dollars(self.detail, LEAKAGE_FLAGS)

    @cached_property
    def leakage_seats_count(self) -> float:
        """Total seats at risk across all leakage flags."""
        return flag_seats(self.detail, LEAKAGE_FLAGS)

    @cached_property
    def leakage_accounts_count(self) -> int:
        """Distinct accounts affected by any leakage flag."""
        return flag_accounts(self.detail, LEAKAGE_FLAGS)

    @cached_property
    def revenue_leakage_dollars(self) -> float:
        """Canonical Revenue Leakage $ (Finance Queue) — the single definition
        used app-wide. Same cohort as the Action Queue tile."""
        return _revenue_leakage_dollars(self.detail)

    @cached_property
    def revenue_leakage_accounts(self) -> int:
        """Distinct accounts inside the canonical Revenue Leakage cohort."""
        return _revenue_leakage_accounts(self.detail)

    @cached_property
    def rate_below_cost_dollars(self) -> float:
        """Material below-cost contract-rate dollar exposure (all rows, absolute)."""
        return abs(contract_below_cost_dollars(self.detail, material_only=True))

    @cached_property
    def rate_below_cost_accounts(self) -> int:
        """Distinct accounts with material below-cost contract rates."""
        return contract_below_cost_accounts(self.detail, material_only=True)

    @cached_property
    def rate_above_cost_dollars(self) -> float:
        """Contract-rate above-cost dollar surplus."""
        return contract_above_cost_dollars(self.detail)

    @cached_property
    def rate_below_cost_clean_dollars(self) -> float:
        """Material below-cost on CLEAR-outcome rows only (safe to add to leakage)."""
        return abs(
            contract_below_cost_dollars_clean_only(self.detail, material_only=True)
        )

    @cached_property
    def timing_only_dollars(self) -> float:
        """Timing-only queue dollars."""
        return flag_dollars(self.detail, TIMING_ONLY_FLAGS)


# ---------------------------------------------------------------------------
# Data load - combined vendor marts
# ---------------------------------------------------------------------------

# Force-clear all @st.cache_data when the schema version advances.
_CACHE_SCHEMA_KEY = "cache_schema_version_cleared"
if st.session_state.get(_CACHE_SCHEMA_KEY) != SLICE_SCHEMA_VERSION:
    st.cache_data.clear()
    st.session_state[_CACHE_SCHEMA_KEY] = SLICE_SCHEMA_VERSION

# Manual force-refresh button — must appear before the data load so clearing
# the cache takes effect on the same rerun that triggered the button click.
_refresh_col1, _refresh_col2 = st.columns([6, 1])
with _refresh_col2:
    if st.button("\u21bb Refresh", help="Clear all cached data and re-query Snowflake"):
        st.cache_data.clear()
        st.session_state.pop(_CACHE_SCHEMA_KEY, None)
        st.rerun()

freshness = fetch_freshness_key()

with _refresh_col1:
    st.caption(
        f"Data auto-refreshes when the reconciliation pipeline is rebuilt "
        f"(cache TTL {DATA_TTL_SECONDS}s)."
    )

# ---------------------------------------------------------------------------
# Data-freshness diagnostic panel  (schema {SLICE_SCHEMA_VERSION})
# Shows the user EXACTLY what Snowflake is returning right now, so they can
# verify the pipeline was rebuilt with the canonical taxonomy. If the table
# LAST_ALTERED is old or EXCEPTION_TYPE values don't match the 12-bucket
# taxonomy, the pipeline needs to be re-run — no app-side change can help.
#
# Lazy-loaded: the queries only fire when the user opts in via the toggle.
# Previously the two queries ran on every rerun (they were cached, but the
# freshness key change still forced a re-query on every pipeline rebuild
# and every TTL expiration). Since the audit is used maybe once per session,
# gating on an opt-in checkbox eliminates two Snowflake round-trips per
# tab/filter change for the 99% path.
# ---------------------------------------------------------------------------
_audit_toggle_key = "data_source_audit_enabled"
with st.expander("\U0001f50d Data source audit", expanded=False):
    _audit_enabled = st.checkbox(
        "Load live audit metrics",
        value=st.session_state.get(_audit_toggle_key, False),
        key=_audit_toggle_key,
        help=(
            "Queries Snowflake for THIRD_PARTY_RECON_OUTPUT_PROD's LAST_ALTERED "
            "timestamp and live EXCEPTION_TYPE distribution. Off by default so "
            "every filter change stays local."
        ),
    )
    if _audit_enabled:
        try:
            _audit_meta = run_query(
                f"""
                SELECT
                    TO_CHAR(MAX(LAST_ALTERED), 'YYYY-MM-DD HH24:MI:SS TZH:TZM') AS LAST_ALTERED,
                    MAX(ROW_COUNT)                                              AS ROW_COUNT,
                    MAX(BYTES)                                                  AS BYTES
                FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = 'DBT_NFOLD_TRANSFORMATION'
                  AND TABLE_NAME   = 'THIRD_PARTY_RECON_OUTPUT_PROD'
                """,
                freshness,
            )
            _audit_dist = run_query(
                f"""
                SELECT
                    COALESCE(EXCEPTION_TYPE, '(null)') AS "Exception Type",
                    COUNT(*)                           AS "Rows"
                FROM {SCHEMA}.THIRD_PARTY_RECON_OUTPUT_PROD
                GROUP BY 1
                ORDER BY 2 DESC
                """,
                freshness,
            )
            c1, c2, c3 = st.columns(3)
            if not _audit_meta.empty:
                c1.metric("Table LAST_ALTERED", str(_audit_meta.iloc[0].get("LAST_ALTERED", "?")))
                c2.metric("ROW_COUNT (metadata)", f"{int(_audit_meta.iloc[0].get('ROW_COUNT', 0) or 0):,}")
            c3.metric("Distinct EXCEPTION_TYPE", f"{len(_audit_dist):,}")
            st.markdown("**Live EXCEPTION_TYPE distribution in `THIRD_PARTY_RECON_OUTPUT_PROD`**")
            st.dataframe(_audit_dist, use_container_width=True, hide_index=True)
            st.caption(
                "If the values above are NOT from the canonical seven-outcome taxonomy "
                "(Marketplace Billing Delay, Clear, Unmapped Partner, "
                "API Usage Insufficient CW Billing, Vendor Billing No CW Billing, "
                "CW Billing No Vendor Billing, Vendor Billing Insufficient CW Billing) \u2014 "
                "the pipeline (`build_third_party_recon_output_prod.py`) needs to be re-run."
            )
        except Exception as _audit_exc:
            st.warning(f"Audit query failed: {_audit_exc}")
    else:
        st.caption(
            "Live audit is off. Toggle above to run the two audit queries (kept off "
            "by default so tab / filter changes stay off the network)."
        )

active_vendors: list[dict[str, Any]] = []
for v in VENDORS:
    loader: Callable[[str], dict[str, pd.DataFrame]] | None = v.get("loader")
    if loader is None:
        continue
    try:
        data = loader(freshness)
    except Exception as exc:  # surface but do not crash the whole app
        st.error(f"Failed to load {v['name']} data: {exc}")
        continue
    v = dict(v)
    v["data"] = data
    active_vendors.append(v)


def _loaded_months_for(summary: pd.DataFrame) -> set[pd.Timestamp]:
    """Return the set of BILLING_MONTH values in the summary frame.

    The ghost-month filter now runs inside the cached `_load_combined_vendor`,
    so by the time we get here every month present in the summary is a
    real vendor-loaded month. This helper just exposes that set for the
    portfolio-wide month union below.
    """
    if summary.empty or "BILLING_MONTH" not in summary.columns:
        return set()
    return {pd.Timestamp(month) for month in pd.to_datetime(summary["BILLING_MONTH"]).dropna().unique()}


# The ghost-month trim is applied inside `_load_combined_vendor_impl` (see
# above) so no additional per-vendor filtering is needed here.

if not active_vendors:
    st.warning("No vendor recon output available. Run the pipeline and refresh.")
    st.stop()

# If every vendor loaded but has no summary/detail yet, continue — the app
# will render "no data" states in each tab rather than stopping.
first_vendor = active_vendors[0]

# Portfolio-level month list = union of every vendor's loaded months.
# Each vendor's frames are already trimmed to its own loaded months inside
# _load_combined_vendor_impl (ghost-month filter + DATA_LOAD_STATUS guard),
# so the union here is safe: a vendor that has no August data simply
# contributes nothing to August aggregates, while a vendor that does have
# August data shows up normally. No portfolio-wide min-cap is applied.
_month_union: set = set()
_per_vendor_max_month: list = []
for v in active_vendors:
    _s = v["data"]["summary"]
    if _s is not None and not _s.empty and "BILLING_MONTH" in _s.columns:
        _v_months = pd.to_datetime(_s["BILLING_MONTH"]).dropna()
        if not _v_months.empty:
            _month_union |= set(_v_months.unique())
            _per_vendor_max_month.append(_v_months.max())
months_available = sorted(_month_union)
# NOTE: no portfolio-min cap here. Each vendor's frames are already trimmed to
# its own loaded months inside _load_combined_vendor_impl (ghost-month filter +
# DATA_LOAD_STATUS guard). Imposing a min() across vendors would suppress
# legitimate data — e.g. if Proofpoint only has through July but KeepIT has
# through August, the month picker should still offer August and each vendor's
# August data (or absence of it) reflects its real load state.
# Cap at the current calendar month — future-dated contract/royalty rows in
# Zuora or Bitdefender quarterly billings can produce months that haven't
# happened yet, which bleeds month-number highlights into the pill row.
_current_month_cap = pd.Timestamp.today().normalize().to_period("M").to_timestamp()
months_available = [m for m in months_available if pd.to_datetime(m) <= _current_month_cap]
# Filter each vendor's frames down to the capped month window so downstream
# aggregations (clear rate, leakage totals) don't include months hidden
# from the picker.
if months_available:
    _capped_set = set(pd.to_datetime(m) for m in months_available)
    for v in active_vendors:
        _d = v["data"]
        for _key in ("summary", "detail", "coverage"):
            _df = _d.get(_key)
            if _df is not None and not _df.empty and "BILLING_MONTH" in _df.columns:
                _d[_key] = _df[pd.to_datetime(_df["BILLING_MONTH"]).isin(_capped_set)].reset_index(drop=True)
if not months_available:
    st.info(
        "No billing months found yet. The pipeline tables exist but contain no data — "
        "run the reconciliation pipeline to populate them, then click Refresh."
    )
    st.stop()
first_month = pd.to_datetime(months_available[0])
last_month = pd.to_datetime(months_available[-1])
range_label = f"{first_month:%B}\u2013{last_month:%B} {last_month:%Y}"


# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------

st.markdown(
    '<div class="hero" style="padding:1.1rem 1.4rem 1rem;">'
    '<h1 style="font-size:1.75rem;letter-spacing:-0.02em;font-weight:700;">'
    'Third Party Vendor Reconciliation</h1>'
    '</div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Filters — vendor / year / month
# ---------------------------------------------------------------------------

c_vendor, c_year, c_month = st.columns([1.5, 1.1, 1.5])
vendor_lookup = {v["name"]: v for v in active_vendors}
_all_vendor_names = sorted(vendor_lookup.keys())

# Surface the registered vendor set so an analyst can immediately tell
# whether a new vendor has actually been wired into the app (versus
# silently missing from the multiselect because the loader failed).
st.caption(
    f"Registered vendors ({len(_all_vendor_names)}): "
    + ", ".join(_all_vendor_names)
)

with c_vendor:
    vendor_choices = st.multiselect(
        "Vendor",
        _all_vendor_names,
        default=_all_vendor_names,
        help=(
            "Multi-select. Defaults to every registered vendor so the "
            "portfolio KPIs reflect all data. Deep Dive uses the first "
            "selected vendor; other tabs aggregate across all selected."
        ),
    )
    if not vendor_choices:
        vendor_choices = list(_all_vendor_names)
    selected_vendor_confs = [vendor_lookup[v] for v in vendor_choices]
    selected_vendor_conf = selected_vendor_confs[0]

with c_year:
    years = sorted({pd.to_datetime(m).year for m in months_available})
    year_options = [str(y) for y in years]
    selected_years_str = st.multiselect(
        "Year",
        year_options,
        default=year_options,
        help="Multi-select. Empty selection = all years.",
    )
    if not selected_years_str:
        selected_years_str = year_options
    year_set = {int(y) for y in selected_years_str}

scoped_months = [m for m in months_available if pd.to_datetime(m).year in year_set]
_scoped_present_month_nums = sorted({pd.to_datetime(m).month for m in scoped_months})

with c_month:
    month_name_options = [calendar.month_name[m] for m in _scoped_present_month_nums]
    selected_month_names = st.multiselect(
        "Billing Month",
        month_name_options,
        default=[],
        help="Multi-select. Empty selection = all months in the selected year(s).",
    )
    _all_month_pills = '<div class="month-presence">' + "".join(
        (
            f'<span class="month-pill on">{calendar.month_abbr[m]}</span>'
            if m in _scoped_present_month_nums
            else f'<span class="month-pill off">{calendar.month_abbr[m]}</span>'
        )
        for m in range(1, 13)
    ) + "</div>"
    st.markdown(_all_month_pills, unsafe_allow_html=True)

if selected_month_names:
    _sel_month_nums = {list(calendar.month_name).index(nm) for nm in selected_month_names}
    selected_month_ts_list: list[pd.Timestamp] = [
        pd.to_datetime(m) for m in scoped_months
        if pd.to_datetime(m).month in _sel_month_nums
    ]
else:
    selected_month_ts_list = [pd.to_datetime(m) for m in scoped_months]

# Only retain vendors with usage-backed summary data in the selected month
# set. For a single month this means Bitdefender can appear by itself when it
# is the only vendor with vendor usage loaded that far out.
_selected_month_set = set(selected_month_ts_list)
selected_vendor_confs = [
    v for v in selected_vendor_confs
    if not _selected_month_set
    or bool(_loaded_months_for(v["data"]["summary"]) & _selected_month_set)
]
if not selected_vendor_confs:
    st.info("No selected vendors have vendor usage loaded for this month selection.")
    st.stop()
selected_vendor_conf = selected_vendor_confs[0]

# Backward-compat scalar: single selected month, else None. Used by callers
# that pre-date multi-select and expect one or none.
selected_month: pd.Timestamp | None = (
    selected_month_ts_list[0] if len(selected_month_ts_list) == 1 else None
)

# Period label reflects the current multi-selection
if not selected_month_ts_list:
    period_label = "No data in selection"
elif len(selected_month_ts_list) == len(months_available):
    period_label = range_label
elif len(selected_month_ts_list) == 1:
    period_label = full_month(selected_month_ts_list[0])
else:
    _yr_txt = ", ".join(sorted(str(y) for y in year_set))
    if selected_month_names and len(selected_month_names) <= 3:
        period_label = f"{', '.join(selected_month_names)} ({_yr_txt})"
    else:
        period_label = f"{len(selected_month_ts_list)} months ({_yr_txt})"

# Annualization basis for profitability insight cards.
annualization_months = max(len(selected_month_ts_list), 1)

# Increment when VendorSlice shape changes to invalidate stale session caches.
# Defined in Config section at top of file.
# Include the vendor roster in the cache key so adding/removing vendors
# always triggers a full rebuild (fixes KeyError on first load after
# adding Auvik/Bitdefender/ESET/Exium to the registry).
_vendor_fingerprint = "|".join(sorted(v["key"] for v in active_vendors))
_all_slices_key = f"all_slices|{SLICE_SCHEMA_VERSION}|{freshness}|{_vendor_fingerprint}"
if _all_slices_key not in st.session_state:
    # Build only the all-months (selected_month=None) slice per vendor eagerly.
    # Per-month slices are built lazily in build_filtered_slice() on first access.
    # This cuts startup work from O(vendors × months) to O(vendors).
    _built_all: dict[str, dict] = {}
    for v in active_vendors:
        _built_all[v["key"]] = {
            None: VendorSlice(
                v,
                v["data"]["summary"],
                v["data"]["detail"],
                v["data"]["coverage"],
                None,
            )
        }
    # Evict stale entries from prior pipeline runs or vendor roster changes.
    for _stale in [k for k in st.session_state if k.startswith("all_slices|") or k.startswith("slice|")]:
        del st.session_state[_stale]
    st.session_state[_all_slices_key] = _built_all

_all_slices: dict[str, dict] = st.session_state[_all_slices_key]


def build_filtered_slice(
    vendor_conf: dict[str, Any], months_list: list[pd.Timestamp]
) -> VendorSlice:
    """Return a VendorSlice for the given month subset.

    O(1) for single-month / all-months (pre-computed cache).
    On-demand for arbitrary multi-month subsets, cached in session_state.
    Defensive fallback: if the vendor key is missing from _all_slices
    (e.g. stale session state), builds and inserts the slice on the fly.
    """
    key = vendor_conf["key"]

    # Defensive: vendor added after cache was built (should not happen with
    # the fingerprinted key, but guards against edge cases in cloud hosting).
    if key not in _all_slices:
        data = vendor_conf["data"]
        _all_slices[key] = {}
        for _m in [None] + list(months_available):
            _all_slices[key][_m] = VendorSlice(
                vendor_conf,
                data["summary"],
                data["detail"],
                data["coverage"],
                _m,
            )

    if not months_list or len(months_list) == len(months_available):
        return _all_slices[key][None]
    if len(months_list) == 1:
        m = months_list[0]
        if m not in _all_slices[key]:
            data = vendor_conf["data"]
            _all_slices[key][m] = VendorSlice(
                vendor_conf, data["summary"], data["detail"], data["coverage"], m
            )
        return _all_slices[key][m]
    frozen = "|".join(sorted(pd.to_datetime(m).strftime("%Y-%m") for m in months_list))
    cache_key = f"slice|{SLICE_SCHEMA_VERSION}|{key}|{freshness}|{frozen}"
    cached = st.session_state.get(cache_key)
    if cached is not None:
        return cached
    data = vendor_conf["data"]
    ts_list = [pd.to_datetime(m) for m in months_list]
    summary = data["summary"][data["summary"]["BILLING_MONTH"].isin(ts_list)]
    detail = data["detail"][data["detail"]["BILLING_MONTH"].isin(ts_list)]
    coverage = data["coverage"][data["coverage"]["BILLING_MONTH"].isin(ts_list)]
    slice_ = VendorSlice(vendor_conf, summary, detail, coverage, selected_month=None)
    st.session_state[cache_key] = slice_
    return slice_


# Slices scoped to SELECTED vendors + SELECTED months.
def _months_key(months_list: list) -> str:
    """Canonical cache key for the current month subset. Empty string = all months."""
    if not months_list or len(months_list) == len(months_available):
        return ""
    return "|".join(sorted(pd.to_datetime(m).strftime("%Y-%m") for m in months_list))


slices: dict[str, VendorSlice] = {
    v["key"]: build_filtered_slice(v, selected_month_ts_list)
    for v in selected_vendor_confs
}
active_slice = slices[selected_vendor_conf["key"]]

llm_summary_df = selected_vendor_conf["data"]["llm_summary"]
latest_summary_text: str | None = None
latest_summary_provider: str = "-"
latest_summary_model: str = "-"
latest_summary_run_ts: Any = None
if not llm_summary_df.empty:
    latest = llm_summary_df.iloc[0]
    latest_summary_text = str(latest.get("SUMMARY_TEXT") or "") or None
    latest_summary_provider = str(latest.get("PROVIDER") or "-")
    latest_summary_model = str(latest.get("MODEL") or "-")
    latest_summary_run_ts = latest.get("RUN_TS")

# ---------------------------------------------------------------------------
# Summary metrics strip
# ---------------------------------------------------------------------------

total_vendors = len(active_vendors)
green_vendors = sum(1 for s in slices.values() if s.worst == "g")
yellow_vendors = sum(1 for s in slices.values() if s.worst == "y")
red_vendors = sum(1 for s in slices.values() if s.worst == "r")
open_vendor_count = yellow_vendors + red_vendors
rows_classified = sum(s.total_rows for s in slices.values())
auto_cleared = sum(s.matched_rows for s in slices.values())
auto_cleared_pct = (auto_cleared / rows_classified) if rows_classified else 0

# Leakage totals — consistent with leakage flags used in the cards below
portfolio_leakage = sum(s.revenue_leakage_dollars for s in slices.values())

st.markdown(
    '<div class="strip">'
    + strip_tile(fmt_num(rows_classified), "Recon Rows Classified",
        hint="Total account/product/month rows processed by the reconciliation pipeline in the selected period")
    + strip_tile(f"{auto_cleared_pct * 100:.0f}%", "Reconciliation Clear Rate",
        hint="% of rows where vendor-reported seats and CW billed seats match exactly \u2014 higher is better")
    + strip_tile(str(total_vendors), "Vendors Monitored",
        hint="Number of third-party vendors with active reconciliation pipelines loaded in this session")
    + strip_tile(f"{open_vendor_count} of {total_vendors}", "Vendors Needing Review",
        hint="Vendors with at least one amber or red check in the reconciliation matrix — these need finance or ops action")
    + "</div>",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_close, tab_team, tab_vendor, tab_profit, tab_ai = st.tabs(
    [
        "Monthly Reconciliation",
        "Recon Team Queue",
        "Vendor Deep Dive",
        "Profitability",
        "AI Analyst",
    ]
)


# ---------------------------------------------------------------------------
# Portfolio aggregates — combine selected slices for multi-vendor views
# ---------------------------------------------------------------------------

def _slices_signature(slices_map: dict) -> str:
    """Stable key of the current filtered slice set. Used to memoize portfolio
    aggregates so re-renders on unrelated widget changes reuse the same math.
    SLICE_SCHEMA_VERSION is baked in so any bucket-taxonomy change forces a
    fresh compute (otherwise the rollup keeps old labels while the drill
    detail carries new ones and nothing matches)."""
    return (
        "|".join(sorted(slices_map.keys()))
        + "::" + _months_key(selected_month_ts_list)
        + "::" + str(freshness)
        + "::" + SLICE_SCHEMA_VERSION
    )


@st.cache_data(ttl=DATA_TTL_SECONDS, show_spinner=False)
def _portfolio_exception_rollup_cached(sig: str, vendor_keys: tuple[str, ...], months_key: str) -> pd.DataFrame:
    """Portfolio exception rollup — ALWAYS reads from THIRD_PARTY_RECON_OUTPUT_PROD.

    Previously this tried a pre-aggregated table (THIRD_PARTY_RECON_VENDOR_MONTH_EXCEPTIONS)
    first, which returned STALE LABELS whenever that table existed but wasn't rebuilt.
    Now the app always computes fresh from OUTPUT_PROD — the single source of truth
    written by the current pipeline (build_third_party_recon_output_prod.py).
    """
    _ = sig
    empty_cols = ["Exception Type", "Affected Accounts", "Seat Variance",
                  "EST_DOLLAR_IMPACT", "Action Needed"]
    if not vendor_keys:
        return pd.DataFrame(columns=empty_cols)
    vendor_names: list[str] = []
    for k in vendor_keys:
        try:
            vendor_names.append(vendor_by_key(k)["name"])
        except Exception:
            continue
    if not vendor_names:
        return pd.DataFrame(columns=empty_cols)
    v_in = ", ".join(f"'{n}'" for n in vendor_names)
    if months_key:
        months_in = ", ".join(f"'{m}'" for m in months_key.split("|"))
        month_sql = f" AND TO_CHAR(BILLING_MONTH, 'YYYY-MM') IN ({months_in})"
    else:
        month_sql = ""

    try:
        raw = run_query(
            f"""
            SELECT
                EXCEPTION_TYPE                              AS "Exception Type",
                COUNT(DISTINCT SF_ID)                       AS "Affected Accounts",
                SUM(COALESCE(ABS_QTY_DELTA, 0))             AS "Seat Variance",
                SUM(GREATEST(
                    COALESCE(EST_DOLLAR_IMPACT, 0),
                    ABS(COALESCE(AMOUNT_DELTA, 0)),
                    ABS(COALESCE(VENDOR_AMOUNT, 0))
                )) AS "EST_DOLLAR_IMPACT"
            FROM {SCHEMA}.THIRD_PARTY_RECON_OUTPUT_PROD
            WHERE VENDOR IN ({v_in})
              AND EXCEPTION_TYPE != 'Clear'
              AND EXCEPTION_TYPE IS NOT NULL
              AND EXCEPTION_TYPE != ''{month_sql}
            GROUP BY EXCEPTION_TYPE
            HAVING SUM(COALESCE(ABS_QTY_DELTA, 0)) > 0
                OR SUM(GREATEST(
                    COALESCE(EST_DOLLAR_IMPACT, 0),
                    ABS(COALESCE(AMOUNT_DELTA, 0)),
                    ABS(COALESCE(VENDOR_AMOUNT, 0))
                )) > 0
                OR COUNT(DISTINCT SF_ID) > 0
            ORDER BY 4 DESC
            """,
            freshness,
        )
        if not raw.empty:
            raw["Affected Accounts"] = raw["Affected Accounts"].fillna(0).astype(int)
            raw["Seat Variance"] = raw["Seat Variance"].astype(float)
            raw["EST_DOLLAR_IMPACT"] = raw["EST_DOLLAR_IMPACT"].astype(float)
            raw["Action Needed"] = raw["Exception Type"].map(
                lambda x: FLAG_DISPLAY_ACTION.get(str(x), "Review required")
            )
            return raw
    except Exception:
        pass

    return pd.DataFrame(columns=empty_cols)


def portfolio_exception_rollup(slices_map: dict) -> pd.DataFrame:
    return _portfolio_exception_rollup_cached(
        _slices_signature(slices_map),
        tuple(sorted(slices_map.keys())),
        _months_key(selected_month_ts_list),
    )


def portfolio_detail_with_categories(slices_map: dict) -> pd.DataFrame:
    """Concat detail_with_categories across selected slices with a VENDOR column.

    Cached in session_state keyed on the current slice signature so tab
    changes and unrelated widget updates reuse the built frame instead of
    re-copying + re-concatenating N vendor detail frames every rerun.
    """
    sig = "portfolio_detail|" + _slices_signature(slices_map)
    cached = st.session_state.get(sig)
    if cached is not None:
        return cached
    parts: list[pd.DataFrame] = []
    for s in slices_map.values():
        d = s.detail_with_categories
        if d.empty:
            continue
        # Attach the vendor label without materializing a full .copy() —
        # `assign` returns a shallow-cloned frame that shares column data
        # with the source, which is orders of magnitude cheaper than the
        # previous `.copy()` on every rerun.
        parts.append(d.assign(_VENDOR=s.name))
    if not parts:
        result = pd.DataFrame()
    else:
        result = pd.concat(parts, ignore_index=True)
    # Evict any prior portfolio_detail cache entries — session_state is a
    # dict and there is no need to keep stale slice signatures around.
    for _k in [k for k in st.session_state if k.startswith("portfolio_detail|") and k != sig]:
        del st.session_state[_k]
    st.session_state[sig] = result
    return result


def portfolio_totals(slices_map: dict) -> dict:
    """Additive portfolio numbers (revenue, cost, GM, leakage). Cheap dictionary."""
    if not slices_map:
        return {
            "billing_amount": 0.0, "vendor_amount": 0.0,
            "gross_margin": 0.0, "gross_margin_pct": 0.0,
            "leakage_dollars": 0.0, "leakage_accounts": 0,
            "leakage_seats": 0.0,
        }
    br = sum(s.billing_amount for s in slices_map.values())
    vc = sum(s.vendor_amount for s in slices_map.values())
    lk = sum(s.revenue_leakage_dollars for s in slices_map.values())
    lk_accts = sum(int(s.revenue_leakage_accounts) for s in slices_map.values())
    lk_seats = sum(float(s.leakage_seats_count) for s in slices_map.values())
    return {
        "billing_amount": br, "vendor_amount": vc,
        "gross_margin": br - vc,
        "gross_margin_pct": ((br - vc) / br) if br else 0.0,
        "leakage_dollars": lk,
        "leakage_accounts": lk_accts,
        "leakage_seats": lk_seats,
    }


# ---- Tab 1: Monthly Vendor Reconciliation ---------------------------------

def render_status_matrix() -> None:
    header = (
        '<tr><th style="width:16%">Vendor</th>'
        '<th class="c" style="width:21%">Vendor Health</th>'
        '<th class="c" style="width:21%">Reconciliation Clear Rate</th>'
        '<th class="c" style="width:21%">Seat Parity</th>'
        '<th class="c" style="width:21%">Margin</th></tr>'
    )

    rows_html = []
    ordered = sorted(
        slices.values(),
        key=lambda s: (-RANK[s.health_status], -(s.billing_amount)),
    )
    for s in ordered:
        row = (
            f'<tr><td><b>{html.escape(s.name)}</b></td>'
            f'<td class="c">{chip_html(s.health_status, HEALTH_LABELS[s.health_status])}'
            f'<span class="cellcap">{html.escape(s.health_reason)}</span></td>'
            f'<td class="c"><span class="metricpair">{clear_rate_pair(s.matched_rows, s.total_rows)}</span></td>'
            f'<td class="c"><span class="metricpair">{seat_parity_pair(s.billing_seats, s.vendor_seats)}</span></td>'
            f'<td class="c"><span class="metricpair">{margin_pair(s.gross_margin_pct, s.gross_margin)}</span></td>'
            f'</tr>'
        )
        rows_html.append(row)

    st.markdown(
        '<table class="recon"><thead>' + header + '</thead><tbody>'
        + "".join(rows_html) + '</tbody></table>',
        unsafe_allow_html=True,
    )


def render_glossary(
    title: str,
    entries: list[tuple[str, str]],
    expanded: bool = False,
) -> None:
    """Render a short definition list inside a collapsible expander.

    Kept intentionally lightweight (semantic HTML in an expander) so business
    stakeholders can decode a chip or a flag without leaving the tab.
    """
    with st.expander(title, expanded=expanded):
        rows = []
        for term, definition in entries:
            rows.append(
                f'<div style="display:grid;grid-template-columns:220px 1fr;gap:14px;'
                f'padding:6px 2px;border-bottom:1px solid var(--cw-line);">'
                f'<div style="color:var(--cw-text-0);font-weight:600;font-size:0.85rem;">'
                f'{html.escape(term)}</div>'
                f'<div style="color:var(--cw-text-1);font-size:0.85rem;line-height:1.45;">'
                f'{html.escape(definition)}</div></div>'
            )
        st.markdown("".join(rows), unsafe_allow_html=True)


def render_exception_detail(
    rollup: pd.DataFrame,
    detail_with_categories: pd.DataFrame,
    dataframe_key: str,
    period_note: str | None = None,
) -> None:
    """Interactive Billing Exception Summary — click a row to drill into every
    account/product row carrying that flag. Works for a single vendor OR an
    aggregated portfolio (multi-vendor) view."""
    if rollup.empty:
        st.markdown('<div class="note">No exceptions this period.</div>', unsafe_allow_html=True)
        return

    display = rollup.copy()
    display["Affected Accounts"] = display["Affected Accounts"].map(fmt_num)
    display["Abs Seat Variance"] = display["Seat Variance"].map(fmt_num)
    display["Est. $ Impact"] = display["EST_DOLLAR_IMPACT"].map(fmt_short_money)
    display_cols = [
        "Exception Type", "Affected Accounts", "Abs Seat Variance",
        "Est. $ Impact", "Action Needed",
    ]

    hint = "Click a row to drill into every account carrying that flag. Click another to swap the view."
    if period_note:
        hint = f"{hint} \u2014 {period_note}"
    st.caption(hint)
    event = st.dataframe(
        display[display_cols].reset_index(drop=True),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"exc_summary_{dataframe_key}",
    )

    selected_rows = []
    try:
        selected_rows = event.selection.rows  # type: ignore[union-attr]
    except Exception:
        selected_rows = []

    if not selected_rows:
        return

    row_idx = int(selected_rows[0])
    ordered = rollup.reset_index(drop=True)
    if row_idx >= len(ordered):
        return
    selected_exception = str(ordered.iloc[row_idx]["Exception Type"])

    drill = detail_with_categories
    # Defensive: if the shared detail load failed (e.g., a bad column in the
    # SELECT, a stale cache, or a schema drift) the rollup can still populate
    # from Snowflake while `drill` comes back empty or missing EXCEPTION_TYPE.
    # Fall back to a friendly notice instead of a KeyError.
    if drill is None or drill.empty or "EXCEPTION_TYPE" not in drill.columns:
        st.markdown(
            f'<div class="note">Drill-down unavailable for <b>{html.escape(selected_exception)}</b> '
            f'\u2014 detail table did not load. Try refreshing; if the issue persists check '
            f'the pipeline logs for THIRD_PARTY_RECON_OUTPUT_PROD.</div>',
            unsafe_allow_html=True,
        )
        return
    # Buckets are mutually exclusive \u2014 filter by EXCEPTION_TYPE directly.
    drill = drill[drill["EXCEPTION_TYPE"] == selected_exception]
    if drill.empty:
        st.markdown(
            f'<div class="note">No rows found for exception <b>{html.escape(selected_exception)}</b>.</div>',
            unsafe_allow_html=True,
        )
        return

    drill = drill.sort_values("ABS_QTY_DELTA", ascending=False).copy()
    drill["BILLING_MONTH"] = pd.to_datetime(
        drill["BILLING_MONTH"], errors="coerce"
    ).dt.strftime("%Y-%m")
    drill["DUPLICATE_BILLING"] = (
        drill.get("DUPLICATE_BILLING", drill.get("DUPLICATE_BILLING_FLAG", "N"))
        .astype(str)
        .str.upper()
        .map({"TRUE": "Y", "FALSE": "N", "Y": "Y", "N": "N"})
        .fillna("N")
    )
    drill["PARTNER_DISPLAY"] = (
        drill.get("PARTNER_DISPLAY_NAME", drill.get("VENDOR_PARTNER_NAME", ""))
        .fillna("(unknown)")
        .astype(str)
    )
    drill["PRODUCT_DISPLAY"] = (
        drill.get("PRODUCT_DISPLAY", drill.get("VENDOR_PRODUCT", ""))
        .fillna("(unmapped)")
        .astype(str)
    )
    drill["SF_ID"] = _salesforce_account_links(drill)
    # OUTCOME_FLAG intentionally last so the primary business dimensions
    # (month, account, product, seats, amounts) render leftmost.
    # If the input carries a _VENDOR column (portfolio view), surface it early.
    _col_source = [
        "BILLING_MONTH",
    ]
    if "_VENDOR" in drill.columns:
        _col_source.append("_VENDOR")
    _col_source += [
        "SF_ID", "PARTNER_DISPLAY", "PRODUCT_DISPLAY",
        "ACTION_NEEDED", "VENDOR_QUANTITY",
        "TOTAL_BILLING_QUANTITY",
        # API_QUANTITY / AVG_API_QUANTITY surface TRT / vendor-API telemetry
        # for pipelines that publish it (SentinelOne, Bitdefender, Webroot,
        # Auvik, Proofpoint). Null for
        # vendors without an API feed — the column renders empty in that case.
        "API_QUANTITY", "AVG_API_QUANTITY",
        "QTY_DELTA", "ABS_QTY_DELTA",
        "VENDOR_UNIT_PRICE", "TOTAL_BILLING_UNIT_PRICE",
        "VENDOR_AMOUNT",
        # API_AMOUNT / AVG_API_AMOUNT = API-seat $ hypotheticals at
        # vendor unit price (point-in-time vs cycle-average).
        "API_AMOUNT", "AVG_API_AMOUNT",
        "TOTAL_BILLING_AMOUNT", "AMOUNT_DELTA",
        "VENDOR_INVOICE_SKU", "VENDOR_INVOICE_RATE_SOURCE",
        "DUPLICATE_BILLING",
        "INVESTIGATION_REASON", "OUTCOME_FLAG",
    ]
    detail_cols = [c for c in _col_source if c in drill.columns]

    st.markdown(f"#### {selected_exception} — {len(drill):,} rows")
    if selected_exception == BUCKET_VENDOR_NO_CW:
        st.caption(
            "Vendor amount exceeds CW amount under the strict canonical rule. "
            "Finance / Sales must close the billing gap."
        )
    st.dataframe(
        drill[detail_cols],
        column_config={
            "BILLING_MONTH": st.column_config.TextColumn("Billing Month"),
            "_VENDOR": st.column_config.TextColumn("Vendor"),
            "SF_ID": _salesforce_link_column(),
            "PARTNER_DISPLAY": st.column_config.TextColumn("Partner"),
            "PRODUCT_DISPLAY": st.column_config.TextColumn("Product"),
            "ACTION_NEEDED": st.column_config.TextColumn("Action Needed"),
            "VENDOR_QUANTITY": st.column_config.NumberColumn("Vendor Seats", format="%d"),
            "TOTAL_BILLING_QUANTITY": st.column_config.NumberColumn("CW Billed Seats", format="%d"),
            "API_QUANTITY": st.column_config.NumberColumn(
                "API Qty",
                format="%d",
                help="Total seats/agents recorded by the vendor's API/telemetry feed for this account-month. NULL for vendors without an API feed.",
            ),
            "AVG_API_QUANTITY": st.column_config.NumberColumn(
                "Avg API Qty",
                format="%.1f",
                help="Daily average of API-reported seats/agents across the billing month. NULL for vendors without an API feed.",
            ),
            "QTY_DELTA": st.column_config.NumberColumn("Qty Delta", format="%d"),
            "ABS_QTY_DELTA": st.column_config.NumberColumn("|Qty Delta|", format="%d"),
            "VENDOR_UNIT_PRICE": st.column_config.NumberColumn("Vendor $/seat", format="$%.4f"),
            "TOTAL_BILLING_UNIT_PRICE": st.column_config.NumberColumn("CW Billed $/seat", format="$%.4f"),
            "VENDOR_AMOUNT": st.column_config.NumberColumn("Vendor Amount", format="$%.2f"),
            "API_AMOUNT": st.column_config.NumberColumn(
                "API $ (pt-in-time)",
                format="$%.2f",
                help="API_QUANTITY x VENDOR_UNIT_PRICE — what the vendor invoice would be if priced strictly on the point-in-time API seat snapshot (day-20 for Proofpoint, 21 for S1/BD, 19 for Webroot).",
            ),
            "AVG_API_AMOUNT": st.column_config.NumberColumn(
                "API $ (cycle avg)",
                format="$%.2f",
                help="AVG_API_QUANTITY x VENDOR_UNIT_PRICE — what the vendor invoice would be if priced on the cycle-average API seat count instead of the point-in-time snapshot.",
            ),
            "TOTAL_BILLING_AMOUNT": st.column_config.NumberColumn("CW Billing Amount", format="$%.2f"),
            "AMOUNT_DELTA": st.column_config.NumberColumn("Amount Delta", format="$%.2f"),
            "VENDOR_INVOICE_SKU": st.column_config.TextColumn("Vendor Invoice SKU"),
            "VENDOR_INVOICE_RATE_SOURCE": st.column_config.TextColumn("Rate Source"),
            "DUPLICATE_BILLING": st.column_config.TextColumn(
                "Duplicate Billing",
                help="Informational signal: Y means both CW billing views overlapped on this row.",
            ),
            "INVESTIGATION_REASON": st.column_config.TextColumn("Investigation Reason"),
            "OUTCOME_FLAG": st.column_config.TextColumn("Outcome Flag"),
        },
        use_container_width=True,
        hide_index=True,
    )


with tab_close:
    # ------------------------------------------------------------------
    # Multi-vendor portfolio aggregates for the Monthly Reconciliation tab.
    # Exception summary + action queues are computed across ALL selected
    # vendors so the numbers scale as vendors are added/removed.
    # ------------------------------------------------------------------
    portfolio_roll = portfolio_exception_rollup(slices)
    portfolio_detail = portfolio_detail_with_categories(slices)
    months_in_scope = int(portfolio_detail["BILLING_MONTH"].nunique()) if (
        not portfolio_detail.empty and "BILLING_MONTH" in portfolio_detail.columns
    ) else 1

    def queue_totals(categories: list[str]) -> tuple[float, float, int, int]:
        """Return (dedup $, dedup seats, unique accounts, account-months) for the
        row cohort matching the given exception buckets.

        Buckets are mutually exclusive (see _classify_bucket_series), so this
        is a straight EXCEPTION_TYPE filter \u2014 no deduplication needed.
        """
        if portfolio_detail.empty or "EXCEPTION_TYPE" not in portfolio_detail.columns:
            return 0.0, 0.0, 0, 0
        q = portfolio_detail[portfolio_detail["EXCEPTION_TYPE"].isin(categories)]
        if q.empty:
            return 0.0, 0.0, 0, 0
        q = q.copy()
        if "SF_ID" in q.columns:
            q["_ACCT"] = q["SF_ID"].astype(str)
            q = q[~q["_ACCT"].isin(["", "None", "nan", "NaT"])].copy()
            uniq_accounts = int(q["_ACCT"].nunique())
        else:
            uniq_accounts = 0
        if months_in_scope > 1 and {"_ACCT", "BILLING_MONTH"}.issubset(q.columns):
            acct_months = int(q[["_ACCT", "BILLING_MONTH"]].dropna().drop_duplicates().shape[0])
        else:
            acct_months = uniq_accounts
        q_amt = float(
            pd.to_numeric(q.get("AMOUNT_DELTA"), errors="coerce").fillna(0).abs().sum()
        ) if "AMOUNT_DELTA" in q.columns else 0.0
        q_seats = float(
            pd.to_numeric(q.get("ABS_QTY_DELTA"), errors="coerce").fillna(0).sum()
        ) if "ABS_QTY_DELTA" in q.columns else 0.0
        return q_amt, q_seats, uniq_accounts, acct_months

    finance_amt, _, finance_accts, finance_acct_months = queue_totals(FINANCE_QUEUE_CATEGORIES)
    sku_amt, _, sku_accts, sku_acct_months = queue_totals(OPS_QUEUE_CATEGORIES)
    timing_amt, _, timing_accts, timing_acct_months = queue_totals(TIMING_QUEUE_CATEGORIES)

    # Contract-price overlay rollup summed across all selected vendors.
    # NOTE: rate_below_cost_dollars returns |amount| (absolute); we negate to
    # preserve the historical sign convention that below_dollars is negative.
    below_dollars = -sum(s.rate_below_cost_dollars for s in slices.values())
    below_accts = sum(s.rate_below_cost_accounts for s in slices.values())
    above_dollars = sum(s.rate_above_cost_dollars for s in slices.values())

    # Status matrix (already iterates all selected slices)
    _vendor_count_label = f"{len(slices)} vendor{'s' if len(slices) != 1 else ''}"
    st.markdown(f"### Vendor Reconciliation Status \u2014 {period_label} \u00B7 {_vendor_count_label}")
    render_status_matrix()
    render_glossary("Column definitions \u2014 what each check means", COLUMN_GLOSSARY)

    # Interactive exception summary — aggregated across selected vendors
    st.markdown("### Billing Exception Summary")
    _dataframe_key = "portfolio_" + "_".join(sorted(slices.keys())) or "portfolio_empty"
    _period_note = (
        f"Aggregated across {len(slices)} selected vendor(s) for {period_label}."
    )
    render_exception_detail(portfolio_roll, portfolio_detail, _dataframe_key, _period_note)
    render_glossary(
        "Exception type definitions \u2014 how each bucket is defined",
        EXCEPTION_TYPE_GLOSSARY,
    )

    # Action Queues cards at bottom (dollar impact aggregated across vendors)
    st.markdown("### Action Queues")
    render_cards(
        [
            card_html(
                "red",
                fmt_short_money(finance_amt),
                f"Revenue Leakage \u2014 Finance Queue ({finance_accts} accounts)",
                "Vendor Billing No CW Billing + Vendor Billing Insufficient CW Billing + API Usage Insufficient CW Billing + Vendor SKU No CW SKU.",
                hint="Vendor is billing CW for seats/products CW is not re-billing to the partner. Requires Finance review to close the gap.",
            ),
            card_html(
                "amber",
                fmt_short_money(sku_amt),
                f"Ops Review Queue ({sku_accts} accounts)",
                "CW Billing No Vendor Billing + CW Billing Insufficient Vendor Billing + Duplicates + Unmapped Partners + CW SKU No Vendor SKU.",
                hint="CW is billing more than the vendor invoices, or there are structural data gaps (duplicates, unmapped partners). Ops / Data team review.",
            ),
            card_html(
                "red" if abs(below_dollars) >= 5000 or below_accts >= 10 else "amber",
                fmt_short_money(abs(below_dollars)),
                f"Contract Discount Exposure \u2014 Below Cost ({below_accts} accounts)",
                "Billed below governed vendor contract rate (SentinelOne only). Margin erosion \u2014 pricing review required.",
                hint="CW billed the partner at a rate below what the vendor charges CW per seat. Only applicable for vendors with a governed contract rate table.",
            ),
            card_html(
                "green",
                fmt_short_money(timing_amt),
                "Timing Only \u2014 No Action Required",
                "Marketplace prior-month billing delay. Will self-resolve next cycle.",
                hint="These rows are missing a CW bill today because of a known Marketplace prior-period timing lag. No action needed.",
            ),
        ],
        layout="2x2",
    )

    # ------------------------------------------------------------------
    # Category traceability audit
    # ------------------------------------------------------------------
    # Trust-but-verify panel. Answers the question "are any raw rows being
    # dropped or double-counted between the pipeline output and the tiles?"
    # Every row in the raw detail lands in exactly one of the visible
    # buckets below OR in the "Uncategorized (audit)" pile. Sums are on
    # |AMOUNT_DELTA| so they match the tiles above 1:1.
    # ------------------------------------------------------------------
    with st.expander("Category traceability audit \u2014 raw row counts and \u2192 tile reconciliation", expanded=False):
        if portfolio_detail.empty:
            st.caption("No rows in the current selection.")
        else:
            _det = portfolio_detail.copy()
            _amt = pd.to_numeric(_det.get("AMOUNT_DELTA"), errors="coerce").fillna(0.0)
            _cw_bill = pd.to_numeric(
                _det.get("TOTAL_BILLING_AMOUNT", pd.Series(0.0, index=_det.index)),
                errors="coerce",
            ).fillna(0.0)
            _flag = _det.get("OUTCOME_FLAG", pd.Series("", index=_det.index)).astype(str)
            _et = _det.get("EXCEPTION_TYPE", pd.Series("", index=_det.index)).astype(str)

            # 1. Raw pipeline output \u2014 no app-side filtering applied.
            st.markdown(
                f"**Raw pipeline output:** {len(_det):,} rows loaded from "
                f"THIRD_PARTY_RECON_OUTPUT_PROD for the current selection. Nothing "
                f"below this line filters rows away \u2014 every row is placed "
                f"in a category, and the last section lists any orphans."
            )

            # 2 & 3. Combined pipeline breakdown — one selector picks the
            # classification dimension. Keeps the audit panel sleek while
            # preserving both views under the hood.
            flag_tbl = (
                _det.groupby(_flag, dropna=False)
                .agg(rows=("OUTCOME_FLAG", "size"))
                .reset_index()
                .rename(columns={"index": "OUTCOME_FLAG"})
            )
            flag_tbl["|AMOUNT_DELTA| $"] = (
                _det.assign(_a=_amt.abs(), _f=_flag)
                .groupby("_f", dropna=False)["_a"].sum().reindex(flag_tbl["OUTCOME_FLAG"]).values
            )
            flag_tbl = flag_tbl.sort_values("rows", ascending=False).reset_index(drop=True)
            flag_tbl["|AMOUNT_DELTA| $"] = flag_tbl["|AMOUNT_DELTA| $"].map(lambda v: f"${v:,.0f}")

            et_tbl = (
                _det.groupby(_et, dropna=False)
                .agg(rows=("EXCEPTION_TYPE", "size"))
                .reset_index()
                .rename(columns={"index": "EXCEPTION_TYPE"})
            )
            et_tbl["|AMOUNT_DELTA| $"] = (
                _det.assign(_a=_amt.abs(), _e=_et)
                .groupby("_e", dropna=False)["_a"].sum().reindex(et_tbl["EXCEPTION_TYPE"]).values
            )
            et_tbl = et_tbl.sort_values("rows", ascending=False).reset_index(drop=True)
            et_tbl["|AMOUNT_DELTA| $"] = et_tbl["|AMOUNT_DELTA| $"].map(lambda v: f"${v:,.0f}")

            _dim_choice = st.selectbox(
                "Raw row counts \u2014 choose classification",
                options=[
                    "Pipeline OUTCOME_FLAG",
                    "Upstream EXCEPTION_TYPE",
                ],
                index=0,
                key=f"trace_dim_{_dataframe_key}",
                help=(
                    "OUTCOME_FLAG is the row-level pipeline classification. "
                    "EXCEPTION_TYPE is the display bucket that drives the "
                    "Billing Exception Summary and Action Queue tiles."
                ),
            )
            if _dim_choice == "Pipeline OUTCOME_FLAG":
                st.dataframe(flag_tbl, use_container_width=True, hide_index=True)
                with st.expander("What each OUTCOME_FLAG means", expanded=False):
                    _rows_html = []
                    for term, definition in OUTCOME_FLAG_GLOSSARY:
                        _rows_html.append(
                            f'<div style="display:grid;grid-template-columns:280px 1fr;'
                            f'gap:14px;padding:6px 2px;border-bottom:1px solid var(--cw-line);">'
                            f'<div style="color:var(--cw-text-0);font-weight:600;'
                            f'font-size:0.8rem;font-family:ui-monospace,monospace;">'
                            f'{html.escape(term)}</div>'
                            f'<div style="color:var(--cw-text-1);font-size:0.85rem;'
                            f'line-height:1.45;">{html.escape(definition)}</div></div>'
                        )
                    st.markdown("".join(_rows_html), unsafe_allow_html=True)
            else:
                st.dataframe(et_tbl, use_container_width=True, hide_index=True)
                with st.expander("What each EXCEPTION_TYPE bucket means", expanded=False):
                    _rows_html = []
                    for term, definition in EXCEPTION_TYPE_GLOSSARY:
                        _rows_html.append(
                            f'<div style="display:grid;grid-template-columns:280px 1fr;'
                            f'gap:14px;padding:6px 2px;border-bottom:1px solid var(--cw-line);">'
                            f'<div style="color:var(--cw-text-0);font-weight:600;'
                            f'font-size:0.85rem;">{html.escape(term)}</div>'
                            f'<div style="color:var(--cw-text-1);font-size:0.85rem;'
                            f'line-height:1.45;">{html.escape(definition)}</div></div>'
                        )
                    st.markdown("".join(_rows_html), unsafe_allow_html=True)

            # 4. App-side bucket assignment \u2014 the same math the tiles use.
            #    Buckets are MUTUALLY EXCLUSIVE: each row lands in exactly one.
            _bucket = _classify_bucket_series(_det).astype(str)

            def _fmt(mask: pd.Series) -> tuple[str, str]:
                n = int(mask.sum())
                d = float(_amt[mask].abs().sum())
                return f"{n:,}", f"${d:,.0f}"

            bucket_rows = []
            for label in EXCEPTION_BUCKETS:
                m = _bucket == label
                if not m.any():
                    continue
                n_str, d_str = _fmt(m)
                bucket_rows.append({"Bucket": label, "Rows": n_str, "|AMOUNT_DELTA| $": d_str})
            # Total row for parity check.
            total_n = len(_det)
            total_d = float(_amt.abs().sum())
            bucket_rows.append({
                "Bucket": "\u2192 Total (all buckets are mutually exclusive)",
                "Rows": f"{total_n:,}",
                "|AMOUNT_DELTA| $": f"${total_d:,.0f}",
            })
            st.markdown(
                "**App-side buckets \u2014 every row lands in exactly one. Sums drive the "
                "Action Queue tiles above:**"
            )
            st.dataframe(pd.DataFrame(bucket_rows), use_container_width=True, hide_index=True)

            # 5. Orphan check. Every row should have a bucket assigned.
            _orphans = _det.loc[~_bucket.isin(EXCEPTION_BUCKETS)]
            if _orphans.empty:
                st.markdown(
                    "**Orphan check:** 0 rows unaccounted for. "
                    "Every raw row is placed in exactly one bucket above."
                )
            else:
                st.markdown(
                    f"**Orphan check:** {len(_orphans):,} rows do not match any "
                    f"bucket. These would be invisible in the tiles \u2014 review below:"
                )
                _keep = [c for c in [
                    "BILLING_MONTH", "SF_ID", "PARTNER_DISPLAY_NAME",
                    "PRODUCT_DISPLAY", "OUTCOME_FLAG", "EXCEPTION_TYPE",
                    "TOTAL_BILLING_AMOUNT", "VENDOR_AMOUNT", "AMOUNT_DELTA",
                ] if c in _orphans.columns]
                _orphan_display = _orphans.copy()
                if "SF_ID" in _orphan_display.columns:
                    _orphan_display["SF_ID"] = _salesforce_account_links(_orphan_display)
                st.dataframe(
                    _orphan_display[_keep],
                    column_config={"SF_ID": _salesforce_link_column()},
                    use_container_width=True,
                    hide_index=True,
                )


# ---- Tab 2: Vendor Deep Dive ---------------------------------------------

@st.cache_data(ttl=DATA_TTL_SECONDS, show_spinner=False)
def _monthly_recon_rows(vendor_key: str, months_key: str, freshness_: str) -> pd.DataFrame:
    """Precomputed month-level rollup for the Deep Dive table. Cached on
    (vendor, month subset, pipeline freshness) so filter round-trips are
    dict lookups after the first render."""
    _ = freshness_
    vc = vendor_by_key(vendor_key)
    data = vc["data"]
    summary_all = data["summary"]
    detail_all = data["detail"]
    coverage_all = data["coverage"]
    if months_key:
        months = [pd.to_datetime(m) for m in months_key.split("|")]
        summary_all = summary_all[summary_all["BILLING_MONTH"].isin(months)]
        detail_all = detail_all[detail_all["BILLING_MONTH"].isin(months)]
        if not coverage_all.empty:
            coverage_all = coverage_all[coverage_all["BILLING_MONTH"].isin(months)]
    if summary_all.empty:
        return pd.DataFrame()
    monthly = summary_all.sort_values("BILLING_MONTH").copy()
    out_rows = []
    for _, row in monthly.iterrows():
        month = pd.to_datetime(row["BILLING_MONTH"])
        vs = float(row.get("TOTAL_VENDOR_SEATS") or 0)
        bs = float(row.get("TOTAL_BILLING_SEATS") or 0)
        rev = float(row.get("TOTAL_BILLING_AMOUNT") or 0)
        cost = float(row.get("TOTAL_VENDOR_AMOUNT") or 0)
        total_rows = float(row.get("TOTAL_ROWS") or 0)
        matched_rows = float(row.get("PERFECT_MATCH_ROWS") or 0)
        clear_rate = clear_rate_value(matched_rows, total_rows)
        seat_parity = seat_parity_value(bs, vs)
        margin_amount = rev - cost
        margin_pct = margin_amount / rev if rev else 0.0
        health_status, health_reason = vendor_health_status(
            clear_rate,
            seat_parity,
            margin_pct,
            margin_amount,
        )
        out_rows.append({
            "BILLING_MONTH": month,
            "VS": vs, "BS": bs, "REV": rev, "COST": cost,
            "TOTAL_ROWS": total_rows, "MATCHED_ROWS": matched_rows,
            "HEALTH_STATUS": health_status, "HEALTH_REASON": health_reason,
            "CLEAR_RATE": clear_rate, "SEAT_PARITY": seat_parity,
            "MARGIN_AMOUNT": margin_amount, "MARGIN_PCT": margin_pct,
        })
    return pd.DataFrame(out_rows)


def render_monthly_recon_table(vendor_key: str) -> None:
    monthly = _monthly_recon_rows(vendor_key, _months_key(selected_month_ts_list), freshness)
    if monthly.empty:
        st.markdown('<div class="note">No monthly summary rows in the selected period.</div>', unsafe_allow_html=True)
        return

    display_rows: list[dict[str, Any]] = []
    for r in monthly.itertuples(index=False):
        vs, bs, rev, cost = r.VS, r.BS, r.REV, r.COST
        cw_vs_vendor_pct = ((bs - vs) / vs * 100) if vs else 0.0
        gm = rev - cost
        gm_pct = gm / rev if rev else 0
        display_rows.append({
            "Month": month_label(r.BILLING_MONTH),
            "Vendor Health": HEALTH_LABELS[r.HEALTH_STATUS],
            "Health Reason": r.HEALTH_REASON,
            "Vendor Seats": vs,
            "CW Billed": bs,
            "CW vs. Vendor": cw_vs_vendor_pct,
            "CW Revenue": rev,
            "Vendor Cost": cost,
            "Margin": gm,
            "Margin %": gm_pct * 100,
        })

    ytd_vs = float(monthly["VS"].sum())
    ytd_bs = float(monthly["BS"].sum())
    ytd_rev = float(monthly["REV"].sum())
    ytd_cost = float(monthly["COST"].sum())
    ytd_gm = ytd_rev - ytd_cost
    ytd_gm_pct = ytd_gm / ytd_rev if ytd_rev else 0
    ytd_cw_vs_vendor_pct = ((ytd_bs - ytd_vs) / ytd_vs * 100) if ytd_vs else 0.0
    ytd_rows = float(monthly["TOTAL_ROWS"].sum())
    ytd_matched = float(monthly["MATCHED_ROWS"].sum())
    ytd_clear_rate = clear_rate_value(ytd_matched, ytd_rows)
    ytd_seat_parity = seat_parity_value(ytd_bs, ytd_vs)
    ytd_health, ytd_health_reason = vendor_health_status(
        ytd_clear_rate,
        ytd_seat_parity,
        ytd_gm_pct,
        ytd_gm,
    )
    display_rows.append({
        "Month": "YTD",
        "Vendor Health": HEALTH_LABELS[ytd_health],
        "Health Reason": ytd_health_reason,
        "Vendor Seats": ytd_vs,
        "CW Billed": ytd_bs,
        "CW vs. Vendor": ytd_cw_vs_vendor_pct,
        "CW Revenue": ytd_rev,
        "Vendor Cost": ytd_cost,
        "Margin": ytd_gm,
        "Margin %": ytd_gm_pct * 100,
    })
    st.dataframe(
        pd.DataFrame(display_rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Vendor Seats": st.column_config.NumberColumn(format="%.0f"),
            "CW Billed": st.column_config.NumberColumn(format="%.0f"),
            "CW vs. Vendor": st.column_config.NumberColumn(format="%+.1f%%"),
            "CW Revenue": st.column_config.NumberColumn(format="$%.2f"),
            "Vendor Cost": st.column_config.NumberColumn(format="$%.2f"),
            "Margin": st.column_config.NumberColumn(format="$%.2f"),
            "Margin %": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )


def render_seat_trend(vendor_key: str) -> None:
    data = vendor_by_key(vendor_key)["data"]
    summary_all = data["summary"].sort_values("BILLING_MONTH")
    if selected_month_ts_list and len(selected_month_ts_list) < len(months_available):
        summary_all = summary_all[summary_all["BILLING_MONTH"].isin(selected_month_ts_list)]
    if summary_all.empty:
        return
    rows: list[dict[str, Any]] = []
    for _, row in summary_all.iterrows():
        vs = float(row.get("TOTAL_VENDOR_SEATS") or 0)
        bs = float(row.get("TOTAL_BILLING_SEATS") or 0)
        cw_vs_vendor = ((bs - vs) / vs) if vs else 0.0
        rows.append({
            "Month": month_label(row["BILLING_MONTH"]),
            "Vendor": vs,
            "CW Billed": bs,
            "CW vs. Vendor": cw_vs_vendor * 100,
        })
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Vendor": st.column_config.NumberColumn(format="%.0f"),
            "CW Billed": st.column_config.NumberColumn(format="%.0f"),
            "CW vs. Vendor": st.column_config.NumberColumn(format="%+.1f%%"),
        },
    )


@st.cache_data(ttl=DATA_TTL_SECONDS, show_spinner=False)
def _load_vendor_invoice_usage_intra(
    vendor_name: str,
    months_key: str,
    freshness_: str,
) -> pd.DataFrame:
    """Load the precomputed vendor-internal invoice-vs-usage control.

    The upstream table uses a vendor-aware comparison grain. The app keeps the
    Snowflake query narrow, gates the display to months where raw vendor usage
    exists, and rolls selected months up for a compact comparison.
    """
    vendor_sql = str(vendor_name).replace("'", "''")
    if months_key:
        month_values = []
        for month in months_key.split("|"):
            ts = pd.to_datetime(month, errors="coerce")
            if pd.notna(ts):
                month_values.append(f"'{ts:%Y-%m-%d}'")
        month_sql = (
            f" AND t.BILLING_MONTH IN ({','.join(month_values)})"
            if month_values
            else ""
        )
    else:
        month_sql = ""

    df = upper_cols(_try_query(
        f"""
        WITH usage_months AS (
            SELECT DISTINCT BILLING_MONTH
            FROM {SCHEMA}.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
            WHERE VENDOR = '{vendor_sql}'
              AND (
                    COALESCE(VENDOR_RAW_USAGE_SEATS, 0) <> 0
                 OR COALESCE(VENDOR_RAW_USAGE_AMOUNT, 0) <> 0
              )
        )
        SELECT
            t.VENDOR,
            t.BILLING_MONTH,
            t.INV_TYPE,
            t.COMPARISON_GRAIN,
            t.COMPARISON_PARTNER,
            t.INVOICE_ID,
            t.INVOICE_LINK_KEYS,
            t.SKU,
            t.VENDOR_INVOICE_SKU,
            t.VENDOR_USAGE_SKU,
            t.VENDOR_INVOICE_SEATS,
            t.VENDOR_RAW_USAGE_SEATS,
            t.VENDOR_INVOICE_AMOUNT,
            t.VENDOR_RAW_USAGE_AMOUNT,
            t.DELTA_SEATS,
            t.DELTA_AMOUNT,
            t.SOURCE_STATUS
                FROM {SCHEMA}.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD t
                INNER JOIN usage_months u
                    ON u.BILLING_MONTH = t.BILLING_MONTH
                WHERE t.VENDOR = '{vendor_sql}'{month_sql}
        ORDER BY BILLING_MONTH, INV_TYPE, INVOICE_ID, ABS(DELTA_AMOUNT) DESC, ABS(DELTA_SEATS) DESC, SKU
        """,
        freshness_,
    ))
    if not df.empty and "BILLING_MONTH" in df.columns:
        df["BILLING_MONTH"] = pd.to_datetime(df["BILLING_MONTH"], errors="coerce")
    return df


def _sum_preserve_null(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce")
    return values.sum(min_count=1)


def render_vendor_invoice_usage_intra(vendor_name: str) -> None:
    is_auvik = vendor_name.strip().upper() == "AUVIK"
    is_bitdefender = vendor_name.strip().upper() == "BITDEFENDER"
    usage_source_label = "Royalty Report" if is_bitdefender else "Vendor Raw Usage"
    st.markdown(f"### Vendor Invoice vs. {usage_source_label}")

    raw = _load_vendor_invoice_usage_intra(
        vendor_name,
        _months_key(selected_month_ts_list),
        freshness,
    )
    if raw.empty:
        st.markdown(
            '<div class="note">No invoice-vs-raw-usage rows are available for this vendor in the selected period.</div>',
            unsafe_allow_html=True,
        )
        return

    if is_bitdefender:
        st.caption(
            "Bitdefender has no product-telemetry usage feed. This control compares "
            "the parsed vendor invoice with the Product Management royalty report at "
            "month/SKU-family grain."
        )

    work = raw.copy()
    work["INV_TYPE"] = work.get("INV_TYPE", pd.Series("UNCLASSIFIED", index=work.index)).fillna("UNCLASSIFIED").astype(str)
    work["COMPARISON_GRAIN"] = work.get("COMPARISON_GRAIN", pd.Series("", index=work.index)).fillna("").astype(str)
    work["COMPARISON_PARTNER"] = work.get("COMPARISON_PARTNER", pd.Series("", index=work.index)).fillna("").astype(str)
    work["INVOICE_ID"] = work.get("INVOICE_ID", pd.Series("UNIDENTIFIED_INVOICE", index=work.index)).fillna("UNIDENTIFIED_INVOICE").astype(str)
    work["INVOICE_LINK_KEYS"] = work.get("INVOICE_LINK_KEYS", pd.Series("", index=work.index)).fillna("").astype(str)
    work["SOURCE_STATUS"] = work.get("SOURCE_STATUS", pd.Series("", index=work.index)).fillna("").astype(str)
    work["SKU"] = work.get("SKU", pd.Series("", index=work.index)).fillna("(missing sku)").astype(str)
    work["VENDOR_INVOICE_SKU"] = (
        work.get("VENDOR_INVOICE_SKU", pd.Series("", index=work.index))
        .fillna("")
        .astype(str)
    )
    work["VENDOR_USAGE_SKU"] = (
        work.get("VENDOR_USAGE_SKU", pd.Series("", index=work.index))
        .fillna("")
        .astype(str)
    )
    metric_cols = [
        "VENDOR_INVOICE_SEATS",
        "VENDOR_RAW_USAGE_SEATS",
        "VENDOR_INVOICE_AMOUNT",
        "VENDOR_RAW_USAGE_AMOUNT",
    ]
    for col in metric_cols:
        work[col] = pd.to_numeric(work.get(col), errors="coerce")

    if is_auvik and work["VENDOR_INVOICE_SKU"].str.match(
        r"^(BASIC|ADVANCED|PREMIER)( NEW)?$",
        case=False,
    ).any():
        st.caption(
            "Auvik BASIC, ADVANCED, and PREMIER rows are ConnectWise OEM invoice "
            "tiers. They are valid invoice charges, but the partner-level Auvik "
            "usage workbook does not contain the corresponding OEM population, so "
            "these rows remain invoice-only rather than true usage variances."
        )

    invoice_options = sorted(work["INVOICE_ID"].dropna().astype(str).unique().tolist())
    selected_invoice = st.selectbox(
        "Invoice",
        ["All Sources", *invoice_options],
        key=f"vendor-intra-invoice-{vendor_name}",
    )
    if selected_invoice != "All Sources":
        work = work[work["INVOICE_ID"] == selected_invoice].copy()

    comparison_grains = sorted(
        {value for value in work["COMPARISON_GRAIN"].astype(str) if value.strip()}
    )
    if is_auvik:
        st.caption("Display grain: Vendor × Invoice × SKU")
    elif comparison_grains:
        st.caption(f"Comparison grain: {', '.join(comparison_grains)}")
    if (work["SOURCE_STATUS"] == "UNALLOCATED_USAGE_POOL").any():
        st.caption(
            f"Unallocated {usage_source_label.lower()} rows have no parsed invoice "
            "for the same month, lane, and SKU; they are retained as source-coverage gaps."
        )

    usage_seats_label = f"{usage_source_label} Seats"
    usage_amount_label = f"{usage_source_label} Amount"

    sku_rollup = (
        work.groupby(
            ["INVOICE_ID", "SKU"],
            dropna=False,
        )
        .agg(
            **{
                "Vendor Invoice Seats": ("VENDOR_INVOICE_SEATS", _sum_preserve_null),
                usage_seats_label: ("VENDOR_RAW_USAGE_SEATS", _sum_preserve_null),
                "Vendor Invoice Amount": ("VENDOR_INVOICE_AMOUNT", _sum_preserve_null),
                usage_amount_label: ("VENDOR_RAW_USAGE_AMOUNT", _sum_preserve_null),
                "Invoice Link Keys": ("INVOICE_LINK_KEYS", "first"),
            }
        )
        .reset_index()
        .rename(
            columns={
                "INVOICE_ID": "Invoice ID",
            }
        )
    )
    if is_auvik:
        comparable = (
            sku_rollup["Vendor Invoice Seats"].notna()
            & sku_rollup[usage_seats_label].notna()
        )
        sku_rollup["Delta Seats"] = (
            sku_rollup[usage_seats_label] - sku_rollup["Vendor Invoice Seats"]
        ).where(comparable)
        sku_rollup["Delta Amount"] = (
            sku_rollup[usage_amount_label] - sku_rollup["Vendor Invoice Amount"]
        ).where(comparable)
        oem_invoice = sku_rollup["SKU"].str.match(
            r"^(BASIC|ADVANCED|PREMIER)( NEW)?$",
            case=False,
        )
        sku_rollup["Status"] = "Variance"
        sku_rollup.loc[
            sku_rollup["Vendor Invoice Seats"].isna(), "Status"
        ] = "Usage only"
        sku_rollup.loc[
            sku_rollup[usage_seats_label].isna(), "Status"
        ] = "Invoice only"
        sku_rollup.loc[
            sku_rollup[usage_seats_label].isna() & oem_invoice, "Status"
        ] = "OEM invoice — no usage feed"
        sku_rollup.loc[
            comparable
            & sku_rollup["Delta Seats"].abs().lt(0.0001)
            & sku_rollup["Delta Amount"].abs().lt(0.01),
            "Status",
        ] = "Match"
    else:
        sku_rollup["Delta Seats"] = (
            sku_rollup[usage_seats_label].fillna(0)
            - sku_rollup["Vendor Invoice Seats"].fillna(0)
        )
        sku_rollup["Delta Amount"] = (
            sku_rollup[usage_amount_label].fillna(0)
            - sku_rollup["Vendor Invoice Amount"].fillna(0)
        )
    sku_rollup["_abs_delta_amount"] = sku_rollup["Delta Amount"].abs()
    sku_rollup["_abs_delta_seats"] = sku_rollup["Delta Seats"].abs()
    sku_rollup = sku_rollup.sort_values(
        ["_abs_delta_amount", "_abs_delta_seats", "Invoice ID", "SKU"],
        ascending=[False, False, True, True],
    ).drop(columns=["_abs_delta_amount", "_abs_delta_seats"])

    total = {
        "SKU": "TOTAL",
        "Invoice ID": "",
        "Invoice Link Keys": "",
        "Vendor Invoice Seats": _sum_preserve_null(sku_rollup["Vendor Invoice Seats"]),
        usage_seats_label: _sum_preserve_null(sku_rollup[usage_seats_label]),
        "Vendor Invoice Amount": _sum_preserve_null(sku_rollup["Vendor Invoice Amount"]),
        usage_amount_label: _sum_preserve_null(sku_rollup[usage_amount_label]),
    }
    if is_auvik and (
        sku_rollup["Vendor Invoice Seats"].isna().any()
        or sku_rollup[usage_seats_label].isna().any()
    ):
        total["Delta Seats"] = float("nan")
        total["Delta Amount"] = float("nan")
        total["Status"] = "Incomplete source coverage"
    else:
        total["Delta Seats"] = (
            (0 if pd.isna(total[usage_seats_label]) else total[usage_seats_label])
            - (0 if pd.isna(total["Vendor Invoice Seats"]) else total["Vendor Invoice Seats"])
        )
        total["Delta Amount"] = (
            (0 if pd.isna(total[usage_amount_label]) else total[usage_amount_label])
            - (0 if pd.isna(total["Vendor Invoice Amount"]) else total["Vendor Invoice Amount"])
        )
        if is_auvik:
            total["Status"] = "Match" if (
                abs(total["Delta Seats"]) < 0.0001
                and abs(total["Delta Amount"]) < 0.01
            ) else "Variance"
    display = pd.concat([sku_rollup, pd.DataFrame([total])], ignore_index=True)

    display_columns = [
        "SKU",
        "Invoice ID",
        "Vendor Invoice Seats",
        usage_seats_label,
        "Vendor Invoice Amount",
        usage_amount_label,
        "Delta Seats",
        "Delta Amount",
    ]
    if is_auvik:
        display_columns.insert(2, "Status")

    def _invoice_links(link_keys: object, invoice_ids: object) -> str:
        url_by_invoice: dict[str, str] = {}
        for token in str(link_keys or "").split(" | "):
            invoice_id, separator, url = token.partition("~~")
            invoice_id = invoice_id.strip()
            url = url.strip() if separator else ""
            if invoice_id and url.startswith("https://6230579.app.netsuite.com/"):
                url_by_invoice[invoice_id] = url

        links = []
        seen = set()
        for invoice_id in str(invoice_ids or "").split(" | "):
            invoice_id = invoice_id.strip()
            if not invoice_id or invoice_id in seen:
                continue
            seen.add(invoice_id)
            escaped_id = html.escape(invoice_id)
            url = url_by_invoice.get(invoice_id, "")
            if url:
                links.append(
                    f'<a href="{html.escape(url, quote=True)}" target="_blank" '
                    f'rel="noopener noreferrer">{escaped_id}</a>'
                )
            else:
                links.append(escaped_id)
        return " <span style='color:var(--cw-text-2)'>|</span> ".join(links)

    header = "<tr>" + "".join(
        f"<th>{html.escape(column)}</th>" for column in display_columns
    ) + "</tr>"
    body = []
    numeric_columns = {
        "Vendor Invoice Seats", usage_seats_label, "Delta Seats",
        "Vendor Invoice Amount", usage_amount_label, "Delta Amount",
    }
    money_columns = {
        "Vendor Invoice Amount", "Vendor Raw Usage Amount", "Delta Amount",
    }
    for _, row in display.iterrows():
        cells = []
        for column in display_columns:
            if column == "Invoice ID":
                value = _invoice_links(row.get("Invoice Link Keys"), row.get(column))
            elif column in numeric_columns:
                raw_value = row.get(column)
                if pd.isna(raw_value):
                    value = ""
                elif column in money_columns:
                    value = html.escape(fmt_money(float(raw_value)))
                else:
                    value = html.escape(fmt_num(float(raw_value)))
            else:
                value = html.escape(str(row.get(column) or ""))
            css_class = ' class="num"' if column in numeric_columns else ""
            cells.append(f"<td{css_class}>{value}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    st.markdown(
        '<div style="max-height:420px;overflow:auto">'
        '<table class="recon"><thead>' + header + '</thead><tbody>'
        + "".join(body) + '</tbody></table></div>',
        unsafe_allow_html=True,
    )

    render_vendor_api_amount_comparison(vendor_name)


def render_vendor_api_amount_comparison(vendor_name: str) -> None:
    """Per-SKU comparison of point-in-time vs cycle-average API-priced $.

    Purpose: quantify the dollar variance between billing the vendor invoice
    on the point-in-time API snapshot (e.g. Proofpoint day-20) vs. the
    cycle-average API seat count across the vendor's monthly cycle
    (day-20 of prior month through day-20 of current month).

    Columns:
      * SKU (VENDOR_PRODUCT rollup)
      * API Seats (point-in-time)    = SUM(API_QUANTITY)
      * API Seats (cycle avg)         = SUM(AVG_API_QUANTITY)
      * CW Billed $ @ API pt-in-time  = SUM(API_QUANTITY x VENDOR_UNIT_PRICE)
      * CW Billed $ @ API cycle avg   = SUM(AVG_API_QUANTITY x VENDOR_UNIT_PRICE)
      * Variance $                    = avg - point
      * Variance %                    = variance / point

    Only rows with an actual API feed populated are counted, so the
    table renders as "no data" for vendors without an API integration.
    """
    st.markdown("### API Point-in-Time vs. Cycle-Average $ Comparison")

    vendor_sql = str(vendor_name).replace("'", "''")
    month_sql = ""
    if selected_month_ts_list:
        month_values = []
        for month in selected_month_ts_list:
            ts = pd.to_datetime(month, errors="coerce")
            if pd.notna(ts):
                month_values.append(f"'{ts:%Y-%m-%d}'")
        if month_values:
            month_sql = f" AND BILLING_MONTH IN ({','.join(month_values)})"

    df = upper_cols(_try_query(
        f"""
        SELECT
            COALESCE(
                NULLIF(TRIM(PRODUCT_DISPLAY), ''),
                NULLIF(TRIM(VENDOR_PRODUCT), ''),
                '(unmapped)'
            ) AS SKU,
            SUM(API_QUANTITY)     AS API_SEATS_POINT,
            SUM(AVG_API_QUANTITY) AS API_SEATS_AVG,
            SUM(API_AMOUNT)       AS API_AMT_POINT,
            SUM(AVG_API_AMOUNT)   AS API_AMT_AVG,
            COUNT_IF(API_QUANTITY IS NOT NULL)     AS ROWS_WITH_POINT,
            COUNT_IF(AVG_API_QUANTITY IS NOT NULL) AS ROWS_WITH_AVG
        FROM {SCHEMA}.THIRD_PARTY_RECON_OUTPUT_PROD
        WHERE VENDOR = '{vendor_sql}'
          AND (API_QUANTITY IS NOT NULL OR AVG_API_QUANTITY IS NOT NULL){month_sql}
        GROUP BY 1
        ORDER BY API_AMT_POINT DESC NULLS LAST
        """,
        freshness,
    ))

    if df.empty:
        st.markdown(
            '<div class="note">No API-feed rows are available for this vendor '
            'in the selected period. API amount comparison is only populated for '
            'vendors with a live API integration.</div>',
            unsafe_allow_html=True,
        )
        return

    df["API_AMT_POINT"] = pd.to_numeric(df["API_AMT_POINT"], errors="coerce").fillna(0.0)
    df["API_AMT_AVG"] = pd.to_numeric(df["API_AMT_AVG"], errors="coerce").fillna(0.0)
    df["API_SEATS_POINT"] = pd.to_numeric(df["API_SEATS_POINT"], errors="coerce").fillna(0.0)
    df["API_SEATS_AVG"] = pd.to_numeric(df["API_SEATS_AVG"], errors="coerce").fillna(0.0)
    # Variance sign convention: point-in-time minus cycle-average.
    #   Positive => the current point-in-time billing method captures more
    #               revenue than a cycle-average method would (current wins).
    #   Negative => the cycle-average method would capture more (avg wins).
    df["VARIANCE_DOLLARS"] = df["API_AMT_POINT"] - df["API_AMT_AVG"]
    df["VARIANCE_PCT"] = df.apply(
        lambda r: (r["VARIANCE_DOLLARS"] / r["API_AMT_AVG"] * 100.0)
        if r["API_AMT_AVG"] else 0.0,
        axis=1,
    )

    total_row = {
        "SKU": "TOTAL",
        "API_SEATS_POINT": df["API_SEATS_POINT"].sum(),
        "API_SEATS_AVG": df["API_SEATS_AVG"].sum(),
        "API_AMT_POINT": df["API_AMT_POINT"].sum(),
        "API_AMT_AVG": df["API_AMT_AVG"].sum(),
    }
    total_row["VARIANCE_DOLLARS"] = total_row["API_AMT_POINT"] - total_row["API_AMT_AVG"]
    total_row["VARIANCE_PCT"] = (
        (total_row["VARIANCE_DOLLARS"] / total_row["API_AMT_AVG"] * 100.0)
        if total_row["API_AMT_AVG"] else 0.0
    )
    display = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)

    display["CW Billed $ @ API pt-in-time"] = display["API_AMT_POINT"].map(
        lambda v: "" if pd.isna(v) else fmt_money(float(v))
    )
    display["CW Billed $ @ API cycle avg"] = display["API_AMT_AVG"].map(
        lambda v: "" if pd.isna(v) else fmt_money(float(v))
    )
    display["Variance $"] = display["VARIANCE_DOLLARS"].map(
        lambda v: "" if pd.isna(v) else fmt_money(float(v))
    )
    display["Variance %"] = display["VARIANCE_PCT"].map(
        lambda v: "" if pd.isna(v) else f"{float(v):+.1f}%"
    )
    display["API Seats (pt-in-time)"] = display["API_SEATS_POINT"].map(
        lambda v: "" if pd.isna(v) else fmt_num(float(v))
    )
    display["API Seats (cycle avg)"] = display["API_SEATS_AVG"].map(
        lambda v: "" if pd.isna(v) else fmt_num(float(v))
    )

    st.dataframe(
        display[
            [
                "SKU",
                "API Seats (pt-in-time)",
                "API Seats (cycle avg)",
                "CW Billed $ @ API pt-in-time",
                "CW Billed $ @ API cycle avg",
                "Variance $",
                "Variance %",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        height=340,
    )


# ---------------------------------------------------------------------------
# SKU-level rate variance tables (row-level, not averaged)
# ---------------------------------------------------------------------------
# Two tables shown above the Profitability by SKU table on the vendor
# deep-dive tab. Grain is billing_month x partner x SKU -- exactly the grain
# already produced by THIRD_PARTY_RECON_OUTPUT_PROD, so every row we render
# comes straight from the pipeline aggregate with no re-aggregation.
#
# Vendor-side variance:
#   actual   = OUTPUT_PROD.VENDOR_UNIT_PRICE   (straight from vendor usage file)
#   expected = SKU_MAP_PROD.VENDOR_UNIT_PRICE  (contracted per-seat vendor rate)
#   Rows shown only when actual differs from expected. Positive delta = vendor
#   invoiced above the mapped rate.
#
# CW-side variance:
#   actual   = ZUORA_UNIT_PRICE if the Zuora line exists on this row,
#              else MARKETPLACE_UNIT_PRICE (falls back to marketplace when the
#              partner is billed exclusively through the CSP/marketplace flow)
#   expected = SKU_MAP_PROD.CW_UNIT_PRICE     (retail per-seat CW rate)
#   Rows shown only when the actual billed rate differs from retail.
#
# Both tables render the full set of variance rows with an in-app CSV
# download button, sorted descending by absolute $ impact.
# ---------------------------------------------------------------------------

# Float-noise tolerance: rates come from the vendor usage file as clean
# decimals (e.g. 2.05). Anything smaller than half a hundredth of a cent
# is treated as equality to prevent binary-float artifacts from creating
# spurious variance rows.
RATE_MATCH_TOLERANCE = 5e-5


@st.cache_data(ttl=DATA_TTL_SECONDS, show_spinner=False)
def _load_sku_map_rates(freshness_: str) -> pd.DataFrame:
    """Reference rates from THIRD_PARTY_RECON_SKU_MAP_PROD.

    The map carries multiple rows per (vendor, product) (also keyed by vendor
    SKU + CW SKU) but VENDOR_UNIT_PRICE, CW_UNIT_PRICE, and CONTRACT_COST_RATE
    are constant for a given (vendor, product) pair. We collapse by MIN so we
    get the exact literal source value (not a floating-point average of
    duplicates).

    We also surface CONTRACT_COST_RATE so vendors whose SKU map does not
    publish VENDOR_UNIT_PRICE directly (Auvik / Bitdefender / ESET / Webroot)
    can still be compared against a governed reference. The invoice-derived
    VENDOR_UNIT_PRICE lands on OUTPUT_PROD through the ingestion pipeline
    (invoice_rate_backfill.fill_missing_prices_dynamic: exact-month invoice
    rate, else carry-forward from the most recent prior month), so this table
    treats CONTRACT_COST_RATE as the ``should be`` fallback when the map
    lacks a per-SKU vendor unit price.
    """
    df = upper_cols(_try_query(
        f"""
        SELECT VENDOR, VENDOR_PRODUCT, VENDOR_UNIT_PRICE, CW_UNIT_PRICE, CONTRACT_COST_RATE
        FROM {SCHEMA}.THIRD_PARTY_RECON_SKU_MAP_PROD
        WHERE VENDOR_PRODUCT IS NOT NULL
        """,
        freshness_,
    ))
    if df.empty:
        return df
    for col in ("VENDOR_UNIT_PRICE", "CW_UNIT_PRICE", "CONTRACT_COST_RATE"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return (
        df.groupby(["VENDOR", "VENDOR_PRODUCT"], dropna=False)
        .agg(
            VENDOR_UNIT_PRICE=("VENDOR_UNIT_PRICE", "min"),
            CW_UNIT_PRICE=("CW_UNIT_PRICE", "min"),
            CONTRACT_COST_RATE=("CONTRACT_COST_RATE", "min"),
        )
        .reset_index()
    )


def _sku_reference_rates(vendor_name: str) -> pd.DataFrame:
    """Return the per-SKU reference-rate frame for a single vendor."""
    all_rates = _load_sku_map_rates(freshness)
    if all_rates.empty:
        return all_rates
    return all_rates[all_rates["VENDOR"].astype(str) == str(vendor_name)].reset_index(drop=True)


def _render_variance_table(
    frame: pd.DataFrame,
    reference_col: str,
    actual_col: str,
    reference_label: str,
    actual_label: str,
    quantity_col: str,
    quantity_label: str,
    direction: str,
    slice_name: str,
    period_label: str,
    download_stem: str,
    header: str,
    caption: str,
    extra_cols: list[tuple[str, str]] | None = None,
) -> None:
    """Row-level variance renderer -- full table + CSV download.

    ``frame`` must be at billing_month x partner x SKU grain and carry:
      * ``BILLING_MONTH``, ``PARTNER``, ``SKU``
      * ``quantity_col`` -- seat count (numeric)
      * ``actual_col``   -- actual unit price paid/billed at that row
      * ``reference_col`` -- expected unit price from the SKU map

    ``direction``:
      * ``"vendor_over"`` -- keep rows where actual > reference (vendor charged
        above map), sort by extra cost $ desc.
      * ``"cw_under"``    -- keep rows where actual < reference (CW billed below
        retail), sort by revenue shortfall $ desc.
      * ``"any"``         -- keep any row where actual != reference, sort by
        abs($ impact) desc.

    ``extra_cols`` -- optional extra display columns to surface between the
    quantity and the $-impact columns. List of (source_col, header_label).
    """
    st.markdown(f"### {header}")

    if frame.empty:
        st.markdown(
            '<div class="note">No matching rows for this vendor in the selected period.</div>',
            unsafe_allow_html=True,
        )
        return

    work = frame.copy()
    ref_vals = pd.to_numeric(work[reference_col], errors="coerce")
    actual_vals = pd.to_numeric(work[actual_col], errors="coerce")
    qty_vals = pd.to_numeric(work[quantity_col], errors="coerce").fillna(0.0)

    mask_actionable = ref_vals.notna() & actual_vals.notna() & (qty_vals > 0)
    if not mask_actionable.any():
        st.markdown(
            '<div class="note">No rows with both a mapped reference rate and non-zero seats.</div>',
            unsafe_allow_html=True,
        )
        return

    work = work.loc[mask_actionable].copy()
    ref_vals = ref_vals.loc[mask_actionable]
    actual_vals = actual_vals.loc[mask_actionable]
    qty_vals = qty_vals.loc[mask_actionable]

    delta_per_seat = actual_vals - ref_vals

    if direction == "vendor_over":
        keep = delta_per_seat > RATE_MATCH_TOLERANCE
        dollar_impact = delta_per_seat * qty_vals
        impact_label = "Extra cost $"
        summary_word = "extra cost"
    elif direction == "cw_under":
        keep = delta_per_seat < -RATE_MATCH_TOLERANCE
        dollar_impact = (-delta_per_seat) * qty_vals
        impact_label = "Revenue shortfall $"
        summary_word = "revenue shortfall"
    elif direction == "any":
        keep = delta_per_seat.abs() > RATE_MATCH_TOLERANCE
        dollar_impact = delta_per_seat.abs() * qty_vals
        impact_label = "$ Impact"
        summary_word = "impact"
    else:
        raise ValueError(f"unknown direction: {direction}")

    if not keep.any():
        if caption:
            st.markdown(
                f'<div class="note">{caption}</div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            f'<div class="note">{slice_name} billed every row at the mapped rate for {period_label}. '
            "No variance rows to review.</div>",
            unsafe_allow_html=True,
        )
        return

    work = work.loc[keep].copy()
    delta_per_seat = delta_per_seat.loc[keep]
    dollar_impact = dollar_impact.loc[keep]
    ref_vals = ref_vals.loc[keep]

    work["DELTA_PER_SEAT"] = delta_per_seat.values
    work["DOLLAR_IMPACT"] = dollar_impact.values
    ref_safe = ref_vals.where(ref_vals != 0)
    work["DELTA_PCT"] = (delta_per_seat / ref_safe).values

    work = work.sort_values(
        ["DOLLAR_IMPACT", quantity_col], ascending=[False, False]
    ).reset_index(drop=True)

    total_impact = float(work["DOLLAR_IMPACT"].sum())
    total_qty = float(pd.to_numeric(work[quantity_col], errors="coerce").fillna(0.0).sum())
    total_partners = int(work["PARTNER"].astype(str).nunique())
    total_rows = int(len(work))

    if caption:
        st.markdown(f'<div class="note">{caption}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="note">'
        f"{fmt_num(total_rows)} variance rows across {fmt_num(total_partners)} partners &middot; "
        f"{fmt_num(int(total_qty))} seats &middot; "
        f"{fmt_short_money(total_impact)} total {summary_word}"
        f"</div>",
        unsafe_allow_html=True,
    )

    display = work.copy()
    if pd.api.types.is_datetime64_any_dtype(display["BILLING_MONTH"]):
        display["Month"] = pd.to_datetime(display["BILLING_MONTH"]).dt.strftime("%b %Y")
    else:
        display["Month"] = display["BILLING_MONTH"].astype(str)
    display["Partner"] = display["PARTNER"].astype(str)
    display[reference_label] = display[reference_col].map(
        lambda v: "-" if pd.isna(v) else f"${float(v):.4f}"
    )
    display[actual_label] = display[actual_col].map(
        lambda v: "-" if pd.isna(v) else f"${float(v):.4f}"
    )
    display["Delta $/seat"] = display["DELTA_PER_SEAT"].map(
        lambda v: "-" if pd.isna(v) else f"${float(v):.4f}"
    )
    display["Delta %"] = display["DELTA_PCT"].map(
        lambda v: "-" if pd.isna(v) else f"{float(v) * 100:+.2f}%"
    )
    display[quantity_label] = pd.to_numeric(display[quantity_col], errors="coerce").fillna(0).map(
        lambda v: fmt_num(int(v))
    )
    display[impact_label] = display["DOLLAR_IMPACT"].map(
        lambda v: "-" if pd.isna(v) else fmt_short_money(float(v))
    )

    extras: list[str] = []
    if extra_cols:
        for src_col, hdr in extra_cols:
            if src_col in display.columns:
                display[hdr] = display[src_col].astype(str).replace({"nan": "-", "": "-"})
                extras.append(hdr)

    show_cols = [
        "Month",
        "Partner",
        "SKU",
        reference_label,
        actual_label,
        "Delta $/seat",
        "Delta %",
        quantity_label,
        *extras,
        impact_label,
    ]

    # Show all rows; use a comfortable fixed height so the internal scrollbar
    # handles long lists cleanly.
    visible_rows = min(total_rows, 25)
    df_height = 38 + 35 * max(visible_rows, 1)
    st.dataframe(
        display[show_cols],
        use_container_width=True,
        hide_index=True,
        height=df_height,
    )

    # ---- CSV download (full variance frame, unformatted numerics) ----
    csv_frame = work.copy()
    if pd.api.types.is_datetime64_any_dtype(csv_frame["BILLING_MONTH"]):
        csv_frame["BILLING_MONTH"] = pd.to_datetime(csv_frame["BILLING_MONTH"]).dt.strftime("%Y-%m-%d")
    csv_cols_order = [
        "BILLING_MONTH",
        "PARTNER",
        "SKU",
        reference_col,
        actual_col,
        "DELTA_PER_SEAT",
        "DELTA_PCT",
        quantity_col,
    ]
    if extra_cols:
        csv_cols_order.extend(src for src, _ in extra_cols if src in csv_frame.columns)
    csv_cols_order.append("DOLLAR_IMPACT")
    csv_frame = csv_frame[[c for c in csv_cols_order if c in csv_frame.columns]]
    safe_period = re.sub(r"[^A-Za-z0-9_-]+", "_", period_label).strip("_")
    safe_vendor = re.sub(r"[^A-Za-z0-9_-]+", "_", slice_name).strip("_")
    st.download_button(
        label=f"Download {slice_name} {download_stem} as CSV",
        data=csv_frame.to_csv(index=False).encode("utf-8"),
        file_name=f"{safe_vendor.lower()}_{download_stem}_{safe_period.lower() or 'all'}.csv",
        mime="text/csv",
        key=f"dl_{safe_vendor.lower()}_{download_stem}",
        help=f"Full row-level variance frame for {slice_name}.",
    )


def render_vendor_invoice_vs_contract_rate(slice_: VendorSlice) -> None:
    """Vendor-side rate variance.

    Grain: billing_month x partner x SKU (as stored in OUTPUT_PROD).
    Actual   = VENDOR_UNIT_PRICE from OUTPUT_PROD. For vendors that publish
               $/seat directly on the raw usage feed (Acronis / Proofpoint /
               KeepIT / SentinelOne / Exium) this is the vendor-invoiced
               rate. For vendors whose raw usage file has no unit price
               (Auvik / Bitdefender / ESET / Webroot) the ingestion pipeline
               populates it dynamically from THIRD_PARTY_RECON_VENDOR_INVOICES
               using the invoice for that billing month, falling back to the
               most recent prior month if the current month is not yet in.
    Expected = VENDOR_UNIT_PRICE from THIRD_PARTY_RECON_SKU_MAP_PROD when
               present, otherwise CONTRACT_COST_RATE. Only rows where actual
               exceeds the reference (vendor billed above the mapped rate)
               are shown, sorted by extra cost $.
    """
    detail = slice_.detail
    if detail.empty or "VENDOR_PRODUCT" not in detail.columns:
        return
    ref = _sku_reference_rates(slice_.name)
    if ref.empty:
        return
    # Prefer per-SKU VENDOR_UNIT_PRICE from the map; fall back to the governed
    # CONTRACT_COST_RATE so vendors without a map-published unit price still
    # get a reference against the invoice-derived actual.
    ref = ref.copy()
    ref_vup = ref["VENDOR_UNIT_PRICE"] if "VENDOR_UNIT_PRICE" in ref.columns else pd.Series([pd.NA] * len(ref))
    ref_ccr = ref["CONTRACT_COST_RATE"] if "CONTRACT_COST_RATE" in ref.columns else pd.Series([pd.NA] * len(ref))
    ref["MAP_REFERENCE_RATE"] = pd.to_numeric(ref_vup, errors="coerce").combine_first(
        pd.to_numeric(ref_ccr, errors="coerce")
    )
    if not ref["MAP_REFERENCE_RATE"].notna().any():
        return

    d = detail[[
        "BILLING_MONTH", "VENDOR_PARTNER_NAME", "VENDOR_PRODUCT",
        "VENDOR_QUANTITY", "VENDOR_UNIT_PRICE", "VENDOR_AMOUNT",
    ]].copy()
    d["PARTNER"] = detail.get(
        "PARTNER_DISPLAY_NAME",
        d["VENDOR_PARTNER_NAME"],
    ).fillna("(unknown)").astype(str)
    d["SKU"] = detail.get(
        "PRODUCT_DISPLAY",
        d["VENDOR_PRODUCT"],
    ).fillna("(unmapped)").astype(str)
    d["SKU_JOIN"] = d["VENDOR_PRODUCT"].fillna("(unmapped)").astype(str)

    ref_slim = ref[["VENDOR_PRODUCT", "MAP_REFERENCE_RATE"]].rename(
        columns={"VENDOR_PRODUCT": "SKU_JOIN"}
    )
    merged = d.merge(ref_slim, on="SKU_JOIN", how="left")

    _render_variance_table(
        frame=merged,
        reference_col="MAP_REFERENCE_RATE",
        actual_col="VENDOR_UNIT_PRICE",
        reference_label="Contract $/seat",
        actual_label="Vendor invoiced $/seat",
        quantity_col="VENDOR_QUANTITY",
        quantity_label="Vendor seats",
        direction="vendor_over",
        slice_name=slice_.name,
        period_label=period_label,
        download_stem="vendor_rate_variance",
        header=f"{slice_.name} vendor invoice vs contracted rate",
        caption="",
    )


def render_cw_retail_vs_billed_rate(slice_: VendorSlice) -> None:
    """CW-side rate variance.

    Grain: billing_month x partner x SKU (as stored in OUTPUT_PROD).
    Actual   = ZUORA_UNIT_PRICE when the Zuora line exists on the row,
               otherwise MARKETPLACE_UNIT_PRICE (falls back to marketplace
               when the partner is billed exclusively via the CSP flow).
    Expected = CW_UNIT_PRICE from THIRD_PARTY_RECON_SKU_MAP_PROD (retail).
    Only rows where actual < expected (CW billed below retail) are shown,
    sorted by revenue shortfall $.
    """
    detail = slice_.detail
    if detail.empty or "VENDOR_PRODUCT" not in detail.columns:
        return
    ref = _sku_reference_rates(slice_.name)
    if ref.empty or "CW_UNIT_PRICE" not in ref.columns:
        return
    if not ref["CW_UNIT_PRICE"].notna().any():
        return

    cols = [
        "BILLING_MONTH", "VENDOR_PARTNER_NAME", "VENDOR_PRODUCT",
        "ZUORA_QUANTITY", "ZUORA_UNIT_PRICE",
        "MARKETPLACE_QUANTITY", "MARKETPLACE_UNIT_PRICE",
    ]
    missing = [c for c in cols if c not in detail.columns]
    if missing:
        return
    d = detail[cols].copy()
    d["PARTNER"] = detail.get(
        "PARTNER_DISPLAY_NAME",
        d["VENDOR_PARTNER_NAME"],
    ).fillna("(unknown)").astype(str)
    d["SKU"] = detail.get(
        "PRODUCT_DISPLAY",
        d["VENDOR_PRODUCT"],
    ).fillna("(unmapped)").astype(str)
    d["SKU_JOIN"] = d["VENDOR_PRODUCT"].fillna("(unmapped)").astype(str)

    zqty = pd.to_numeric(d["ZUORA_QUANTITY"], errors="coerce").fillna(0.0)
    zprc = pd.to_numeric(d["ZUORA_UNIT_PRICE"], errors="coerce").fillna(0.0)
    mqty = pd.to_numeric(d["MARKETPLACE_QUANTITY"], errors="coerce").fillna(0.0)
    mprc = pd.to_numeric(d["MARKETPLACE_UNIT_PRICE"], errors="coerce").fillna(0.0)

    use_zuora = (zqty > 0) & (zprc > 0)
    d["ACTUAL_BILLED_QTY"] = np.where(use_zuora, zqty, mqty)
    d["ACTUAL_BILLED_UNIT_PRICE"] = np.where(use_zuora, zprc, mprc)
    d["BILLING_SOURCE"] = np.where(
        use_zuora, "Zuora",
        np.where((mqty > 0) & (mprc > 0), "Marketplace", "-"),
    )

    ref_slim = ref[["VENDOR_PRODUCT", "CW_UNIT_PRICE"]].rename(
        columns={
            "VENDOR_PRODUCT": "SKU_JOIN",
            "CW_UNIT_PRICE": "MAP_CW_UNIT_PRICE",
        }
    )
    merged = d.merge(ref_slim, on="SKU_JOIN", how="left")

    _render_variance_table(
        frame=merged,
        reference_col="MAP_CW_UNIT_PRICE",
        actual_col="ACTUAL_BILLED_UNIT_PRICE",
        reference_label="CW retail $/seat",
        actual_label="CW billed $/seat",
        quantity_col="ACTUAL_BILLED_QTY",
        quantity_label="CW billed seats",
        direction="cw_under",
        slice_name=slice_.name,
        period_label=period_label,
        download_stem="cw_rate_variance",
        header=f"{slice_.name} CW billing vs retail rate",
        caption="",
        extra_cols=[("BILLING_SOURCE", "Source")],
    )


# ---------------------------------------------------------------------------
# Vendor Rate Audit — SentinelOne-invoiced unit price vs governed contract rate
# ---------------------------------------------------------------------------

def render_vendor_sku_profitability(slice_: VendorSlice) -> None:
    """Per-SKU profitability breakout for the deep-dive tab.

    Groups the vendor's detail by product/SKU and shows:
      * revenue, cost, margin $/%, and share of vendor revenue per SKU
      * SKU-mismatch leakage $ per SKU (rows flagged SKU_MISMATCH_BILLING_ON_OTHER_SKU)
    Purpose: when a vendor's blended margin moves, this view answers
    "is it mix shift (share moving between SKUs) or rate movement (margin %
    changing within a SKU)?"

    Prefer PRODUCT_DISPLAY when present so family rollups stay consistent
    with the queue and portfolio tables. Fall back to VENDOR_PRODUCT for
    pre-refresh data or raw audit slices.
    """
    detail = slice_.detail
    if detail.empty:
        return
    sku_col = next(
        (c for c in ("PRODUCT_DISPLAY", "VENDOR_PRODUCT") if c in detail.columns),
        None,
    )
    if sku_col is None:
        return

    st.markdown(f"### {slice_.name} profitability by SKU")
    _det = detail.copy()
    _det["_SKU"] = _det[sku_col].fillna("(unmapped)").astype(str)
    _det["_REV"] = pd.to_numeric(
        _det.get("TOTAL_BILLING_AMOUNT"), errors="coerce"
    ).fillna(0.0)
    _det["_COST"] = pd.to_numeric(
        _det.get("VENDOR_AMOUNT"), errors="coerce"
    ).fillna(0.0)
    _det["_FLAG"] = _det.get(
        "OUTCOME_FLAG", pd.Series("", index=_det.index)
    ).astype(str)
    _det["_MM_LEAK"] = pd.to_numeric(
        _det.get("AMOUNT_DELTA"), errors="coerce"
    ).fillna(0.0).abs().where(
        _det["_FLAG"] == "SKU_MISMATCH_BILLING_ON_OTHER_SKU", 0.0
    )

    sku_agg = (
        _det.groupby("_SKU", dropna=False)
        .agg(
            Revenue=("_REV", "sum"),
            Cost=("_COST", "sum"),
            Mismatch_Leakage=("_MM_LEAK", "sum"),
            Rows=("_SKU", "size"),
        )
        .reset_index()
        .rename(columns={"_SKU": "SKU", "Mismatch_Leakage": "SKU Mismatch $"})
    )
    sku_agg["Margin $"] = sku_agg["Revenue"] - sku_agg["Cost"]
    _total_rev = float(sku_agg["Revenue"].sum())
    sku_agg["Revenue Share"] = (
        sku_agg["Revenue"] / _total_rev if _total_rev else 0.0
    )
    sku_agg["Margin %"] = sku_agg.apply(
        lambda r: (r["Margin $"] / r["Revenue"]) if r["Revenue"] else 0.0,
        axis=1,
    )
    sku_agg = sku_agg.sort_values("Revenue", ascending=False).reset_index(drop=True)

    if sku_agg.empty:
        st.markdown(
            '<div class="note">No SKU-level revenue rows for this vendor in the selected period.</div>',
            unsafe_allow_html=True,
        )
        return

    _sku_display = sku_agg.copy()
    _sku_display["Revenue"] = _sku_display["Revenue"].map(fmt_short_money)
    _sku_display["Cost"] = _sku_display["Cost"].map(fmt_short_money)
    _sku_display["Margin $"] = _sku_display["Margin $"].map(fmt_short_money)
    _sku_display["Margin %"] = _sku_display["Margin %"].map(lambda v: f"{v * 100:.1f}%")
    _sku_display["Revenue Share"] = _sku_display["Revenue Share"].map(
        lambda v: f"{v * 100:.1f}%"
    )
    _sku_display["SKU Mismatch $"] = _sku_display["SKU Mismatch $"].map(fmt_short_money)
    _sku_display["Rows"] = _sku_display["Rows"].map(fmt_num)
    st.dataframe(
        _sku_display[
            [
                "SKU",
                "Revenue",
                "Revenue Share",
                "Cost",
                "Margin $",
                "Margin %",
                "SKU Mismatch $",
                "Rows",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_vendor_rate_audit(slice_: VendorSlice) -> None:
    """Compare what the vendor invoiced CW per seat against the governed rate.

    Buckets each recon row as OVER_CONTRACT / UNDER_CONTRACT / EVEN and reports
    prevalence and dollar impact. Requires VENDOR_VS_CONTRACT_FLAG from the
    pipeline (added 2026-07-31).
    """
    detail = slice_.detail
    if detail.empty or "VENDOR_VS_CONTRACT_FLAG" not in detail.columns:
        return

    st.markdown("### Contracted Rate Variance Analysis")
    st.caption(
        f"**Upstream vendor-invoice question:** Where {slice_.name} invoiced CW at a unit "
        "price that differs from the contracted rate — above contract = pursue vendor credit, "
        "below contract = savings to reconcile / verify contract terms."
    )

    rate_df = detail.copy()
    rate_df["_impact"] = pd.to_numeric(
        rate_df.get("VENDOR_VS_CONTRACT_DOLLAR_IMPACT"), errors="coerce"
    ).fillna(0.0)

    exposed = rate_df[rate_df["VENDOR_VS_CONTRACT_FLAG"].isin(["OVER_CONTRACT", "UNDER_CONTRACT"])].copy()
    if exposed.empty:
        st.markdown(
            f'<div class="note">{html.escape(slice_.name)} invoiced CW at the governed contract rate on every rated row this period.</div>',
            unsafe_allow_html=True,
        )
        return

    # No $300/3% noise gate here — the OVER_CONTRACT / UNDER_CONTRACT buckets
    # are already gated upstream by the SQL EVEN band (±$0.01/seat and ±1%),
    # so any row that reaches this point represents a real vendor-invoice
    # deviation from contract regardless of dollar magnitude.
    exposed["_abs"] = exposed["_impact"].abs()
    exposed = exposed.sort_values("_abs", ascending=False)
    exposed["SF_ID"] = _salesforce_account_links(exposed)
    exposed["BILLING_MONTH"] = pd.to_datetime(
        exposed["BILLING_MONTH"], errors="coerce"
    ).dt.strftime("%Y-%m")

    keep_cols = [c for c in [
        "SF_ID", "BILLING_MONTH", "PARTNER_DISPLAY_NAME", "PRODUCT_DISPLAY",
        "CONTRACT_COST_RATE", "VENDOR_UNIT_PRICE", "VENDOR_VS_CONTRACT_PCT",
        "VENDOR_QUANTITY", "VENDOR_VS_CONTRACT_DOLLAR_IMPACT",
        "VENDOR_VS_CONTRACT_FLAG",
    ] if c in exposed.columns]
    disp = exposed[keep_cols].rename(columns={
        "SF_ID": "Salesforce ID",
        "BILLING_MONTH": "Month",
        "PARTNER_DISPLAY_NAME": "Partner",
        "PRODUCT_DISPLAY": "Product",
        "CONTRACT_COST_RATE": "Contract $/seat",
        "VENDOR_UNIT_PRICE": "Vendor $/seat",
        "VENDOR_VS_CONTRACT_PCT": "Vendor vs Contract",
        "VENDOR_QUANTITY": "Vendor Seats",
        "VENDOR_VS_CONTRACT_DOLLAR_IMPACT": "Rate impact ($)",
        "VENDOR_VS_CONTRACT_FLAG": "Bucket",
    })
    for c in ("Contract $/seat", "Vendor $/seat"):
        if c in disp.columns:
            disp[c] = disp[c].map(lambda x: f"${float(x):.4f}" if pd.notna(x) else "-")
    if "Vendor vs Contract" in disp.columns:
        disp["Vendor vs Contract"] = disp["Vendor vs Contract"].map(
            lambda x: f"{float(x) * 100:+.2f}%" if pd.notna(x) else "-"
        )
    if "Vendor Seats" in disp.columns:
        disp["Vendor Seats"] = disp["Vendor Seats"].map(fmt_num)
    if "Rate impact ($)" in disp.columns:
        disp["Rate impact ($)"] = disp["Rate impact ($)"].map(fmt_short_money)
    # Move Bucket (outcome flag) to the rightmost column for readability.
    if "Bucket" in disp.columns:
        _cols_order = [c for c in disp.columns if c != "Bucket"] + ["Bucket"]
        disp = disp[_cols_order]

    st.dataframe(
        disp,
        column_config={"Salesforce ID": _salesforce_link_column()},
        use_container_width=True,
        hide_index=True,
    )


# ---- Deep-dive helpers ---------------------------------------------------

def _build_negative_margin_frame(slice_: VendorSlice) -> pd.DataFrame:
    """Return the display-ready Negative Margin table for this slice.
    Applies the $300 / 3% noise gate. Empty frame => no material rows."""
    detail = slice_.detail
    if detail.empty or not has_contract_price(detail):
        return pd.DataFrame()
    below = detail[_contract_mask(detail, "BELOW_COST_DISCOUNT", material_only=True)].copy()
    if below.empty:
        return pd.DataFrame()
    if "TOTAL_BILLING_UNIT_PRICE" in below.columns:
        below["BILLED_UNIT_PRICE"] = below["TOTAL_BILLING_UNIT_PRICE"]
    else:
        qty = below["TOTAL_BILLING_QUANTITY"].replace(0, pd.NA)
        below["BILLED_UNIT_PRICE"] = below["TOTAL_BILLING_AMOUNT"] / qty
    _cost = below["CONTRACT_COST_RATE"].astype(float)
    _billed = below["BILLED_UNIT_PRICE"].astype(float)
    below["PCT_DISCOUNT"] = ((_cost - _billed) / _cost.where(_cost != 0)) * 100.0
    below["EXCEPTION_TYPE"] = slice_.detail_with_categories.loc[
        below.index, "EXCEPTION_TYPE"
    ].values
    # No $300/3% noise gate here — SQL-side MATERIAL_BELOW_COST_FLAG already
    # filters immaterial per-seat/per-month rounding. The extra dollar/percent
    # gate is only relevant for seat-variance categories.
    if below.empty:
        return pd.DataFrame()
    below = below.sort_values("BILLING_VS_COST_DOLLAR_IMPACT")
    below["SF_ID"] = _salesforce_account_links(below)
    below["BILLING_MONTH"] = pd.to_datetime(
        below["BILLING_MONTH"], errors="coerce"
    ).dt.strftime("%Y-%m")
    _cols = [c for c in [
        "SF_ID", "BILLING_MONTH", "PARTNER_DISPLAY_NAME", "PRODUCT_DISPLAY",
        "CONTRACT_COST_RATE", "BILLED_UNIT_PRICE", "PCT_DISCOUNT",
        "TOTAL_BILLING_QUANTITY", "BILLING_VS_COST_DOLLAR_IMPACT", "EXCEPTION_TYPE",
    ] if c in below.columns]
    disp = below[_cols].rename(columns={
        "SF_ID": "Salesforce ID",
        "BILLING_MONTH": "Month",
        "PARTNER_DISPLAY_NAME": "Partner",
        "PRODUCT_DISPLAY": "Product",
        "CONTRACT_COST_RATE": "Contract $/seat",
        "BILLED_UNIT_PRICE": "Billed $/seat",
        "PCT_DISCOUNT": "% Discount",
        "TOTAL_BILLING_QUANTITY": "CW Billed",
        "BILLING_VS_COST_DOLLAR_IMPACT": "Monthly loss ($)",
        "EXCEPTION_TYPE": "Billing exception type",
    })
    for c in ("Contract $/seat", "Billed $/seat"):
        if c in disp.columns:
            disp[c] = disp[c].map(lambda x: f"${float(x):.4f}" if pd.notna(x) else "-")
    if "% Discount" in disp.columns:
        disp["% Discount"] = disp["% Discount"].map(
            lambda x: f"{float(x):+.1f}%" if pd.notna(x) else "-"
        )
    if "CW Billed" in disp.columns:
        disp["CW Billed"] = disp["CW Billed"].map(fmt_num)
    if "Monthly loss ($)" in disp.columns:
        disp["Monthly loss ($)"] = disp["Monthly loss ($)"].map(fmt_short_money)
    # Move outcome-flag column (Billing exception type) to the rightmost position.
    if "Billing exception type" in disp.columns:
        _order = [c for c in disp.columns if c != "Billing exception type"] + ["Billing exception type"]
        disp = disp[_order]
    return disp


# ---- Tab: Recon Team Queue -----------------------------------------------
#
# Priority queue + inline case tracker for the reconciliation team. Each row
# is one (vendor, account, product, month, exception) case ranked by dollar
# impact. Marketplace timing rows are excluded (they self-resolve). The team
# can update Status / Assignee / Notes inline — edits persist in
# st.session_state["recon_team_tracker"] keyed by a stable Case ID hash and
# survive filter changes. A CSV export snapshot is available at the bottom.
# --------------------------------------------------------------------------

RECON_TEAM_STATUS_OPTIONS = ["New", "In Progress", "Resolved", "No Action"]


def _recon_team_case_id(vendor: str, sf_id: str, product: str, month: Any, exc_type: str) -> str:
    _m = pd.to_datetime(month, errors="coerce")
    _m_str = _m.strftime("%Y-%m") if pd.notna(_m) else ""
    return "|".join([str(vendor or ""), str(sf_id or ""), str(product or ""), _m_str, str(exc_type or "")])


def _full_reconciliation_export(detail: pd.DataFrame) -> pd.DataFrame:
    """Return every OUTPUT_PROD row in the recon team's exact Excel layout."""
    if detail.empty:
        return pd.DataFrame()

    def source(*names: str) -> pd.Series:
        result: pd.Series | None = None
        for name in names:
            if name in detail.columns:
                candidate = detail[name].replace(r"^\s*$", pd.NA, regex=True)
                result = candidate if result is None else result.combine_first(candidate)
        return result if result is not None else pd.Series("", index=detail.index)

    export = pd.DataFrame({
        "Vendor": source("VENDOR"),
        "Billing Month": source("BILLING_MONTH"),
        "Invoice ID": source("INV_ID"),
        "Vendor Partner Name": source("VENDOR_PARTNER_NAME"),
        "SF ID": source("SF_ID"),
        "CMS ID": source("CMS_ID"),
        "CW Partner Name": source("CW_PARTNER_NAME", "PARTNER_DISPLAY_NAME"),
        "CW Parent Company": source("CW_PARENT_COMPANY", "PARTNER_PARENT_COMPANY"),
        "Vendor Product SKU": source("VENDOR_PRODUCT_SKU", "VENDOR_PRODUCT"),
        # Actual source-billed SKU(s) assigned to this reconciliation row.
        # Do not expose CW_SKUS here: that is the full governed map candidate
        # list, not necessarily the SKU present on the matched invoice.
        "CW SKU": source("MATCHED_INVOICE_SKU", "ZUORA_SKUS", "MARKETPLACE_SKUS"),
        "Vendor Qty": source("VENDOR_QUANTITY"),
        "Vendor Unit Price": source("VENDOR_UNIT_PRICE"),
        "Vendor Amount": source("VENDOR_AMOUNT"),
        "API Qty": source("API_QUANTITY"),
        "Avg API Qty": source("AVG_API_QUANTITY"),
        "Zuora Qty": source("ZUORA_QUANTITY"),
        "Zuora Unit Price": source("ZUORA_UNIT_PRICE"),
        "Zuora Amount": source("ZUORA_AMOUNT"),
        "MP Qty": source("MARKETPLACE_QUANTITY"),
        "MP Unit Price": source("MARKETPLACE_UNIT_PRICE"),
        "MP Amount": source("MARKETPLACE_AMOUNT"),
        "CW Total Billing Qty": source("TOTAL_BILLING_QUANTITY"),
        "CW Total Billing Amount": source("TOTAL_BILLING_AMOUNT"),
        "Qty Delta": source("QTY_DELTA"),
        "Amount Delta": source("AMOUNT_DELTA"),
        "Outcome Flag": source("OUTCOME_FLAG"),
        "Investigation Reason": source("INVESTIGATION_REASON"),
        "SF Account URL": source("SALESFORCE_ACCOUNT_URL"),
        "Case ID": source("CASE_ID"),
    })
    export["Billing Month"] = pd.to_datetime(
        export["Billing Month"], errors="coerce"
    ).dt.date
    return export.sort_values(
        ["Vendor", "Billing Month", "SF ID", "Vendor Product SKU"],
        kind="stable",
    ).reset_index(drop=True)


@st.cache_data(ttl=DATA_TTL_SECONDS, show_spinner=False)
def _full_reconciliation_export_cached(
    freshness_key: str, schema_version: str
) -> pd.DataFrame:
    """Cache the large all-row export without hashing a DataFrame argument."""
    all_output_detail = _load_all_recon_frames(freshness_key, schema_version)[1]
    return _full_reconciliation_export(all_output_detail)


with tab_team:
    st.markdown(f"### Recon Team Priority Queue \u2014 {period_label}")

    if portfolio_detail.empty or "EXCEPTION_TYPE" not in portfolio_detail.columns:
        st.markdown(
            '<div class="note">No exceptions in the current selection.</div>',
            unsafe_allow_html=True,
        )
    else:
        # Base cohort: everything except Marketplace timing (self-resolves).
        queue_source = portfolio_detail[
            portfolio_detail["EXCEPTION_TYPE"] != "Marketplace Billing Delay"
        ].copy()

        # Keep any row that is either (a) already flagged as a non-Clear
        # exception, or (b) surfaced by the combined "Vendor Billing/Usage >
        # CW Billing/Usage" mask (CLEAR-outcome rows where CW is losing
        # margin or where vendor-reported usage exceeds CW-billed seats).
        _et = queue_source["EXCEPTION_TYPE"].astype(str)
        _keep_row = _et.ne("Clear") & _et.ne("") & queue_source["EXCEPTION_TYPE"].notna()
        _vmask = combined_vendor_over_mask(queue_source)
        queue_source = queue_source[_keep_row | _vmask].copy()
        # Re-compute the mask on the filtered frame so index alignment is safe.
        queue_source["_VIRTUAL"] = combined_vendor_over_mask(queue_source).values

        if queue_source.empty:
            st.markdown(
                '<div class="note">No actionable exceptions in the current selection.</div>',
                unsafe_allow_html=True,
            )
        else:
            # Vectorized replacements for the old row-by-row .apply calls.
            # Previously the queue construction called .apply(axis=1) three
            # times (label resolution, case id, resolve flag) — each one an
            # O(n) Python loop that dominated tab-switch latency once the
            # queue exceeded a few thousand rows. Fully vectorized now.
            _et_col = queue_source["EXCEPTION_TYPE"].astype(str)
            _virtual_col = queue_source["_VIRTUAL"].fillna(False).astype(bool)
            _needs_virtual_label = _virtual_col & _et_col.isin(["", "Clear", "nan", "None"])
            queue_source["Exception Type"] = _et_col.where(
                ~_needs_virtual_label, VENDOR_BILLING_OVER_CW_LABEL
            )
            queue_source["Est $ Impact"] = (
                pd.to_numeric(queue_source.get("AMOUNT_DELTA"), errors="coerce")
                .fillna(0.0)
                .abs()
            )
            queue_source["Seat Variance"] = (
                pd.to_numeric(queue_source.get("ABS_QTY_DELTA"), errors="coerce").fillna(0.0)
            )
            # Vectorized Case ID (vendor|sf_id|product|YYYY-MM|exception).
            # Vector string concat with pd.Series.str.cat is ~50-100x faster
            # than .apply for tens of thousands of rows.
            #
            # 2026-08-31: OUTPUT_PROD now emits a canonical CASE_ID column
            # keyed on PRODUCT_DISPLAY (SKU_MATCH_GROUP-driven family, not
            # raw VENDOR_PRODUCT). Prefer that when present so pipe-SKU
            # variants collapse into one queue row per real case. Fall back
            # to the vectorized rebuild for pre-refresh cached data.
            _existing_case = queue_source.get("CASE_ID")
            if (
                _existing_case is not None
                and _existing_case.astype(str).str.strip().replace({"": None}).notna().any()
            ):
                queue_source["Case ID"] = _existing_case.fillna("").astype(str)
            else:
                _vendor_str = queue_source.get(
                    "_VENDOR", pd.Series("", index=queue_source.index)
                ).fillna("").astype(str)
                _sf_str = queue_source.get(
                    "SF_ID", pd.Series("", index=queue_source.index)
                ).fillna("").astype(str)
                # Prefer PRODUCT_DISPLAY for Case ID keying; fall back to
                # VENDOR_PRODUCT when the display column is not yet populated.
                _prod_series = queue_source.get(
                    "PRODUCT_DISPLAY", pd.Series("", index=queue_source.index)
                ).fillna("").astype(str)
                if not _prod_series.str.strip().replace({"": None}).notna().any():
                    _prod_series = queue_source.get(
                        "VENDOR_PRODUCT", pd.Series("", index=queue_source.index)
                    ).fillna("").astype(str)
                _prod_str = _prod_series
                _month_ts = pd.to_datetime(
                    queue_source.get("BILLING_MONTH"), errors="coerce"
                )
                _month_str = _month_ts.dt.strftime("%Y-%m").fillna("")
                _exc_str = queue_source["Exception Type"].fillna("").astype(str)
                queue_source["Case ID"] = (
                    _vendor_str + "|" + _sf_str + "|" + _prod_str + "|"
                    + _month_str + "|" + _exc_str
                )
            # Collapse to one row per Case ID (multiple pipeline rows can
            # share the same case when SKUs roll up to the same product).
            # OUTCOME_FLAG: keep the pipeline's own reason code (first non-
            # empty value per case, or "VENDOR_BILLING_OVER_CW" for rows that
            # only surface via the virtual mask) so the recon team can see at
            # a glance why each case is flagged.
            #
            # Vectorized flag resolution — the old .groupby().apply() ran a
            # Python function per Case ID (thousands of calls). Now we
            # precompute per-row "effective flag" and use groupby.first()
            # on rows sorted so non-Clear real flags win.
            _raw_flag = queue_source.get(
                "OUTCOME_FLAG", pd.Series("", index=queue_source.index)
            ).fillna("").astype(str)
            _is_real_flag = ~_raw_flag.isin(["", "CLEAR", "MATCHED", "nan", "None"])
            _effective_flag = _raw_flag.where(
                _is_real_flag,
                pd.Series(
                    ["VENDOR_BILLING_OVER_CW (virtual)"] * len(queue_source),
                    index=queue_source.index,
                ).where(_virtual_col, _raw_flag),
            )
            # Sort so real flags come before virtual/blank inside each Case
            # ID group; groupby.first() then picks the correct one in a
            # single vectorized pass.
            _sort_key = (~_is_real_flag).astype(int)
            _flag_frame = pd.DataFrame({
                "Case ID": queue_source["Case ID"].values,
                "_flag": _effective_flag.values,
                "_prio": _sort_key.values,
            }).sort_values(["Case ID", "_prio"])
            _flag_by_case = (
                _flag_frame.groupby("Case ID", dropna=False)["_flag"]
                .first()
                .rename("Outcome Flag")
            )
            # 2026-08-31 board-ready: prefer canonical *_DISPLAY columns
            # for the Partner and Product fields the recon team sees.
            # When they're absent (pre-refresh cached data) fall back to
            # the raw pipe-delimited VENDOR_* columns so nothing breaks.
            _has_partner_display = (
                "PARTNER_DISPLAY_NAME" in queue_source.columns
                and queue_source["PARTNER_DISPLAY_NAME"]
                    .fillna("").astype(str).str.strip().replace({"": None}).notna().any()
            )
            _has_product_display = (
                "PRODUCT_DISPLAY" in queue_source.columns
                and queue_source["PRODUCT_DISPLAY"]
                    .fillna("").astype(str).str.strip().replace({"": None}).notna().any()
            )
            _partner_col = "PARTNER_DISPLAY_NAME" if _has_partner_display else "VENDOR_PARTNER_NAME"
            _product_col = "PRODUCT_DISPLAY" if _has_product_display else "VENDOR_PRODUCT"
            _group_agg = {
                "_VENDOR": "first",
                "SF_ID": "first",
                _partner_col: "first",
                _product_col: "first",
                "BILLING_MONTH": "first",
                "Exception Type": "first",
                "Est $ Impact": "sum",
                "Seat Variance": "sum",
            }
            if "SALESFORCE_ACCOUNT_URL" in queue_source.columns:
                _group_agg["SALESFORCE_ACCOUNT_URL"] = "first"
            grouped = (
                queue_source.groupby("Case ID", dropna=False)
                .agg(_group_agg)
                .reset_index()
            )
            grouped = grouped.merge(_flag_by_case.reset_index(), on="Case ID", how="left")
            grouped = grouped.sort_values("Est $ Impact", ascending=False).reset_index(drop=True)
            grouped["Priority"] = grouped.index + 1
            grouped["Billing Month"] = pd.to_datetime(
                grouped["BILLING_MONTH"], errors="coerce"
            ).dt.strftime("%Y-%m")
            grouped["Account"] = _salesforce_account_links(grouped)

            # Merge in persisted tracker state (session-scoped, keyed on Case ID
            # so entries survive vendor/month filter changes).
            tracker: dict = st.session_state.setdefault("recon_team_tracker", {})
            # Migration: rename any legacy "Won't Fix" status to "No Action".
            for _cid, _entry in list(tracker.items()):
                if isinstance(_entry, dict) and _entry.get("status") == "Won't Fix":
                    _entry["status"] = "No Action"
            # Vectorized tracker lookup — build three dicts and .map() once
            # each rather than a lambda per row (previously did 3 × N dict
            # lookups + 3 × N sub-lookups every rerun).
            _status_map = {cid: e.get("status", "New") for cid, e in tracker.items() if isinstance(e, dict)}
            _assign_map = {cid: e.get("assignee", "") for cid, e in tracker.items() if isinstance(e, dict)}
            _notes_map = {cid: e.get("notes", "") for cid, e in tracker.items() if isinstance(e, dict)}
            grouped["Status"] = grouped["Case ID"].map(_status_map).fillna("New")
            grouped["Assignee"] = grouped["Case ID"].map(_assign_map).fillna("")
            grouped["Notes"] = grouped["Case ID"].map(_notes_map).fillna("")

            display_df = grouped.rename(columns={
                "_VENDOR": "Vendor",
                _partner_col: "Partner",
                _product_col: "Product",
            })[[
                "Priority", "Vendor", "Account", "Partner", "Product",
                "Billing Month", "Exception Type", "Outcome Flag",
                "Est $ Impact", "Seat Variance",
                "Status", "Assignee", "Notes", "Case ID",
            ]]

            # Header strip: cases + open Est $ Impact + status mix.
            total_cases = len(display_df)
            open_cases = int((display_df["Status"] == "New").sum())
            in_progress = int((display_df["Status"] == "In Progress").sum())
            resolved = int((display_df["Status"] == "Resolved").sum())
            no_action = int((display_df["Status"] == "No Action").sum())
            # Simple + honest: sum Est $ Impact across every open case in the
            # queue above. This matches the table exactly (no hidden filters)
            # so the tile is easy to trace back to the rows that drive it.
            _open_mask = display_df["Status"].isin(["New", "In Progress"])
            open_dollars = float(display_df.loc[_open_mask, "Est $ Impact"].sum())

            st.markdown(
                '<div class="strip">'
                + strip_tile(
                    fmt_num(total_cases), "Total Cases",
                    hint="Actionable exception cases in the current selection (Marketplace timing excluded)",
                )
                + strip_tile(
                    fmt_short_money(open_dollars), "Est $ Impact (open cases)",
                    hint="Sum of the Est $ Impact column across every New or In Progress case in the queue above — the addressable dollar amount if every open case is resolved in CW's favor. Est $ Impact per row = |AMOUNT_DELTA| (absolute variance between vendor and CW billing for that row). This is a working total for the recon team, not the Revenue Leakage KPI — use the Monthly Reconciliation Action Queue for the Finance Queue number",
                )
                + strip_tile(
                    f"{open_cases} / {in_progress} / {resolved}",
                    "New / In Progress / Resolved",
                    hint="Case count by working status",
                )
                + strip_tile(
                    fmt_num(no_action), "No Action",
                    hint="Cases the team reviewed and marked as not requiring further work",
                )
                + "</div>",
                unsafe_allow_html=True,
            )

            st.caption(
                "Edit Status, Assignee, and Notes inline. Priority is fixed at "
                "$ impact rank so the top of the queue is always the highest-value work."
            )
            edited = st.data_editor(
                display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                column_config={
                    "Priority": st.column_config.NumberColumn("Priority", format="%d", disabled=True),
                    "Vendor": st.column_config.TextColumn("Vendor", disabled=True),
                    "Account": _salesforce_link_column(),
                    "Partner": st.column_config.TextColumn("Partner", disabled=True),
                    "Product": st.column_config.TextColumn("Product", disabled=True),
                    "Billing Month": st.column_config.TextColumn("Billing Month", disabled=True),
                    "Exception Type": st.column_config.TextColumn("Exception Type", disabled=True),
                    "Outcome Flag": st.column_config.TextColumn(
                        "Outcome Flag",
                        disabled=True,
                        help="Raw pipeline reason code (OUTCOME_FLAG) for this case. Shows exactly what the recon pipeline detected — e.g. KNOWN_GOOD_MAPPING_MISSING_CURRENT_BILLING, VENDOR_OVER_BILLING, DUPLICATE_BILLING. Cases that only surface via the app-side margin-loss mask show 'VENDOR_BILLING_OVER_CW (virtual)'.",
                    ),
                    "Est $ Impact": st.column_config.NumberColumn("Est $ Impact", format="$%.2f", disabled=True),
                    "Seat Variance": st.column_config.NumberColumn("Seat Variance", format="%d", disabled=True),
                    "Status": st.column_config.SelectboxColumn(
                        "Status",
                        options=RECON_TEAM_STATUS_OPTIONS,
                        required=True,
                    ),
                    "Assignee": st.column_config.TextColumn("Assignee", width="small"),
                    "Notes": st.column_config.TextColumn("Notes"),
                    "Case ID": st.column_config.TextColumn("Case ID", disabled=True, width="small"),
                },
                key="recon_team_editor",
            )

            # Persist edits back into the session tracker. Only touch entries
            # that actually changed to keep session_state small.
            _now_iso = pd.Timestamp.now().isoformat()
            for _, _r in edited.iterrows():
                cid = str(_r.get("Case ID") or "")
                if not cid:
                    continue
                _status = str(_r.get("Status") or "New")
                _assignee = str(_r.get("Assignee") or "")
                _notes = str(_r.get("Notes") or "")
                _prev = tracker.get(cid, {})
                if (
                    _prev.get("status") != _status
                    or _prev.get("assignee") != _assignee
                    or _prev.get("notes") != _notes
                ):
                    tracker[cid] = {
                        "status": _status,
                        "assignee": _assignee,
                        "notes": _notes,
                        "updated_at": _now_iso,
                    }

            _export_df = edited.copy()
            _export_df["Updated At"] = _export_df["Case ID"].map(
                lambda cid: tracker.get(cid, {}).get("updated_at", "")
            )
            _safe_team_period = (
                period_label.replace(" ", "_").replace(",", "").replace("(", "").replace(")", "")
            )
            st.download_button(
                label="Download Recon Team Queue as CSV",
                data=_export_df.to_csv(index=False).encode("utf-8"),
                file_name=f"recon_team_queue_{_safe_team_period}.csv",
                mime="text/csv",
                key="dl_recon_team_queue",
                help="Snapshot of every actionable case with current Status, Assignee, and Notes.",
            )

    # Full, unfiltered reconciliation detail for the team's offline Excel
    # workflow. This intentionally reads the cached all-vendor OUTPUT_PROD
    # frame directly: no global vendor/month filter, exception filter, case
    # collapse, or Clear-row exclusion is applied.
    st.markdown(f"### Actual Reconciliation — {period_label}")
    _full_recon_export_all = _full_reconciliation_export_cached(
        freshness, SLICE_SCHEMA_VERSION
    )
    _selected_export_months = {
        pd.to_datetime(month).to_period("M") for month in selected_month_ts_list
    }
    _full_recon_export = _full_recon_export_all[
        pd.to_datetime(_full_recon_export_all["Billing Month"], errors="coerce")
        .dt.to_period("M")
        .isin(_selected_export_months)
    ].reset_index(drop=True)
    if _full_recon_export.empty:
        st.markdown(
            '<div class="note">No reconciliation output is currently available.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.caption(
            f"{len(_full_recon_export):,} rows from THIRD_PARTY_RECON_OUTPUT_PROD. "
            "Click a row to inspect its complete reconciliation record. — "
            f"Includes every vendor and every reconciliation outcome for {period_label}."
        )
        _full_recon_event = st.dataframe(
            _full_recon_export,
            use_container_width=True,
            hide_index=True,
            height=520,
            on_select="rerun",
            selection_mode="single-row",
            key="actual_reconciliation_table",
            column_config={
                "Billing Month": st.column_config.DateColumn(
                    "Billing Month", format="YYYY-MM-DD"
                ),
                "SF Account URL": st.column_config.LinkColumn(
                    "SF Account URL", display_text="Open Salesforce"
                ),
                **{
                    column: st.column_config.NumberColumn(column, format="%.2f")
                    for column in (
                        "Vendor Qty", "API Qty", "Avg API Qty", "Zuora Qty",
                        "MP Qty", "CW Total Billing Qty", "Qty Delta",
                    )
                },
                **{
                    column: st.column_config.NumberColumn(column, format="$%.4f")
                    for column in (
                        "Vendor Unit Price", "Zuora Unit Price", "MP Unit Price",
                    )
                },
                **{
                    column: st.column_config.NumberColumn(column, format="$%.2f")
                    for column in (
                        "Vendor Amount", "Zuora Amount", "MP Amount",
                        "CW Total Billing Amount", "Amount Delta",
                    )
                },
            },
        )
        _full_recon_selected_rows: list[int] = []
        try:
            _full_recon_selected_rows = _full_recon_event.selection.rows
        except Exception:
            _full_recon_selected_rows = []
        if _full_recon_selected_rows:
            _selected_recon_row = _full_recon_export.iloc[
                int(_full_recon_selected_rows[0])
            ]
            st.markdown(
                f"#### {html.escape(str(_selected_recon_row['Vendor']))} — "
                f"{html.escape(str(_selected_recon_row['Billing Month']))} — "
                f"{html.escape(str(_selected_recon_row['Case ID']))}"
            )
            st.dataframe(
                pd.DataFrame({
                    "Field": _selected_recon_row.index,
                    "Value": ["" if pd.isna(v) else str(v) for v in _selected_recon_row.values],
                }),
                use_container_width=True,
                hide_index=True,
                height=350,
            )
        _safe_full_period = (
            period_label.replace(" ", "_").replace(",", "")
            .replace("(", "").replace(")", "")
        )
        st.download_button(
            label="Download Actual Reconciliation as CSV",
            data=_full_recon_export.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"actual_reconciliation_{_safe_full_period}.csv",
            mime="text/csv",
            key="dl_actual_reconciliation_full",
            help="Excel-compatible export of every row currently present in THIRD_PARTY_RECON_OUTPUT_PROD, including Clear rows.",
        )


with tab_vendor:
    # ------------------------------------------------------------------
    # Vendor picker for the Deep Dive tab. Independent of the global
    # multiselect so the analyst can inspect any vendor without changing
    # the portfolio-wide filters. Defaults to the first globally selected
    # vendor for continuity across tabs.
    # ------------------------------------------------------------------
    _dd_vendor_names = sorted(v["name"] for v in active_vendors)
    _dd_default_idx = 0
    if selected_vendor_conf["name"] in _dd_vendor_names:
        _dd_default_idx = _dd_vendor_names.index(selected_vendor_conf["name"])
    dd_vendor_name = st.selectbox(
        "Vendor",
        _dd_vendor_names,
        index=_dd_default_idx,
        key="deep_dive_vendor_picker",
        help="Choose which vendor to deep-dive on. Independent of the top-of-page vendor multiselect.",
    )
    dd_vendor_conf = vendor_lookup[dd_vendor_name]
    # Reuse the same session-cached slice builder as the rest of the app so
    # switching vendors on this tab is O(1) after the first render.
    dd_slice = build_filtered_slice(dd_vendor_conf, selected_month_ts_list)

    # Local aliases keep the pre-existing tab code unchanged while directing
    # every downstream computation to the picked vendor's slice.
    active_slice = dd_slice
    selected_vendor_conf = dd_vendor_conf

    st.markdown(f'### {selected_vendor_conf["name"]} deep dive - {period_label}')

    render_vendor_invoice_usage_intra(selected_vendor_conf["name"])

    parity_pct = (
        (active_slice.billing_seats / active_slice.vendor_seats) * 100
        if active_slice.vendor_seats
        else 0
    )
    parity_delta_seats = active_slice.billing_seats - active_slice.vendor_seats
    st.markdown(
        '<div class="kpis">'
        + kpi_html(
            fmt_pct(active_slice.gross_margin_pct),
            "Gross margin",
            fmt_short_money(active_slice.gross_margin) + " margin $",
            hint="CW billed revenue minus SentinelOne invoice cost for the selected period",
        )
        + kpi_html(
            f"{parity_pct:.1f}%",
            "Seat parity",
            f"CW billed / vendor-reported ({parity_delta_seats:+,.0f} seats)",
            hint="CW billed seats as a % of vendor-reported seats. 100% = exact match",
        )
        + kpi_html(
            fmt_short_money(active_slice.revenue_leakage_dollars),
            "Revenue leakage",
            f"{active_slice.revenue_leakage_accounts} affected accounts",
            hint="Finance Queue $: Missing CW Bill + Contract Gap + Vendor Billing/Usage > CW Billing/Usage (deduped). Same definition as the Monthly Reconciliation Action Queue tile",
        )
        + kpi_html(
            fmt_short_money(abs(active_slice.rate_below_cost_dollars)),
            "Contract discount exposure",
            f"{active_slice.rate_below_cost_accounts} accounts billed below cost",
            hint="Material below-cost rows only. Separate from Revenue Leakage — this is a pricing/discount signal, not a billing gap",
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(f"### Month-by-month reconciliation - {selected_vendor_conf['name']}")
    render_monthly_recon_table(selected_vendor_conf["key"])

    st.markdown("### Seat trend - vendor-reported (blue) vs CW-billed (green)")
    render_seat_trend(selected_vendor_conf["key"])

    # ------------------------------------------------------------------
    # Negative Margin Accounts — governed vendor rate vs billed rate
    # ------------------------------------------------------------------
    if has_contract_price(active_slice.detail):
        st.markdown("### Negative Margin Accounts")
        st.caption(
            "**Downstream margin question:** Where CW billed the partner **below** "
            f"the contracted {selected_vendor_conf['name']} cost — CW is selling at a loss or reduced margin."
        )

        below_display_df = _build_negative_margin_frame(active_slice)
        if below_display_df.empty:
            st.markdown(
                '<div class="note">No material below-cost rows for this vendor in the selected period.</div>',
                unsafe_allow_html=True,
            )
        else:
            st.dataframe(
                below_display_df,
                column_config={"Salesforce ID": _salesforce_link_column()},
                use_container_width=True,
                hide_index=True,
            )

        render_vendor_rate_audit(active_slice)

    # ------------------------------------------------------------------
    # Margin erosion tables (billing_month x partner x SKU grain)
    #   1. Vendor-side  - vendor invoiced above contracted rate
    #   2. CW-side      - CW billed partner below retail (Zuora + Marketplace)
    # Rendered above Profitability by SKU so the analyst can decompose
    # margin movement into "vendor over-billing" and "CW under-billing"
    # before drilling into per-SKU mix + margin.
    # ------------------------------------------------------------------
    render_vendor_invoice_vs_contract_rate(active_slice)
    render_cw_retail_vs_billed_rate(active_slice)

    # ------------------------------------------------------------------
    # Profitability by SKU — per-vendor mix + rate view
    # ------------------------------------------------------------------
    render_vendor_sku_profitability(active_slice)


# ---- Tab 3: Profitability by Vendor ---------------------------------------

with tab_profit:
    st.markdown("### Profitability by vendor")

    portfolio_rev = sum(s.billing_amount for s in slices.values())
    portfolio_cost = sum(s.vendor_amount for s in slices.values())
    portfolio_gm = portfolio_rev - portfolio_cost
    portfolio_gm_pct = portfolio_gm / portfolio_rev if portfolio_rev else 0

    # Use the count of SELECTED vendors so the KPI card matches the scope of
    # portfolio_rev / portfolio_cost / portfolio_leakage below.
    selected_vendor_count = len(slices)

    top_names = ", ".join(
        v.name
        for v in sorted(slices.values(), key=lambda s: -s.billing_amount)[:3]
    )
    top3_revenue = sum(s.billing_amount for s in sorted(slices.values(), key=lambda s: -s.billing_amount)[:3])
    top3_share = (top3_revenue / portfolio_rev) if portfolio_rev else 0.0

    st.markdown(
        '<div class="kpis">'
        + kpi_html(
            fmt_short_money(portfolio_rev),
            "3rd-party revenue",
            f"{period_label}, {selected_vendor_count} vendor(s) selected",
            hint="Total CW billing to partners across the SELECTED vendors in the selected period",
        )
        + kpi_html(
            fmt_short_money(portfolio_cost),
            "Vendor cost",
            "COGS on 3rd-party resale",
            hint="Total vendor invoices paid by CW — the cost basis for third-party resale",
        )
        + kpi_html(
            fmt_short_money(portfolio_gm),
            "Gross margin",
            f"{portfolio_gm_pct * 100:.1f}% blended margin",
            hint="Revenue minus vendor cost. Does not net out revenue leakage or below-cost discounts",
        )
        + kpi_html(
            fmt_pct(top3_share, 0),
            "Top-3 revenue concentration",
            top_names or "-",
            hint="Share of total portfolio revenue from the three largest selected vendors by billing amount",
        )
        + kpi_html(
            fmt_short_money(portfolio_leakage),
            "Portfolio revenue leakage",
            "Finance Queue $ across selected vendors",
            hint="Missing CW Bill + Contract Gap + Vendor Billing/Usage > CW Billing/Usage (deduped). Same definition as the Monthly Reconciliation Action Queue tile",
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    # ------------------------------------------------------------------
    # Per-vendor profitability breakout table (multi-vendor aware)
    # ------------------------------------------------------------------
    st.markdown("### Vendor-level breakout")
    _rows = []
    for s in sorted(slices.values(), key=lambda x: -x.billing_amount):
        rev = float(s.billing_amount or 0)
        cost = float(s.vendor_amount or 0)
        gm = rev - cost
        gm_pct = (gm / rev) if rev else 0.0
        vs = float(s.vendor_seats or 0)
        bs = float(s.billing_seats or 0)
        parity = (bs / vs * 100.0) if vs else 0.0
        _rows.append({
            "Vendor": s.name,
            "Revenue": fmt_short_money(rev),
            "Vendor cost": fmt_short_money(cost),
            "Margin $": fmt_short_money(gm),
            "Margin %": f"{gm_pct * 100:.1f}%",
            "Vendor seats": fmt_num(vs),
            "CW billed seats": fmt_num(bs),
            "Seat parity": f"{parity:.1f}%",
            "Revenue leakage $": fmt_short_money(float(s.revenue_leakage_dollars or 0)),
            "Leakage accts": fmt_num(int(s.revenue_leakage_accounts)),
        })
    if _rows:
        _breakout_df = pd.DataFrame(_rows)
        st.dataframe(_breakout_df, use_container_width=True, hide_index=True)
    else:
        st.markdown('<div class="note">No vendors selected.</div>', unsafe_allow_html=True)

    top_leakage = sorted(slices.values(), key=lambda s: -s.revenue_leakage_dollars)
    top_leak = top_leakage[0] if top_leakage else None
    annualization_factor = 12 / float(annualization_months)

    insight_cards: list[str] = []
    # Portfolio-wide leakage (sum across all selected vendors, not just the top one)
    _portfolio_leakage_annualized = sum(s.revenue_leakage_dollars for s in slices.values()) * annualization_factor
    _portfolio_leakage_accts = sum(int(s.revenue_leakage_accounts) for s in slices.values())
    insight_cards.append(
        card_html(
            "red",
            f"{fmt_short_money(_portfolio_leakage_annualized)}/yr",
            "Revenue at Risk",
            (
                f"{_portfolio_leakage_accts} accounts in the Finance Queue across all selected vendors; "
                f"{fmt_short_money(_portfolio_leakage_annualized)}/yr annualized exposure."
            ),
        )
    )

    insight_cards.append(
        card_html(
            "amber",
            fmt_pct(top3_share, 0),
            "Concentration - top 3 vendors",
            "Largest three vendors drive most portfolio revenue and blended-margin movement.",
        )
    )

    insight_cards.append(
        card_html(
            "green",
            f"{fmt_short_money(portfolio_gm * annualization_factor)}/yr",
            "Portfolio annualized margin run-rate",
            f"Annualized from current filtered period ({period_label}).",
        )
    )

    st.markdown("### Insights for review")
    render_cards(insight_cards)

    ai_cards = insight_cards_from_summary(latest_summary_text or "")
    if ai_cards:
        st.markdown("### AI-generated highlights")
        render_cards(ai_cards)

# ---- Tab 4: Summary Snapshot ---------------------------------------------

def render_portfolio_summary(slices_map: dict, period: str) -> None:
    """Portfolio-level executive summary. Aggregates financials, leakage,
    exceptions, and reconciliation status across ALL selected vendors so the
    narrative reflects the current vendor + year + month multi-selection."""
    annual_factor = 12 / float(annualization_months)
    totals = portfolio_totals(slices_map)

    # Aggregate exception rollup + queues.
    # Use cached VendorSlice properties (memoized per slice) instead of
    # re-concatenating detail on every rerun.
    port_roll = portfolio_exception_rollup(slices_map)
    sku_amt = sum(s.sku_mismatch_dollars for s in slices_map.values())
    timing_amt = sum(s.timing_only_dollars for s in slices_map.values())
    rate_below_all = sum(s.rate_below_cost_dollars for s in slices_map.values())

    # Per-vendor status counts (green/amber/red)
    g = sum(1 for s in slices_map.values() if s.worst == "g")
    y = sum(1 for s in slices_map.values() if s.worst == "y")
    r = sum(1 for s in slices_map.values() if s.worst == "r")
    vendor_status_line = (
        f"{g} clear, {y} amber, {r} red across {len(slices_map)} selected vendor(s)"
    )

    top_exceptions: list[str] = []
    if not port_roll.empty:
        for _, row in port_roll.head(3).iterrows():
            top_exceptions.append(
                f"- {row['Exception Type']}: {fmt_short_money(row['EST_DOLLAR_IMPACT'])}, "
                f"{fmt_num(row['Affected Accounts'])} accounts, {fmt_num(row['Seat Variance'])} seats"
            )

    priority_items: list[str] = []
    if not port_roll.empty:
        for _, row in port_roll.head(5).iterrows():
            priority_items.append(
                f"1. {row['Exception Type']} - {fmt_short_money(row['EST_DOLLAR_IMPACT'])} impact; "
                f"{row['Action Needed']}"
            )

    # Per-vendor mini-lines so the narrative always reflects each vendor.
    vendor_lines: list[str] = []
    for s in sorted(slices_map.values(), key=lambda x: -x.billing_amount):
        vendor_lines.append(
            f"- **{s.name}**: {fmt_short_money(s.billing_amount)} revenue, "
            f"{fmt_pct(s.gross_margin_pct)} margin, "
            f"{fmt_short_money(s.revenue_leakage_dollars)} revenue leakage "
            f"({int(s.revenue_leakage_accounts)} accts)"
        )

    scope_line = f"{len(slices_map)} vendor(s), {period}"
    if not slices_map:
        st.markdown("_No vendors selected._")
        return

    lines = [
        f"### Portfolio summary - {scope_line}",
        "",
        "**Scope**",
        f"- Vendors: {', '.join(sorted(s.name for s in slices_map.values()))}",
        f"- Period: {period}",
        f"- Vendor status mix: {vendor_status_line}",
        "",
        "**Portfolio financials (sum of selected vendors)**",
        f"- CW billed revenue: {fmt_money(totals['billing_amount'])}",
        f"- Vendor cost: {fmt_money(totals['vendor_amount'])}",
        f"- Gross margin: {fmt_money(totals['gross_margin'])} ({fmt_pct(totals['gross_margin_pct'])})",
        f"- Annualized gross margin run-rate: {fmt_short_money(totals['gross_margin'] * annual_factor)}/yr",
        "",
        "**Risk and leakage**",
        f"- Revenue leakage (Finance Queue): {fmt_short_money(totals['leakage_dollars'])} across {int(totals['leakage_accounts'])} accounts",
        f"- Annualized revenue leakage run-rate: {fmt_short_money(totals['leakage_dollars'] * annual_factor)}/yr",
        f"- Rate below-cost (material, all rows): {fmt_short_money(rate_below_all)}",
        f"- Wrong-SKU exposure: {fmt_short_money(sku_amt)}",
        f"- Timing-only (non-action): {fmt_short_money(timing_amt)}",
        "",
        "**Vendor breakout**",
        *vendor_lines,
    ]

    if top_exceptions:
        lines.extend(["", "**Top exception categories (portfolio)**", *top_exceptions])
    else:
        lines.extend(["", "**Top exception categories (portfolio)**", "- No open exceptions in the selected slice."])

    if priority_items:
        lines.extend(["", "**Priority actions (address first)**", *priority_items])

    # Escape literal '$' so Streamlit's markdown doesn't treat paired dollar
    # signs as LaTeX inline math (which was mangling bold vendor names like
    # "**SentinelOne**: $4.96M ..." into "**oint**: 4.96M ...").
    st.markdown("\n".join(lines).replace("$", "\\$"))


with tab_ai:
    st.markdown("### Summary Snapshot")
    st.caption(
        f"Aggregated narrative for the current selection: "
        f"{len(slices)} vendor(s) \u00b7 {period_label}."
    )
    render_portfolio_summary(slices, period_label)

    if latest_summary_text:
        _ai_ts_label = fmt_est_timestamp(latest_summary_run_ts) if latest_summary_run_ts is not None else "-"
        with st.expander(
            f"Latest pipeline-run AI summary for {selected_vendor_conf['name']} "
            f"— generated {_ai_ts_label} ({latest_summary_provider} / {latest_summary_model})",
            expanded=False,
        ):
            cleaned_pipeline_summary = normalize_summary_markdown(latest_summary_text)
            st.markdown(
                f"<div class='stand' style='white-space:pre-wrap'>{html.escape(cleaned_pipeline_summary)}</div>",
                unsafe_allow_html=True,
            )
    else:
        st.info("No pipeline-run AI summary row found yet. Run the pipeline summary generator to populate this section.")


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown(
    f'<div class="foot">\u00A9 ConnectWise \u00B7 Third Party Reconciliation Suite<br/>'
    f'Pipeline last refreshed (EST): '
    f'{fmt_est_timestamp(latest_freshness_timestamp(freshness))}</div>',
    unsafe_allow_html=True,
)

