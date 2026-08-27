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
# v26: ESET is quantity-first and now carries contract-cost overlay dollars.
# v27: adds vendor invoice vs raw vendor usage SKU-level intra-vendor control.
SLICE_SCHEMA_VERSION = "v27"

# Reconciliation check keys shown on every vendor row.
CHECKS = [
    ("account", "Account Match"),
    ("seats", "Seat Count"),
    ("sku", "SKU Match"),
    ("price", "Negative Margin Accounts"),
]

# Glossary shown under the Vendor Reconciliation Status matrix.
COLUMN_GLOSSARY: list[tuple[str, str]] = [
    ("Account Match", "Validated mapping of vendor account ID to ConnectWise Salesforce ID."),
    ("Seat Count", "Vendor-reported seat count matches CW billed quantity for the same account/product."),
    ("SKU Match", "Vendor-billed SKU maps to the CW-billed SKU (revenue booked on the correct product)."),
    ("Negative Margin Accounts", "Accounts where the vendor-billed amount exceeds the CW-invoiced amount \u2014 CW is losing margin on the resale."),
]

# Plain-English glossary of the 12 canonical OUTCOME_FLAG / EXCEPTION_TYPE values.
# These are the ONLY values the pipeline now emits. Shown in the Category Traceability Audit panel.
OUTCOME_FLAG_GLOSSARY: list[tuple[str, str]] = [
    ("Clear",
     "CW amount \u2265 vendor amount (including CW well above vendor \u2014 positive margin is fine). No action required."),
    ("Known Discount / Bundle",
     "Intentional pricing applied by the individual pipeline: RMM bundle discount (Webroot), MDR bundle (SentinelOne), CW-included zero-dollar line. No action required."),
    ("Marketplace Billing Delay",
     "Prior-period Marketplace billing timing artifact \u2014 expected billing was missing this month but existed previously. Will self-resolve next cycle. No action required."),
    ("Unmapped Partner",
     "Vendor partner name cannot be resolved to a Salesforce ID. The account cannot be matched to any CW billing. Data team must add the partner mapping."),
    ("Duplicated CW Invoice",
     "Both Zuora AND Marketplace billed the same account/product/month. Billing Ops must identify the duplicate source and cancel one."),
    ("API Usage, Insufficient CW Billing",
     "TRT/API endpoint data confirms active usage but CW billing is missing or materially short. Finance must close billing for the confirmed usage."),
    ("Vendor SKU, No CW SKU",
     "Vendor is billing CW for a product that has no corresponding CW rebill SKU. Product/Catalog must create the rebill SKU before CW can charge the partner."),
    ("CW SKU, No Vendor SKU",
     "CW billed the partner on a SKU for which the vendor has no matching charge. Ops must verify whether this CW subscription is still active and correct."),
    ("Vendor Billing, No CW Billing",
     "Vendor is charging CW for this account/product (vendor amount > $0) but CW has zero billing to the partner. Finance/Sales must onboard or restore the billing contract."),
    ("CW Billing, No Vendor Billing",
     "CW billed the partner (CW amount > $0) but the vendor charges CW nothing for this account/product. Ops must verify vendor attribution or retire the stale subscription."),
    ("Vendor Billing, Insufficient CW Billing",
     "Vendor charges CW more than 25% above what CW bills the partner (both sides have real amounts). Finance/Sales must close the billing gap \u2014 CW is losing margin."),
    ("Other Issue",
     "Catch-all for rows that do not fit any of the 11 defined categories. Review manually to determine the correct action."),
]

# Plain-English glossary of the 12 canonical EXCEPTION_TYPE buckets.
EXCEPTION_TYPE_GLOSSARY: list[tuple[str, str]] = [
    ("Unmapped Partner", "Vendor account cannot be resolved to a Salesforce ID. No CW billing can be matched until the partner mapping table is updated."),
    ("Duplicated CW Invoice", "Both Zuora AND Marketplace billed the same account/product/month. Billing Ops must identify and cancel the duplicate source."),
    ("Marketplace Billing Delay", "Prior-period Marketplace billing timing lag \u2014 will self-resolve next cycle. No action required."),
    ("Known Discount / Bundle", "Intentional pricing: RMM bundle discount (Webroot), MDR bundle (SentinelOne), or CW-included zero-dollar line. No action required."),
    ("Vendor SKU, No CW SKU", "Vendor invoiced CW for a product with no corresponding CW rebill SKU. Product/Catalog must create the rebill SKU."),
    ("CW SKU, No Vendor SKU", "CW billed a rebill SKU for which the vendor has no matching invoice line. Ops must verify whether this subscription should still be active."),
    ("API Usage, Insufficient CW Billing", "TRT/API usage data confirms active endpoint usage but CW billing is missing or materially short. Finance must close billing for the confirmed usage."),
    ("Vendor Billing, No CW Billing", "Vendor charged CW (vendor amount > $0) but CW billing to the partner = $0. Finance/Sales must onboard or restore the billing contract."),
    ("CW Billing, No Vendor Billing", "CW billed the partner (CW amount > $0) but vendor amount = $0. Stale subscription or vendor attribution gap \u2014 Ops must verify."),
    ("Vendor Billing, Insufficient CW Billing", "Vendor charges CW more than 25% above what CW bills the partner (both sides > $0). Finance/Sales must close the gap \u2014 CW is losing margin."),
    ("Clear", "CW amount \u2265 vendor amount. No action \u2014 row hidden from the Billing Exception Summary."),
    ("Other Issue", "Catch-all for rows that do not fit any defined category. Review manually."),
]
CHIP_LABELS = {"g": "Match", "y": "Review", "r": "Exception"}
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

