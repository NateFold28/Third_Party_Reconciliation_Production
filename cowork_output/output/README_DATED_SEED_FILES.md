# Third-Party Reconciliation — Dated Seed Files

**Prepared by:** Nate Fold, FP&A · **Generated:** 2026-07-15
**Purpose:** Snowflake seed tables (source of truth) for the third-party vendor
reconciliation mapping layer, with month-level temporal validity so mappings
resolve correctly between file months.

## What changed vs. the prior seeds
These files **replace** the prior four `RECON_*` seeds (same filenames). The prior
versions were a single `load_month = 2026-05` snapshot: each row carried a
free-text `months_present` field (mixed delimiters/casing) but no usable date
range. These versions convert that observed history into best-practice **SCD Type
2** validity so engineering can join a mapping to the correct billing month — the
fix for *"do the mappings change by month… add a start and end date so we can map
them correctly between the file months."*

The clearest example is contract pricing: Acronis SKU `SBATFNLOS` was one row at a
blended price before; it is now two dated rows — `0.67` valid `2026-01-01 →
2026-02-28`, then `0.6745` valid `2026-03-01 → current`.

## The four seed files
| File | Grain / natural key | Business columns |
|---|---|---|
| `RECON_PARTNER_MAP.csv` | vendor + vendor_partner_name | cms_id, sf_account_number, zuora_name, parent_co, account_status, join_key_type |
| `RECON_SKU_TO_PRODUCT_MAP.csv` | vendor + vendor_sku_or_product | product_family, cw_product_or_charge, reconciliation_grain |
| `RECON_SKU_TO_SKU_MAP.csv` | vendor + vendor_sku | cw_sku, cw_charge_name, mapping_method |
| `RECON_CONTRACT_PRICING_MAP.csv` | vendor + vendor_sku_or_product + unit_price | unit_price, currency, uom, price_basis, source_doc, notes |

## Shared temporal columns (added to every file)
| Column | Meaning |
|---|---|
| `map_row_id` | Stable per-file row identifier (surrogate key for the seed) |
| `valid_from` | First day of the earliest month the mapping was observed (`YYYY-MM-DD`) |
| `valid_to` | Last day of the latest observed month, **or `9999-12-31` if still current** |
| `is_current` | `TRUE` when the mapping is present in that vendor's latest data month |
| `months_observed` | Normalized list of months seen, e.g. `2026-01;2026-02;2026-05` |
| `observed_month_count` | Number of months the mapping appeared in |
| `has_month_gap` | `TRUE` when observed months are **not** contiguous (review candidate) |
| `data_window_start` / `data_window_end` | Observation window these dates were derived from |
| `seed_generated_on` | Build date |

## Dating logic
- `valid_from` = first-of-month of the earliest observed month.
- `valid_to` = end-of-month of the latest observed month, **unless** the mapping is
  still present in that vendor's most recent data month → then it is left **open**
  (`9999-12-31`, `is_current = TRUE`).
- A mapping that dropped off before its vendor's latest month is **closed out**
  (treated as expired/churned for that key) rather than silently dropped.
- Non-contiguous months are **flagged** (`has_month_gap = TRUE`), never merged or
  hidden — so the recon team can confirm whether it's true intermittency or a data gap.

## Assumptions & scope (please confirm)
1. **Year = 2026.** The source months are unlabeled (`JAN…JUN`); every project
   artifact (tracker "2026 File", ingestion spec "validated against May-2026",
   `load_month = 2026-05`) places them in **2026 H1**. Dates are stamped `2026-01`
   through `2026-06` accordingly.
2. **Coverage = 2026 Jan–Jun**, because that is the window the reverse-engineered
   maps actually cover. The raw archive goes back to 2021; extending the date
   history to prior years requires parsing those monthly workbooks and is a
   follow-on (not fabricated here).
3. `is_current` uses each vendor's own latest observed month as the "high-water
   mark," since vendors have different data depths.

## Best-practice notes for ingestion
- Load with `COPY INTO` or as a dbt seed; treat `map_row_id` as the seed PK.
- To resolve a mapping for month *M*: filter
  `valid_from <= M_end AND valid_to >= M_start`.
- `9999-12-31` is the open-ended sentinel; swap for `NULL` if your model prefers it.
