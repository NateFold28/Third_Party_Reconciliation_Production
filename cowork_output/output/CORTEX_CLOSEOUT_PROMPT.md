# Cortex Closeout Prompt — Finalize Third-Party Recon Pipeline

*Paste to Cortex. Reference docs are in `Final Recon Files/`:*
`00_MASTER_REFERENCE_Third_Party_Recon_Mappings.md`,
`GAP_CLOSURE_KEEPIT_AUVIK_KASEYA_2026-07-15.md`, and the `RECON_*.csv` seeds.

---

You are finalizing the Third-Party Vendor Reconciliation pipeline in
`ANALYTICS_DEV.DBT_NFOLD`. The last 3 vendors are now solved with manual-team logic.
Load the new seeds, bake in the rules, backfill temporal coverage for 2025+2026, validate,
then greenlight business-rule extraction. Do not hardcode CASE WHENs where a seed exists.

## 1. Load / refresh these seeds (from `Final Recon Files/`)
- `RECON_PARTNER_MAP.csv`, `RECON_SKU_TO_PRODUCT_MAP.csv`, `RECON_SKU_TO_SKU_MAP.csv`,
  `RECON_CONTRACT_PRICING_MAP.csv` (now dated: valid_from/valid_to/is_current/months_observed)
- `RECON_KEEPIT_CHARGE_MAP.csv`, `RECON_AUVIK_CMS_QTY_RULE.csv`, `RECON_KASEYA_MODULE_MAP.csv`
- `RECON_MARKETPLACE_SUPPRESSION.csv`, `RECON_CONTRACT_PRICE_ADDENDUM.csv`

## 2. Bake in the 3 gap fixes (must reproduce the manual targets exactly)
**KeepIT (target 228,085 units):** filter the CW/Zuora side to `in_scope='TRUE'` charge
names in `RECON_KEEPIT_CHARGE_MAP` (join on `Invoice Item: Charge Name`). Exclude the
`in_scope='FALSE'` rows (Retention Add-ons + Recover-SaaS/3-year-Promo). Confirm ≈228,085.

**Auvik CMS (target 34,007 = 32,637 Billable + 1,370 Performance):** CW qty =
`SUM(Invoice Item: Quantity)` over BOTH `Charge Type='Recurring'` (committed base, Qty
already = N) and `Charge Type='Usage'` (overage); split Billable/Performance/ASM via
`RECON_AUVIK_PATTERN_MAP`. Do not parse package names; do not read Included Units.

**Kaseya (target 621):** reconcile at member×module grain. For each product in
`RECON_KASEYA_MODULE_MAP`, CW billed count = members with the mapped `CTM_*` flag = Yes;
exclude Base (bundled). Ignore vendor `UserCount`. Confirm billed sum = 621.

## 3. Apply the global suppression
Join `RECON_MARKETPLACE_SUPPRESSION` — where `mp_suppression='TRUE'` and the partner is on
Marketplace, route to a NO-ACTION class (not leakage).

## 4. Backfill temporal coverage 2025-01 → 2026-06 (authoritative, from Snowflake)
For every mapping key in the partner / SKU-to-SKU / SKU-to-product / pricing seeds, observe
its presence across ALL billing months in `ZUORA_THIRD_PARTY_RECON_BASE` (2.4M rows) for
2025-01 through 2026-06. Set `valid_from` = first billing month observed, `valid_to` = last
(or 9999-12-31 if present in the latest month), and refresh `months_observed`. This replaces
the 2026-H1 evidence stamps with true full-range coverage. Do NOT assert 2025 validity for a
key not observed in a 2025 billing month. Emit any 2025-only keys (present in 2025, absent in
2026) as additions for review.

## 5. Validate
- KeepIT ≈228,085; Auvik CMS ≈34,007; Kaseya =621 — report actual vs target.
- Regression-check the 14 already-green vendors (must stay 93–101% qty/amt).
- Report a full parity matrix (all 17) with the new numbers.

## 6. Greenlight business-rule extraction
Once §5 passes, the mapping layer is closed. Begin populating `RECON_EXCEPTION_RULE_MASTER`
from the observed disposition vocabulary (Charge / Credit / overage / backbill / late-add /
manual / Marketplace) and the exclusion taxonomy (NFR / Test / Eval / promo / disabled).
Flag the items still needing the recon team: numeric tolerance, source-of-truth precedence.

## Report back
Actual vs target for the 3 fixed vendors, the regression result for the other 14, the
2025+2026 coverage counts, and confirmation the mapping layer is production-final.