/* Recon status matrix */
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
            SELECT COALESCE(TO_VARCHAR(MAX(LAST_ALTERED), 'YYYY-MM-DD HH24:MI:SS'), '') AS K
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
        "MARKETPLACE_QUANTITY", "MARKETPLACE_AMOUNT",
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
    ]
    text_empty = [
        "VENDOR", "SF_ID", "VENDOR_PARTNER_NAME", "VENDOR_PRODUCT",
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
    ]
    bool_false = [
        "DUPLICATE_BILLING_FLAG", "MARKETPLACE_TIMING_FLAG", "MATERIAL_BELOW_COST_FLAG",
        # Pipeline v23 boolean queue-membership flags.
        "IS_LEAKAGE", "IS_FINANCE_QUEUE", "IS_OPS_QUEUE",
        "IS_TIMING_QUEUE", "IS_CLEAR",
    ]
    out = df.copy()
    for col in numeric_zero:
        if col not in out.columns:
            out[col] = 0.0
    for col in text_empty:
        if col not in out.columns:
            out[col] = ""
    for col in bool_false:
        if col not in out.columns:
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
    out = df.copy()
    for col in numeric_zero:
        if col not in out.columns:
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

    # Ghost-month trim baked into the cached load: any month where the
    # vendor summary shows 0 seats AND 0 amount is dropped from every
    # returned frame. Prevents phantom trailing months (TRT / marketplace
    # accumulation past the last invoice month) from leaking into the app.
    if not summary.empty and "BILLING_MONTH" in summary.columns:
        seat_col = "TOTAL_VENDOR_SEATS" if "TOTAL_VENDOR_SEATS" in summary.columns else None
        amt_col = "TOTAL_VENDOR_AMOUNT" if "TOTAL_VENDOR_AMOUNT" in summary.columns else None
        if seat_col or amt_col:
            mask = pd.Series(False, index=summary.index)
            if seat_col:
                mask = mask | (pd.to_numeric(summary[seat_col], errors="coerce").fillna(0) > 0)
            if amt_col:
                mask = mask | (pd.to_numeric(summary[amt_col], errors="coerce").fillna(0) > 0)
            loaded = set(pd.to_datetime(summary.loc[mask, "BILLING_MONTH"]).unique())
            for key, df in (("summary", summary), ("detail", detail), ("coverage", coverage)):
                if df is None or df.empty or "BILLING_MONTH" not in df.columns:
                    continue
                if key == "summary":
                    summary = df.loc[df["BILLING_MONTH"].isin(loaded)].reset_index(drop=True)
                elif key == "detail":
                    detail = df.loc[df["BILLING_MONTH"].isin(loaded)].reset_index(drop=True)
                else:
                    coverage = df.loc[df["BILLING_MONTH"].isin(loaded)].reset_index(drop=True)

    # Vendor-file-presence cap: restrict all frames to months where we have
    # at least one row that came from an actual vendor usage file
    # (VENDOR_SOURCE_ROW_COUNT > 0).  This is the canonical "vendor usage
    # is the limiting factor" guard -- if no vendor file data exists for a
    # month, there is nothing to reconcile against, so those months are
    # excluded regardless of what the CW billing side shows.
    if not detail.empty and "VENDOR_SOURCE_ROW_COUNT" in detail.columns and "BILLING_MONTH" in detail.columns:
        _src_active = pd.to_numeric(detail["VENDOR_SOURCE_ROW_COUNT"], errors="coerce").fillna(0) > 0
        _vendor_file_months = set(pd.to_datetime(detail.loc[_src_active, "BILLING_MONTH"]).unique())
        if _vendor_file_months:
            summary = summary[pd.to_datetime(summary["BILLING_MONTH"]).isin(_vendor_file_months)].reset_index(drop=True)
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


# ---------------------------------------------------------------------------
# Reconciliation math (per-vendor detail slice)
# ---------------------------------------------------------------------------

def outcome_count(detail: pd.DataFrame, flag: str) -> int:
    if detail.empty or "OUTCOME_FLAG" not in detail.columns:
        return 0
    return int((detail["OUTCOME_FLAG"] == flag).sum())


def outcome_qty(detail: pd.DataFrame, flag: str) -> float:
    if detail.empty:
        return 0.0
    return float(detail.loc[detail["OUTCOME_FLAG"] == flag, "ABS_QTY_DELTA"].fillna(0).sum())


# ---------------------------------------------------------------------------
# Display bucket constants (2026-08-12 v2 refresh)
# ---------------------------------------------------------------------------
# EXCEPTION_TYPE is computed app-side from raw quantities/amounts by
# _classify_bucket_series so buckets are strictly mutually exclusive:
# every row lands in EXACTLY ONE of these labels.

BUCKET_CLEAR                 = "Clear"
BUCKET_UNMAPPED              = "Unmapped Partner"
BUCKET_DUPLICATE             = "Duplicated CW Invoice"  # legacy-only fallback label
BUCKET_MARKETPLACE_TIMING    = "Marketplace Billing Delay"
BUCKET_KNOWN_DISCOUNT        = "Known Discount / Bundle"
BUCKET_VENDOR_SKU_NO_CW      = "Vendor SKU, No CW SKU"
BUCKET_CW_SKU_NO_VENDOR      = "CW SKU, No Vendor SKU"
BUCKET_TRT_NO_BILLING        = "API Usage, Insufficient CW Billing"
BUCKET_VENDOR_NO_CW_BILLING  = "Vendor Billing, No CW Billing"
BUCKET_CW_NO_VENDOR_BILLING  = "CW Billing, No Vendor Billing"
BUCKET_VENDOR_NO_CW          = "Vendor Billing, Insufficient CW Billing"

# Ordered list drives display ordering / iteration.
# "Other Issue" is explicitly included so the traceability audit orphan check
# does not flag legitimate catch-all rows as unclassified.
EXCEPTION_BUCKETS = [
    BUCKET_UNMAPPED,
    BUCKET_MARKETPLACE_TIMING,
    BUCKET_KNOWN_DISCOUNT,
    BUCKET_VENDOR_SKU_NO_CW,
    BUCKET_CW_SKU_NO_VENDOR,
    BUCKET_TRT_NO_BILLING,
    BUCKET_VENDOR_NO_CW_BILLING,
    BUCKET_CW_NO_VENDOR_BILLING,
    BUCKET_VENDOR_NO_CW,
    BUCKET_CLEAR,
    "Other Issue",
]

