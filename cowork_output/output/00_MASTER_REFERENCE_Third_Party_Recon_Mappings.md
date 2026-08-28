# 00 — MASTER REFERENCE — Third-Party Recon Mappings (Cortex Source of Truth)

**Owner:** Nate Fold, FP&A · **Updated:** 2026-07-15 · **Basis:** manual recon workbooks
**Coverage target:** 2025-01 → 2026-06 (see §Coverage) · **Status:** 17/17 vendors have mapping logic

This is the single reference for the governed mapping/seed layer. Everything below is
reverse-engineered from the manual team's workbooks and validated to their output.

---

## Seed files (load as dbt seeds / COPY INTO)

| File | Rows | Purpose |
|---|--:|---|
| `RECON_PARTNER_MAP.csv` | 7,228 | vendor partner → SF account / CMS id, dated (valid_from/valid_to/is_current) |
| `RECON_SKU_TO_PRODUCT_MAP.csv` | 481 | vendor SKU/product → CW product family, dated |
| `RECON_SKU_TO_SKU_MAP.csv` | 322 | vendor SKU → CW SKU bridge, dated |
| `RECON_CONTRACT_PRICING_MAP.csv` | 1,273 | observed vendor unit rates, dated |
| `RECON_KEEPIT_CHARGE_MAP.csv` | 24 | **NEW** — Zuora charge-name → KI family, in-scope flag |
| `RECON_AUVIK_CMS_QTY_RULE.csv` | 3 | **NEW** — committed/overage quantity derivation rule |
| `RECON_KASEYA_MODULE_MAP.csv` | 8 | **NEW** — product ↔ CTM flag ↔ price ↔ billed count |
| `RECON_MARKETPLACE_SUPPRESSION.csv` | 13 | **NEW** — G6 "on Marketplace = do nothing" vendor list |
| `RECON_CONTRACT_PRICE_ADDENDUM.csv` | 13 | **NEW** — governed Kaseya + KeepIT rates |
| `RECON_AUVIK_PATTERN_MAP.csv` | 143 | (existing) Auvik family classifier |

Dated seeds use SCD2 columns: `valid_from`, `valid_to` (9999-12-31 = current),
`is_current`, `months_observed`, `has_month_gap`, `data_window_*`.

---

## The 3 gap closures (final — tie exactly to manual May 2026)

### KeepIT — charge-name scope → 228,085 units
The manual `Vendor SKU` tag column in `KeepIT Recon May'26.xlsx` defines scope.
**Include** base-seat charges across 4 prefixes (`CW RMM`, `ConnectWise`, `M2M RMM`,
`M2M-ConnectWise`) for M365 / Azure AD Advanced / Google / Dynamics / Salesforce.
**Exclude** `Unlimited Retention Add-on` (add-on, not a seat) and every
`Recover SaaS 3-year Promo` / `3-year Promo` line (reconciled in the separate Promo file).
Full list in `RECON_KEEPIT_CHARGE_MAP.csv`.

### Auvik CMS — committed device count → 34,007 (32,637 Billable + 1,370 Perf)
Committed count is the native `Invoice Item: Quantity` on the `(Recurring)…Fixed Price`
line (Zuora stores N there). CW qty = SUM(Quantity) over Recurring + Usage lines, split by
family via `RECON_AUVIK_PATTERN_MAP`. Do NOT parse package names; do NOT read Included Units.

### Kaseya — member-per-module (not UserCount) → 621
Reconcile at member × module grain using RFT `CTM_*` flags. Billed count per product =
count of members with flag = Yes. UserCount (10,420) is not a billing metric. Base (945
members, CTM_NA) is bundled / not separately billed. Full map + prices in
`RECON_KASEYA_MODULE_MAP.csv`.

---

## Global rules (already applied; here for reference)
- CW side = Zuora "Productwise": `STATUS='Posted' AND SOURCE='BillRun'`; active accounts only.
- Marketplace: `ISDELTA=FALSE`; if partner on Marketplace → no action (`RECON_MARKETPLACE_SUPPRESSION.csv`).
- Acronis: only SKUs starting with 'S'. Auvik: negative lines = free/discount (subtract).
- Auvik CW bills in advance; Auvik CMS current month. Webroot cycle = 15th–14th.

---

## Coverage (2025 + 2026) — how to make it real
The current dated seeds carry **observed evidence from 2026 H1** (plus the structural
rules above, which do not vary by month). The 2025 monthly source files exist and use the
**same per-vendor structure** (12 folders `01_JAN_2025`…`12_DEC_2025`).

**Authoritative way to extend to 2025-01 → 2026-06:** derive month-presence from the
Snowflake billing base (`ZUORA_THIRD_PARTY_RECON_BASE`, 2.4M rows spanning these months)
rather than re-parsing spreadsheets — observe each mapping key across every billing month
and set `valid_from` / `valid_to` / `months_observed` accordingly. This is a Cortex step
(see the closeout prompt) and is more accurate than a spreadsheet re-parse. A dedicated
spreadsheet backfill can be run separately if a file-side cross-check is wanted.

**Do not** widen `valid_from` to 2025 for a key without observing it in a 2025 billing
month — no fabricated history.

---

## Stale files to remove from this folder (superseded)
- `Engineering Ingestion Spec.md` → superseded by `ENGINEERING_MONTHLY_INGESTION_MANIFEST.md`
- `CORTEX_FINDINGS_2026-07-15.md`, `CORTEX_FULL_VALIDATION_2026-07-15.md`,
  `CORTEX_PRODUCTION_VALIDATION_2026-07-15.md` → superseded by `CORTEX_FINAL_STATUS_2026-07-15.md`
- `RECON_VENDOR_NAME_NORMALIZATION.csv` (near-empty stub) → fold into `RECON_VENDOR_NAME_NORMALIZATION` proper or delete
- `Recon Unified Mapping Tables 2026.xlsx` → duplicate of the 4 dated CSVs
