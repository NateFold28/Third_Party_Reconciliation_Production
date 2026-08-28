# Third-Party Partner Map + Merge-Date Fix Results (2026-08-26)

## What was implemented

### 1) Unified merged-account resolver in map layer
- Added `RECON_ACCOUNT_MERGE_RESOLVER` built from `ANALYTICS.DBO.CW_DW__MERGED_ACCOUNT_MAP`.
- Resolver is recursive chain-aware (`OLD -> NEW -> NEW...`) and records:
  - `old_sf_id`
  - `canonical_sf_id`
  - `merge_effective_ts`
  - `merge_effective_month`
  - `resolver_depth`

### 2) Effective-dated partner map
- Updated `RECON_PARTNER_MAP` to store:
  - canonical `SF_ID`
  - `RAW_SF_ID`
  - `SF_ID_SOURCE`
  - merge effective timestamp/month
- Added `RECON_PARTNER_MAP_MONTHLY` with month-aware SF_ID logic:
  - if `billing_month < merge_effective_month` -> use `RAW_SF_ID`
  - else -> use canonical `SF_ID`

### 3) Vendor SQL patches for month-aware partner mapping + billing-only partner backfill
- Patched:
  - `Auvik_Reconciliation_Script_Prod.sql`
  - `Exium_Reconciliation_Script_Prod.sql`
  - `Webroot_Reconciliation_Script_Prod.sql`
- Changes:
  - partner-name resolution now joins `RECON_PARTNER_MAP_MONTHLY` by `billing_month`
  - billing-only rows now backfill partner labels from:
    1. `RECON_PARTNER_MAP` by `SF_ID`
    2. Salesforce account name fallback

### 4) Rebuilt pipeline outputs
- Re-ran:
  - map build SQL
  - all live vendor reconciliation SQL via skeleton pipeline
  - `THIRD_PARTY_RECON_OUTPUT_PROD` + `THIRD_PARTY_RECON_SUMMARY_PROD`

## Impact (before vs after)

### Output-level partner hygiene
- `NULL/blank VENDOR_PARTNER_NAME` rows:
  - before: **4,241**
  - after: **301**
  - improvement: **-3,940 rows (-92.9%)**

- `NULL partner + SF_ID present`:
  - before: **4,238**
  - after: **298**
  - improvement: **-3,940 rows**

- `Pipe-delimited partner name` rows:
  - before: **412**
  - after: **412**
  - change: **no change** (expected; this is alias-display normalization, not null-backfill)

### Vendor-level null partner deltas
- Webroot: `2937 -> 7`
- Auvik: `595 -> 12`
- Exium: `435 -> 0`
- Bitdefender: `256 -> 258` (essentially flat)
- SentinelOne: `0 -> 6` (small increase from refreshed run, unrelated to targeted null-backfill path)

### Partner map many-to-many (partner name -> multiple SF_ID)
- before: **50**
- after: **47**
- Remaining 47 are all **manual review required** (not auto-resolvable by merged-account map).
- Extract saved: `logs/remaining_partner_multi_sf_after_fix_20260826.csv`

## Interpretation
- The merge-date/effective-month logic is now implemented and active.
- The major queue noise source (null partner labels) was materially reduced.
- Remaining partner->multi-SF conflicts are true curation decisions, not stale merged-account artifacts.

## Remaining work recommendations
1. Curate the 47 remaining partner-key conflicts in `remaining_partner_multi_sf_after_fix_20260826.csv`.
2. Add optional canonical display layer to collapse pipe-delimited partner names for queue UX.
3. Apply the same month-aware map + sf_id label backfill pattern to any remaining vendor scripts that still depend on usage-side partner labels for billing-only rows.