# Plain-English display names for pipeline OUTCOME_FLAG values. Used only
# as a fallback by _classify_bucket_series for rows where EXCEPTION_TYPE
# is missing/empty. The pipeline (build_third_party_recon_output_prod.py)
# writes canonical EXCEPTION_TYPE for every row, so this map exists only
# for backward compatibility with historical data.
FLAG_PLAIN: dict[str, str] = {
    # Clean / no-action flags
    "CLEAR": BUCKET_CLEAR,
    "MATCHED": BUCKET_CLEAR,
    "MINOR_DRIFT": BUCKET_CLEAR,
    "NEGLIGIBLE_DOLLAR_EXPOSURE": BUCKET_CLEAR,
    "MARKETPLACE_ONLY_CLEAR": BUCKET_CLEAR,
    "OVERAGE_EXPECTED": BUCKET_CLEAR,
    # CW > vendor rows are folded into Clear (positive margin, no action)
    "MATERIAL_OVER_VENDOR": BUCKET_CLEAR,
    "BILLING_DIFFERENTIAL_OVER": BUCKET_CLEAR,
    "BILLING_OVER_VENDOR": BUCKET_CLEAR,
    "MARKETPLACE_OVERAGE": BUCKET_CLEAR,
    # Structural data integrity
    "PARTNER_MAPPING_REQUIRED": BUCKET_UNMAPPED,
    # Duplicate is informational now (Y/N column), not a primary exception bucket.
    "DUPLICATE_BILLING": "Other Issue",
    "MARKETPLACE_TIMING": BUCKET_MARKETPLACE_TIMING,
    "MARKETPLACE_BILLING_NO_VENDOR": BUCKET_MARKETPLACE_TIMING,
    "BILLING_TIMING_ADJACENT_MONTH": BUCKET_MARKETPLACE_TIMING,
    # Intentional pricing
    "RMM_DISCOUNTED": BUCKET_KNOWN_DISCOUNT,
    "KNOWN_DISCOUNT_BUNDLE": BUCKET_KNOWN_DISCOUNT,
    "MDR_BUNDLE": BUCKET_KNOWN_DISCOUNT,
    "CW_INCLUDED_ZERO_DOLLAR": BUCKET_KNOWN_DISCOUNT,
    "INTENTIONAL_DISCOUNT": BUCKET_KNOWN_DISCOUNT,
    # Catalog / SKU gaps
    "VENDOR_ADDON_NO_CW_SKU": BUCKET_VENDOR_SKU_NO_CW,
    "VENDOR_PRODUCT_NO_CW_SKU": BUCKET_VENDOR_SKU_NO_CW,
    "VENDOR_SKU_NO_CW_SKU": BUCKET_VENDOR_SKU_NO_CW,
    "CW_ONLY_ADDON_NO_VENDOR": BUCKET_CW_SKU_NO_VENDOR,
    "CW_SKU_NO_VENDOR_SKU": BUCKET_CW_SKU_NO_VENDOR,
    # TRT / API usage evidence
    "API Usage Recorded, No CW Billing": BUCKET_TRT_NO_BILLING,
    "TRT_VENDOR_USAGE_NOT_BILLED": BUCKET_TRT_NO_BILLING,
    "STRUCTURAL_VENDOR_ONLY_TRT_CONFIRMED": BUCKET_TRT_NO_BILLING,
    # No-billing structural flags
    "STRUCTURAL_VENDOR_ONLY_NO_CONTRACT": BUCKET_VENDOR_NO_CW_BILLING,
    "NO_BILLING_NO_HISTORY": BUCKET_VENDOR_NO_CW_BILLING,
    "STRUCTURAL_BILLING_ONLY": BUCKET_CW_NO_VENDOR_BILLING,
    "BILLING_ONLY_NO_VENDOR_USAGE": BUCKET_CW_NO_VENDOR_BILLING,
    "STRUCTURAL_BILLING_ONLY_TRT_CONFIRMED": BUCKET_CW_NO_VENDOR_BILLING,
    # Quantity/amount variance flags
    "MATERIAL_UNDER_VENDOR": BUCKET_VENDOR_NO_CW,
    "BILLING_DIFFERENTIAL_UNDER": BUCKET_VENDOR_NO_CW,
    "VENDOR_OVER_BILLING": BUCKET_VENDOR_NO_CW,
    "ACCOUNT_HAS_OTHER_S1_BILLING_NO_MATCH": BUCKET_VENDOR_NO_CW,
    "KNOWN_GOOD_MAPPING_MISSING_CURRENT_BILLING": BUCKET_VENDOR_NO_CW_BILLING,
    "MAPPED_ADDON_NO_CURRENT_BILLING": BUCKET_VENDOR_NO_CW_BILLING,
    "SKU_MISMATCH_BILLING_ON_OTHER_SKU": BUCKET_VENDOR_NO_CW,
    "CONTRACT_TIMING_OR_INACTIVE": BUCKET_VENDOR_NO_CW_BILLING,
    # Auvik/Exium operational flags
    "TAKEOUT_SUPPORT_FILE_NO_DIRECT_BILLING": BUCKET_VENDOR_NO_CW_BILLING,
    "CARR_SECONDARY_CHECK_ONLY": BUCKET_VENDOR_NO_CW_BILLING,
    # Unified OUTCOME_FLAG value -- maps to Vendor Billing, Insufficient CW Billing
    "Vendor Billing > CW Billing": BUCKET_VENDOR_NO_CW,
}

# Bucket -> plain-English action string. Used in tables + queue tiles.
FLAG_DISPLAY_ACTION: dict[str, str] = {
    BUCKET_CLEAR: "None",
    BUCKET_UNMAPPED: "Data team: update partner mapping",
    BUCKET_DUPLICATE: "Billing Ops: review duplicate overlap signal",
    BUCKET_MARKETPLACE_TIMING: "No action \u2014 prior-month invoice expected next cycle",
    BUCKET_KNOWN_DISCOUNT: "No action \u2014 intentional discount or bundle pricing",
    BUCKET_VENDOR_SKU_NO_CW: "Product / Catalog: add a CW rebill SKU for this vendor product",
    BUCKET_CW_SKU_NO_VENDOR: "Ops: verify whether this CW rebill SKU should still be active",
    BUCKET_TRT_NO_BILLING: "Finance: close billing gap for TRT/API-confirmed usage",
    BUCKET_VENDOR_NO_CW_BILLING: "Finance / Sales: onboard billing \u2014 vendor charged CW with no CW rebill to partner",
    BUCKET_CW_NO_VENDOR_BILLING: "Ops: verify vendor-side attribution or retire the stale CW subscription",
    BUCKET_VENDOR_NO_CW: "Finance / Sales: close billing gap \u2014 vendor materially ahead of CW",
}

# Queue category groupings (used by the Action Queue tiles on the Monthly
# Reconciliation tab). Catalog Gap (Vendor SKU, No CW SKU) is intentionally
# counted in BOTH queues: it is real revenue leakage AND it needs Ops/Product
# action to add the missing rebill SKU.
FINANCE_QUEUE_CATEGORIES = [
    BUCKET_VENDOR_NO_CW_BILLING,
    BUCKET_VENDOR_NO_CW,
    BUCKET_TRT_NO_BILLING,
    BUCKET_VENDOR_SKU_NO_CW,
]
OPS_QUEUE_CATEGORIES = [
    BUCKET_CW_NO_VENDOR_BILLING,
    BUCKET_CW_SKU_NO_VENDOR,
    BUCKET_VENDOR_SKU_NO_CW,  # Catalog gap -- needs Ops/Product action
    BUCKET_UNMAPPED,
]
TIMING_QUEUE_CATEGORIES = [BUCKET_MARKETPLACE_TIMING]
KNOWN_NO_ACTION_CATEGORIES = [BUCKET_KNOWN_DISCOUNT]

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
    """Below-cost loss $ restricted to rows with OUTCOME_FLAG='CLEAR' (safe to add
    to quantity-leakage without double counting)."""
    if not has_contract_price(detail) or "OUTCOME_FLAG" not in detail.columns:
        return 0.0
    mask = _contract_mask(detail, "BELOW_COST_DISCOUNT", material_only=material_only)
    mask &= detail["OUTCOME_FLAG"].astype(str) == "CLEAR"
    return float(detail.loc[mask, "BILLING_VS_COST_DOLLAR_IMPACT"].fillna(0).sum())


# ---------------------------------------------------------------------------
# Canonical bucket classifier (2026-08-12 v2)
#
# Every row lands in EXACTLY ONE bucket. Priority order:
#   1. OUTCOME_FLAG structural overrides (unmapped / duplicate / catalog /
#      marketplace timing)  -- these are data-integrity classes, not qty math.
#   2. Numeric classification off raw quantities/amounts:
#         a. Clear if within 3% qty tolerance OR positive margin (CW $ >= vendor $).
#         b. Both-sides material AND smaller >= 25% of larger -> "close" buckets
#            (Vendor > CW or CW > Vendor depending on qty sign).
#         c. Otherwise -> "no billing" buckets (Vendor Billing No CW /
#            CW Billing No Vendor) based on which side dominates.
# Threshold constants pulled out so recon team can tune from one place.
# ---------------------------------------------------------------------------

BUCKET_QTY_TOLERANCE_PCT = 0.03   # <=3% qty variance -> Clear
BUCKET_CLOSE_RATIO = 0.25         # smaller side >= 25% of larger side -> "close" (buckets 7/8)


# Label used in the recon team queue for rows surfacing via the vendor-over mask.
# Points to "Vendor Billing, Insufficient CW Billing" after bucket consolidation.
VENDOR_BILLING_OVER_CW_LABEL = BUCKET_VENDOR_NO_CW


def _classify_bucket_series(detail: pd.DataFrame) -> pd.Series:
    """Return a Series aligned to `detail.index` giving each row its canonical
    EXCEPTION_TYPE bucket. Never returns NaN.

    Fast path: EXCEPTION_TYPE is pre-computed by build_third_party_recon_output_prod.py.
    Old transitional values are remapped inline for backward compatibility with any
    data built before the canonical taxonomy was deployed.
    Fallback: derive from OUTCOME_FLAG via FLAG_PLAIN for rows without EXCEPTION_TYPE.
    """
    if detail.empty:
        return pd.Series([], dtype=object, index=detail.index)
    if "EXCEPTION_TYPE" in detail.columns:
        raw = detail["EXCEPTION_TYPE"].astype(str)
        return raw.replace({
            # Legacy transitional values → canonical equivalents
            "Vendor Billing/Usage > CW Billing/Usage": BUCKET_VENDOR_NO_CW,
            "CW Billing/Usage > Vendor Billing/Usage": BUCKET_CLEAR,
            "CW Billing, Insufficient Vendor Billing": BUCKET_CLEAR,
            "Overage": BUCKET_CLEAR,
            "Vendor Billing > CW Billing": BUCKET_VENDOR_NO_CW,
            "Billed by Vendor, Missing CW Billing": BUCKET_VENDOR_NO_CW_BILLING,
            "Billed by CW, Missing Vendor Billing": BUCKET_CW_NO_VENDOR_BILLING,
            "Missing CW Billing - API Confirmed": BUCKET_TRT_NO_BILLING,
            "API Usage Recorded, No CW Billing": BUCKET_TRT_NO_BILLING,
            "Unmapped SKU": BUCKET_UNMAPPED,
            "Duplicate Billing": "Other Issue",
            "Clear - Discounted / Bundled": BUCKET_KNOWN_DISCOUNT,
        })
    # Fallback: EXCEPTION_TYPE not in table — derive from OUTCOME_FLAG.
    flag = detail.get("OUTCOME_FLAG", pd.Series("", index=detail.index)).astype(str)
    result = flag.map(FLAG_PLAIN).fillna("Other Issue")
    return result


# ---------------------------------------------------------------------------
# Bucket-based masks. Simple wrappers around the deterministic EXCEPTION_TYPE
# column since buckets are already mutually exclusive.
# ---------------------------------------------------------------------------


def _bucket_series(detail: pd.DataFrame) -> pd.Series:
    if "EXCEPTION_TYPE" in detail.columns:
        return detail["EXCEPTION_TYPE"].astype(str)
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
    BUCKET_VENDOR_SKU_NO_CW,
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
    """Absolute dollar impact for rows matching the given exception type or outcome flags.

    Checks EXCEPTION_TYPE first (canonical, SQL-precomputed). Falls back to
    OUTCOME_FLAG for any rows where EXCEPTION_TYPE is absent or empty.
    """
    if detail.empty:
        return 0.0
    # Primary: use EXCEPTION_TYPE (canonical bucket, always present in OUTPUT_PROD)
    if "EXCEPTION_TYPE" in detail.columns:
        mask = detail["EXCEPTION_TYPE"].astype(str).isin(flags)
        if "AMOUNT_DELTA" in detail.columns:
            return float(detail.loc[mask, "AMOUNT_DELTA"].fillna(0).abs().sum())
        if "TOTAL_BILLING_AMOUNT" in detail.columns and "VENDOR_AMOUNT" in detail.columns:
            sub = detail.loc[mask]
            return float((sub["VENDOR_AMOUNT"].fillna(0) - sub["TOTAL_BILLING_AMOUNT"].fillna(0)).abs().sum())
        return 0.0
    # Fallback: legacy OUTCOME_FLAG column
    if "OUTCOME_FLAG" not in detail.columns:
        return 0.0
    mask = detail["OUTCOME_FLAG"].isin(flags)
    if "AMOUNT_DELTA" in detail.columns:
        return float(detail.loc[mask, "AMOUNT_DELTA"].fillna(0).abs().sum())
    if "TOTAL_BILLING_AMOUNT" in detail.columns and "VENDOR_AMOUNT" in detail.columns:
        sub = detail.loc[mask]
        return float((sub["VENDOR_AMOUNT"].fillna(0) - sub["TOTAL_BILLING_AMOUNT"].fillna(0)).abs().sum())
    return 0.0


def flag_accounts(detail: pd.DataFrame, flags: list[str]) -> int:
    """Distinct affected account count (SF_ID) for the given exception/outcome flags."""
    if detail.empty:
        return 0
    # Primary: EXCEPTION_TYPE
    if "EXCEPTION_TYPE" in detail.columns:
        mask = detail["EXCEPTION_TYPE"].astype(str).isin(flags)
        if "SF_ID" in detail.columns:
            return int(detail.loc[mask, "SF_ID"].nunique())
        return int(mask.sum())
    # Fallback: OUTCOME_FLAG
    if "OUTCOME_FLAG" not in detail.columns:
        return 0
    mask = detail["OUTCOME_FLAG"].isin(flags)
    if "SF_ID" in detail.columns:
        return int(detail.loc[mask, "SF_ID"].nunique())
    return int(mask.sum())


def flag_seats(detail: pd.DataFrame, flags: list[str]) -> float:
    """Sum of ABS_QTY_DELTA for rows matching the given exception/outcome flags."""
    if detail.empty:
        return 0.0
    if "EXCEPTION_TYPE" in detail.columns:
        mask = detail["EXCEPTION_TYPE"].astype(str).isin(flags)
    elif "OUTCOME_FLAG" in detail.columns:
        mask = detail["OUTCOME_FLAG"].isin(flags)
    else:
        return 0.0
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
        sku_flags = ["Vendor SKU, No CW SKU", "CW SKU, No Vendor SKU"]
        accts = flag_accounts(detail, sku_flags)
        seats = flag_seats(detail, sku_flags)
        pct = seats / denom if denom else 0.0
        if accts == 0:
            return "g", "No SKU catalog gaps"
        if pct < 0.03:
            return "y", f"{accts} accounts with SKU gaps"
        return "r", f"{accts} accounts with SKU gaps"

    if check_key == "price":
        # Duplicate billing + stale CW subscriptions + contract discount signal.
        dup_accts = flag_accounts(detail, ["Duplicated CW Invoice"])
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
    if "EST_DOLLAR_IMPACT" in d.columns:
        # Precomputed by SQL as ABS(amount_delta). Skip the fillna+abs pass.
        pass
    elif "AMOUNT_DELTA" in d.columns:
        d["EST_DOLLAR_IMPACT"] = d["AMOUNT_DELTA"].fillna(0).abs()
    else:
        d["EST_DOLLAR_IMPACT"] = 0.0

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
            # Count Clear rows using EXCEPTION_TYPE (canonical) when available,
            # otherwise fall back to OUTCOME_FLAG == "Clear"
            if "EXCEPTION_TYPE" in self.detail.columns:
                self.matched_rows = int((self.detail["EXCEPTION_TYPE"] == "Clear").sum())
            else:
                self.matched_rows = int((self.detail["OUTCOME_FLAG"] == "Clear").sum())

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
        return outcome_qty(self.detail, "Other Issue")

    @cached_property
    def sku_mismatch_dollars(self) -> float:
        """Dollar exposure for Other Issue rows (unified taxonomy)."""
        return flag_dollars(self.detail, ["Other Issue"])

    @cached_property
    def timing_qty(self) -> float:
        """Not separately tracked in the unified 10-flag taxonomy."""
        return 0.0

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
        f"Data auto-refreshes when the pipeline rebuilds any recon table "
        f"(TTL {DATA_TTL_SECONDS}s). Latest LAST_ALTERED across recon + map + "
        f"resolver: {freshness or '(unknown)'}"
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
with st.expander(f"\U0001f50d Data source audit  (schema {SLICE_SCHEMA_VERSION})", expanded=False):
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
                "If the values above are NOT from the canonical 12-bucket taxonomy "
                "(Clear, Unmapped Partner, Duplicated CW Invoice, Known Discount / Bundle, "
                "Marketplace Billing Delay, API Usage Insufficient CW Billing, Vendor SKU No CW SKU, "
                "CW SKU No Vendor SKU, Vendor Billing No CW Billing, CW Billing No Vendor Billing, "
                "Vendor Billing Insufficient CW Billing, Other Issue) \u2014 "
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


def _loaded_months_for(summary: pd.DataFrame) -> set:
    """Return the set of BILLING_MONTH values in the summary frame.

    The ghost-month filter now runs inside the cached `_load_combined_vendor`,
    so by the time we get here every month present in the summary is a
    real vendor-loaded month. This helper just exposes that set for the
    portfolio-wide month union below.
    """
    if summary.empty or "BILLING_MONTH" not in summary.columns:
        return set()
    return set(pd.to_datetime(summary["BILLING_MONTH"]).unique())


# The ghost-month trim is applied inside `_load_combined_vendor_impl` (see
# above) so no additional per-vendor filtering is needed here.

if not active_vendors:
    st.warning("No vendor recon output available. Run the pipeline and refresh.")
    st.stop()

# If every vendor loaded but has no summary/detail yet, continue — the app
# will render "no data" states in each tab rather than stopping.
first_vendor = active_vendors[0]

# Portfolio-level months are the union of loaded months across every active
# vendor (each vendor was already trimmed to its loaded months above).
_month_union: set = set()
for v in active_vendors:
    _s = v["data"]["summary"]
    if _s is not None and not _s.empty and "BILLING_MONTH" in _s.columns:
        _month_union |= set(pd.to_datetime(_s["BILLING_MONTH"]).unique())
months_available = sorted(_month_union)
# Cap at the current calendar month — future-dated contract/royalty rows in
# Zuora or Bitdefender quarterly billings can produce months that haven't
# happened yet, which bleeds month-number highlights into the pill row.
_current_month_cap = pd.Timestamp.today().normalize().to_period("M").to_timestamp()
months_available = [m for m in months_available if pd.to_datetime(m) <= _current_month_cap]
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
_all_vendor_names = list(vendor_lookup.keys())

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
                SUM(COALESCE(EST_DOLLAR_IMPACT, ABS(COALESCE(AMOUNT_DELTA, 0)))) AS "EST_DOLLAR_IMPACT"
            FROM {SCHEMA}.THIRD_PARTY_RECON_OUTPUT_PROD
            WHERE VENDOR IN ({v_in})
              AND EXCEPTION_TYPE != 'Clear'
              AND EXCEPTION_TYPE IS NOT NULL
              AND EXCEPTION_TYPE != ''{month_sql}
            GROUP BY EXCEPTION_TYPE
            HAVING SUM(COALESCE(ABS_QTY_DELTA, 0)) > 0
                OR SUM(COALESCE(EST_DOLLAR_IMPACT, ABS(COALESCE(AMOUNT_DELTA, 0)))) > 0
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
    header = '<tr><th style="width:16%">Vendor</th>'
    for _, label in CHECKS:
        header += f'<th class="c" style="width:21%">{label}</th>'
    header += '</tr>'

    rows_html = []
    ordered = sorted(
        slices.values(),
        key=lambda s: (-RANK[s.worst], -(s.billing_amount)),
    )
    for s in ordered:
        row = f'<tr><td><b>{s.name}</b></td>'
        for key, _ in CHECKS:
            status, cap = s.matrix[key]
            # Make descriptions more concise and impactful
            short_cap = cap[:60] + "..." if len(cap) > 60 else cap
            row += f'<td class="c">{chip_html(status)}<span class="cellcap">{short_cap}</span></td>'
        row += f'</tr>'
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
    # OUTCOME_FLAG intentionally last so the primary business dimensions
    # (month, account, product, seats, amounts) render leftmost.
    # If the input carries a _VENDOR column (portfolio view), surface it early.
    _col_source = [
        "BILLING_MONTH",
    ]
    if "_VENDOR" in drill.columns:
        _col_source.append("_VENDOR")
    _col_source += [
        "SF_ID", "VENDOR_PARTNER_NAME", "VENDOR_PRODUCT",
        "ACTION_NEEDED", "VENDOR_QUANTITY",
        "TOTAL_BILLING_QUANTITY",
        # API_QUANTITY / AVG_API_QUANTITY surface TRT / vendor-API telemetry
        # for pipelines that publish it (Webroot, SentinelOne). Null for
        # vendors without an API feed — the column renders empty in that case.
        "API_QUANTITY", "AVG_API_QUANTITY",
        "QTY_DELTA", "ABS_QTY_DELTA",
        "VENDOR_UNIT_PRICE", "TOTAL_BILLING_UNIT_PRICE",
        "VENDOR_AMOUNT", "TOTAL_BILLING_AMOUNT", "AMOUNT_DELTA",
        "VENDOR_INVOICE_SKU", "VENDOR_INVOICE_RATE_SOURCE",
        "DUPLICATE_BILLING",
        "INVESTIGATION_REASON", "OUTCOME_FLAG",
    ]
    detail_cols = [c for c in _col_source if c in drill.columns]

    st.markdown(f"#### {selected_exception} — {len(drill):,} rows")
    if selected_exception == BUCKET_VENDOR_NO_CW:
        st.caption(
            "Vendor amount or seat count materially exceeds CW billing (>25% gap, or both sides "
            "material with vendor higher). Finance / Sales must close the gap."
        )
    st.dataframe(
        drill[detail_cols],
        column_config={
            "BILLING_MONTH": st.column_config.TextColumn("Billing Month"),
            "_VENDOR": st.column_config.TextColumn("Vendor"),
            "SF_ID": st.column_config.TextColumn("Salesforce ID"),
            "VENDOR_PARTNER_NAME": st.column_config.TextColumn("Partner"),
            "VENDOR_PRODUCT": st.column_config.TextColumn("Product"),
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
                    "BILLING_MONTH", "SF_ID", "VENDOR_PARTNER_NAME",
                    "VENDOR_PRODUCT", "OUTCOME_FLAG", "EXCEPTION_TYPE",
                    "TOTAL_BILLING_AMOUNT", "VENDOR_AMOUNT", "AMOUNT_DELTA",
                ] if c in _orphans.columns]
                st.dataframe(_orphans[_keep], use_container_width=True, hide_index=True)


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
        month_detail = detail_all[detail_all["BILLING_MONTH"] == month]
        vs = float(row.get("TOTAL_VENDOR_SEATS") or 0)
        bs = float(row.get("TOTAL_BILLING_SEATS") or 0)
        month_cov = coverage_all[coverage_all["BILLING_MONTH"] == month] if not coverage_all.empty else coverage_all
        month_row_cov = None
        if not month_cov.empty:
            raw_r = float(month_cov["RAW_ROWS_AFTER_SCOPE"].fillna(0).sum())
            mapped_r = float(month_cov["MAPPED_ROWS"].fillna(0).sum())
            month_row_cov = (mapped_r / raw_r) if raw_r else None
        matrix = vendor_check_matrix(
            month_detail,
            vendor_seats=vs,
            billing_seats=bs,
            partner_row_coverage=month_row_cov,
        )
        rev = float(row.get("TOTAL_BILLING_AMOUNT") or 0)
        cost = float(row.get("TOTAL_VENDOR_AMOUNT") or 0)
        out_rows.append({
            "BILLING_MONTH": month,
            "VS": vs, "BS": bs, "REV": rev, "COST": cost,
            "STATUSES": tuple(matrix[k][0] for k, _ in CHECKS),
        })
    return pd.DataFrame(out_rows)


def render_monthly_recon_table(vendor_key: str) -> None:
    monthly = _monthly_recon_rows(vendor_key, _months_key(selected_month_ts_list), freshness)
    if monthly.empty:
        st.markdown('<div class="note">No monthly summary rows in the selected period.</div>', unsafe_allow_html=True)
        return

    header = '<tr><th>Month</th>'
    for _, label in CHECKS:
        header += f'<th class="c">{label}</th>'
    header += (
        '<th class="num">Vendor seats</th><th class="num">CW Billed</th>'
        '<th class="num">CW vs. Vendor</th><th class="num">CW revenue</th>'
        '<th class="num">Vendor cost</th><th class="num">Margin $</th>'
        '<th class="num">Margin %</th></tr>'
    )
    body = []
    for r in monthly.itertuples(index=False):
        vs, bs, rev, cost = r.VS, r.BS, r.REV, r.COST
        cw_vs_vendor_pct = ((bs - vs) / vs * 100) if vs else 0.0
        gm = rev - cost
        gm_pct = gm / rev if rev else 0
        cells = f'<tr><td><b>{month_label(r.BILLING_MONTH)}</b></td>'
        for s in r.STATUSES:
            cells += f'<td class="c">{chip_html(s)}</td>'
        cells += (
            f'<td class="num">{fmt_num(vs)}</td>'
            f'<td class="num">{fmt_num(bs)}</td>'
            f'<td class="num">{cw_vs_vendor_pct:+.1f}%</td>'
            f'<td class="num">{fmt_money(rev)}</td>'
            f'<td class="num">{fmt_money(cost)}</td>'
            f'<td class="num">{fmt_money(gm)}</td>'
            f'<td class="num">{gm_pct * 100:.1f}%</td></tr>'
        )
        body.append(cells)

    ytd_vs = float(monthly["VS"].sum())
    ytd_bs = float(monthly["BS"].sum())
    ytd_rev = float(monthly["REV"].sum())
    ytd_cost = float(monthly["COST"].sum())
    ytd_gm = ytd_rev - ytd_cost
    ytd_gm_pct = ytd_gm / ytd_rev if ytd_rev else 0
    ytd_cw_vs_vendor_pct = ((ytd_bs - ytd_vs) / ytd_vs * 100) if ytd_vs else 0.0
    body.append(
        '<tr style="font-weight:700;background:var(--cw-bg-3);color:var(--cw-text-0)"><td>YTD</td>'
        f'<td class="c" colspan="{len(CHECKS)}">-</td>'
        f'<td class="num">{fmt_num(ytd_vs)}</td>'
        f'<td class="num">{fmt_num(ytd_bs)}</td>'
        f'<td class="num">{ytd_cw_vs_vendor_pct:+.1f}%</td>'
        f'<td class="num">{fmt_money(ytd_rev)}</td>'
        f'<td class="num">{fmt_money(ytd_cost)}</td>'
        f'<td class="num">{fmt_money(ytd_gm)}</td>'
        f'<td class="num">{ytd_gm_pct * 100:.1f}%</td></tr>'
    )
    st.markdown(
        '<table class="recon"><thead>' + header + '</thead><tbody>'
        + "".join(body) + '</tbody></table>',
        unsafe_allow_html=True,
    )


def render_seat_trend(vendor_key: str) -> None:
    data = vendor_by_key(vendor_key)["data"]
    summary_all = data["summary"].sort_values("BILLING_MONTH")
    if selected_month_ts_list and len(selected_month_ts_list) < len(months_available):
        summary_all = summary_all[summary_all["BILLING_MONTH"].isin(selected_month_ts_list)]
    if summary_all.empty:
        return
    max_seats = float(
        max(
            summary_all["TOTAL_VENDOR_SEATS"].fillna(0).max(),
            summary_all["TOTAL_BILLING_SEATS"].fillna(0).max(),
            1,
        )
    )
    header = (
        '<tr><th>Month</th><th class="num">Vendor</th>'
        '<th class="num">CW Billed</th><th class="num">CW vs. Vendor</th><th style="width:46%">Trend</th></tr>'
    )
    body = []
    for _, row in summary_all.iterrows():
        vs = float(row.get("TOTAL_VENDOR_SEATS") or 0)
        bs = float(row.get("TOTAL_BILLING_SEATS") or 0)
        w_v = vs / max_seats * 100
        w_b = bs / max_seats * 100
        cw_vs_vendor = ((bs - vs) / vs) if vs else 0.0
        cw_vs_vendor_txt = f"{cw_vs_vendor * 100:+.1f}%"
        body.append(
            f'<tr><td><b>{month_label(row["BILLING_MONTH"])}</b></td>'
            f'<td class="num">{fmt_num(vs)}</td>'
            f'<td class="num">{fmt_num(bs)}</td>'
            f'<td class="num">{cw_vs_vendor_txt}</td>'
            f'<td><div class="bar" style="height:7px;margin-bottom:3px">'
            f'<span style="width:{w_v}%;background:var(--blue)"></span></div>'
            f'<div class="bar" style="height:7px">'
            f'<span style="width:{w_b}%;background:var(--green)"></span></div></td></tr>'
        )
    st.markdown(
        '<table class="recon"><thead>' + header + '</thead><tbody>'
        + "".join(body) + '</tbody></table>',
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=DATA_TTL_SECONDS, show_spinner=False)
def _load_vendor_invoice_usage_intra(
    vendor_name: str,
    months_key: str,
    freshness_: str,
) -> pd.DataFrame:
    """Load the precomputed vendor-internal invoice-vs-usage control.

    The table is vendor/month/SKU grain upstream. The app keeps the Snowflake
    query narrow, then rolls the selected months up to SKU for display.
    Months without parsed invoice lines remain included so invoice-side fields
    stay NULL and clearly signal invoice absence for that period.
    """
    vendor_sql = str(vendor_name).replace("'", "''")
    if months_key:
        month_values = []
        for month in months_key.split("|"):
            ts = pd.to_datetime(month, errors="coerce")
            if pd.notna(ts):
                month_values.append(f"'{ts:%Y-%m-%d}'")
        month_sql = (
            f" AND BILLING_MONTH IN ({','.join(month_values)})"
            if month_values
            else ""
        )
    else:
        month_sql = ""

    df = upper_cols(_try_query(
        f"""
        SELECT
            VENDOR,
            BILLING_MONTH,
            SKU,
            VENDOR_INVOICE_SKU,
            VENDOR_USAGE_SKU,
            VENDOR_INVOICE_SEATS,
            VENDOR_RAW_USAGE_SEATS,
            VENDOR_INVOICE_AMOUNT,
            VENDOR_RAW_USAGE_AMOUNT,
            DELTA_SEATS,
            DELTA_AMOUNT,
            SOURCE_STATUS
                FROM {SCHEMA}.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD t
                WHERE t.VENDOR = '{vendor_sql}'{month_sql}
        ORDER BY BILLING_MONTH, ABS(DELTA_AMOUNT) DESC, ABS(DELTA_SEATS) DESC, SKU
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
    st.markdown("### Vendor Invoice vs. Vendor Raw Usage Files")
    st.caption(
        "Selected-period SKU rollup (one row per invoice/usage SKU combination). "
        "Delta = raw vendor usage minus parsed vendor invoice; invoice-side metric NULLs still mean no parsed invoice row exists for that vendor/month/SKU yet."
    )

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

    work = raw.copy()
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

    # Keep one display row per concrete SKU combination (no pipe-concatenated SKU lists).
    # Label fallbacks use SKU so rows stay attributable even when invoice/usage SKU fields are blank.
    work["VENDOR_INVOICE_SKU"] = work["VENDOR_INVOICE_SKU"].str.strip()
    work["VENDOR_USAGE_SKU"] = work["VENDOR_USAGE_SKU"].str.strip()
    work["INVOICE_SKU_DISPLAY"] = work["VENDOR_INVOICE_SKU"].where(
        work["VENDOR_INVOICE_SKU"].ne(""),
        work["SKU"],
    )
    work["USAGE_SKU_DISPLAY"] = work["VENDOR_USAGE_SKU"].where(
        work["VENDOR_USAGE_SKU"].ne(""),
        work["SKU"],
    )

    sku_rollup = (
        work.groupby(["SKU", "INVOICE_SKU_DISPLAY", "USAGE_SKU_DISPLAY"], dropna=False)
        .agg(
            **{
                "Vendor Invoice Seats": ("VENDOR_INVOICE_SEATS", _sum_preserve_null),
                "Vendor Raw Usage Seats": ("VENDOR_RAW_USAGE_SEATS", _sum_preserve_null),
                "Vendor Invoice Amount": ("VENDOR_INVOICE_AMOUNT", _sum_preserve_null),
                "Vendor Raw Usage Amount": ("VENDOR_RAW_USAGE_AMOUNT", _sum_preserve_null),
            }
        )
        .reset_index()
        .rename(
            columns={
                "INVOICE_SKU_DISPLAY": "Vendor Invoice SKU",
                "USAGE_SKU_DISPLAY": "Vendor Usage SKU",
            }
        )
    )
    sku_rollup["Delta Seats"] = (
        sku_rollup["Vendor Raw Usage Seats"].fillna(0)
        - sku_rollup["Vendor Invoice Seats"].fillna(0)
    )
    sku_rollup["Delta Amount"] = (
        sku_rollup["Vendor Raw Usage Amount"].fillna(0)
        - sku_rollup["Vendor Invoice Amount"].fillna(0)
    )
    sku_rollup["_abs_delta_amount"] = sku_rollup["Delta Amount"].abs()
    sku_rollup["_abs_delta_seats"] = sku_rollup["Delta Seats"].abs()
    sku_rollup = sku_rollup.sort_values(
        ["_abs_delta_amount", "_abs_delta_seats", "SKU", "Vendor Invoice SKU", "Vendor Usage SKU"],
        ascending=[False, False, True, True, True],
    ).drop(columns=["_abs_delta_amount", "_abs_delta_seats"])

    total = {
        "SKU": "TOTAL",
        "Vendor Invoice SKU": "",
        "Vendor Usage SKU": "",
        "Vendor Invoice Seats": _sum_preserve_null(sku_rollup["Vendor Invoice Seats"]),
        "Vendor Raw Usage Seats": _sum_preserve_null(sku_rollup["Vendor Raw Usage Seats"]),
        "Vendor Invoice Amount": _sum_preserve_null(sku_rollup["Vendor Invoice Amount"]),
        "Vendor Raw Usage Amount": _sum_preserve_null(sku_rollup["Vendor Raw Usage Amount"]),
    }
    total["Delta Seats"] = (
        (0 if pd.isna(total["Vendor Raw Usage Seats"]) else total["Vendor Raw Usage Seats"])
        - (0 if pd.isna(total["Vendor Invoice Seats"]) else total["Vendor Invoice Seats"])
    )
    total["Delta Amount"] = (
        (0 if pd.isna(total["Vendor Raw Usage Amount"]) else total["Vendor Raw Usage Amount"])
        - (0 if pd.isna(total["Vendor Invoice Amount"]) else total["Vendor Invoice Amount"])
    )
    display = pd.concat([sku_rollup, pd.DataFrame([total])], ignore_index=True)

    for col in ["Vendor Invoice Seats", "Vendor Raw Usage Seats", "Delta Seats"]:
        display[col] = display[col].map(lambda v: "" if pd.isna(v) else fmt_num(float(v)))
    for col in ["Vendor Invoice Amount", "Vendor Raw Usage Amount", "Delta Amount"]:
        display[col] = display[col].map(lambda v: "" if pd.isna(v) else fmt_money(float(v)))

    st.dataframe(
        display[
            [
                "SKU",
                "Vendor Invoice SKU",
                "Vendor Usage SKU",
                "Vendor Invoice Seats",
                "Vendor Raw Usage Seats",
                "Vendor Invoice Amount",
                "Vendor Raw Usage Amount",
                "Delta Seats",
                "Delta Amount",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        height=360,
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

    Currently keyed off VENDOR_PRODUCT since that's the only vendor
    with a mapped SKU column in the POC. When additional vendors land a
    <VENDOR>_PRODUCT column, extend the fallback list below.
    """
    detail = slice_.detail
    if detail.empty:
        return
    sku_col = next(
        (c for c in ("VENDOR_PRODUCT",) if c in detail.columns),
        None,
    )
    if sku_col is None:
        return

    st.markdown(f"### {slice_.name} profitability by SKU")
    st.caption(
        "Totals across all months in scope. Use this to isolate mix shift "
        "(share of revenue changing between SKUs) from rate movement "
        "(margin % changing within a SKU)."
    )

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
    exposed["BILLING_MONTH"] = pd.to_datetime(
        exposed["BILLING_MONTH"], errors="coerce"
    ).dt.strftime("%Y-%m")

    keep_cols = [c for c in [
        "SF_ID", "BILLING_MONTH", "VENDOR_PARTNER_NAME", "VENDOR_PRODUCT",
        "CONTRACT_COST_RATE", "VENDOR_UNIT_PRICE", "VENDOR_VS_CONTRACT_PCT",
        "VENDOR_QUANTITY", "VENDOR_VS_CONTRACT_DOLLAR_IMPACT",
        "VENDOR_VS_CONTRACT_FLAG",
    ] if c in exposed.columns]
    disp = exposed[keep_cols].rename(columns={
        "SF_ID": "Salesforce ID",
        "BILLING_MONTH": "Month",
        "VENDOR_PARTNER_NAME": "Partner",
        "VENDOR_PRODUCT": "Product",
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

    st.dataframe(disp, use_container_width=True, hide_index=True)


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
    below["BILLING_MONTH"] = pd.to_datetime(
        below["BILLING_MONTH"], errors="coerce"
    ).dt.strftime("%Y-%m")
    _cols = [c for c in [
        "SF_ID", "BILLING_MONTH", "VENDOR_PARTNER_NAME", "VENDOR_PRODUCT",
        "CONTRACT_COST_RATE", "BILLED_UNIT_PRICE", "PCT_DISCOUNT",
        "TOTAL_BILLING_QUANTITY", "BILLING_VS_COST_DOLLAR_IMPACT", "EXCEPTION_TYPE",
    ] if c in below.columns]
    disp = below[_cols].rename(columns={
        "SF_ID": "Salesforce ID",
        "BILLING_MONTH": "Month",
        "VENDOR_PARTNER_NAME": "Partner",
        "VENDOR_PRODUCT": "Product",
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
            _vendor_str = queue_source.get(
                "_VENDOR", pd.Series("", index=queue_source.index)
            ).fillna("").astype(str)
            _sf_str = queue_source.get(
                "SF_ID", pd.Series("", index=queue_source.index)
            ).fillna("").astype(str)
            _prod_str = queue_source.get(
                "VENDOR_PRODUCT", pd.Series("", index=queue_source.index)
            ).fillna("").astype(str)
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
            grouped = (
                queue_source.groupby("Case ID", dropna=False)
                .agg({
                    "_VENDOR": "first",
                    "SF_ID": "first",
                    "VENDOR_PARTNER_NAME": "first",
                    "VENDOR_PRODUCT": "first",
                    "BILLING_MONTH": "first",
                    "Exception Type": "first",
                    "Est $ Impact": "sum",
                    "Seat Variance": "sum",
                })
                .reset_index()
            )
            grouped = grouped.merge(_flag_by_case.reset_index(), on="Case ID", how="left")
            grouped = grouped.sort_values("Est $ Impact", ascending=False).reset_index(drop=True)
            grouped["Priority"] = grouped.index + 1
            grouped["Billing Month"] = pd.to_datetime(
                grouped["BILLING_MONTH"], errors="coerce"
            ).dt.strftime("%Y-%m")

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
                "SF_ID": "Salesforce ID",
                "VENDOR_PARTNER_NAME": "Partner",
                "VENDOR_PRODUCT": "Product",
            })[[
                "Priority", "Vendor", "Salesforce ID", "Partner", "Product",
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
                    "Salesforce ID": st.column_config.TextColumn("Salesforce ID", disabled=True),
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


with tab_vendor:
    # ------------------------------------------------------------------
    # Vendor picker for the Deep Dive tab. Independent of the global
    # multiselect so the analyst can inspect any vendor without changing
    # the portfolio-wide filters. Defaults to the first globally selected
    # vendor for continuity across tabs.
    # ------------------------------------------------------------------
    _dd_vendor_names = [v["name"] for v in active_vendors]
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
            st.dataframe(below_display_df, use_container_width=True, hide_index=True)

        render_vendor_rate_audit(active_slice)

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
    f'Pipeline last refreshed (EST): {fmt_est_timestamp(freshness)}</div>',
    unsafe_allow_html=True,
)

