# Cleanup & Handoff Checklist — Reconciliation Rebuild (Final Project Area)

**Target folder:** SharePoint → `Data Services - Internal / Third_Party_Recon / Reconciliation Rebuild`
**Prepared:** 2026-07-15 · Nate Fold, FP&A

> I can't write into SharePoint or OneDrive directly (tool limit — file uploads are
> blocked), so the 17 current files are staged in your **OneDrive → Documents/Cowork**
> folder. This checklist makes the folder current in ~3 minutes: delete 3, copy 17
> (replace 4), keep 3.

---

## STEP 1 — DELETE these stale files from the folder (3)
- `Engineering Ingestion Spec.md`  → superseded by `ENGINEERING_MONTHLY_INGESTION_MANIFEST.md`
- `README - Reconciliation Rebuild.md`  → superseded by `00_MASTER_REFERENCE_Third_Party_Recon_Mappings.md`
- `Recon Unified Mapping Tables 2026.xlsx`  → duplicate of the 4 dated CSVs

## STEP 2 — COPY IN from Cowork (17 files; choose *Replace* for the 4 CSVs marked ↺)

**Mappings / seeds (current, dated):**
- ↺ `RECON_PARTNER_MAP.csv` (7,228 — now dated)
- ↺ `RECON_SKU_TO_PRODUCT_MAP.csv` (481 — now dated)
- ↺ `RECON_SKU_TO_SKU_MAP.csv` (322 — now dated)
- ↺ `RECON_CONTRACT_PRICING_MAP.csv` (1,273 — now dated)
- `RECON_KEEPIT_CHARGE_MAP.csv` (new — KeepIT scope, 228,085 target)
- `RECON_AUVIK_CMS_QTY_RULE.csv` (new — committed/overage rule)
- `RECON_KASEYA_MODULE_MAP.csv` (new — module↔flag↔price, 621 target)
- `RECON_MARKETPLACE_SUPPRESSION.csv` (new — G6 vendor list)
- `RECON_CONTRACT_PRICE_ADDENDUM.csv` (new — Kaseya + KeepIT governed rates)
- `RECON_VENDOR_NAME_NORMALIZATION.csv` (rebuilt — spelling rules)
- `RECON_AUVIK_PATTERN_MAP.csv` (Auvik family classifier)
- `RECON_EXCLUSION_SIGNALS.csv` (review accounts)

**Documentation (current):**
- `00_MASTER_REFERENCE_Third_Party_Recon_Mappings.md` (the source-of-truth index)
- `GAP_CLOSURE_KEEPIT_AUVIK_KASEYA_2026-07-15.md` (the 3 final gap fixes)
- `CORTEX_CLOSEOUT_PROMPT.md` (paste to Cortex to finalize)
- `ENGINEERING_MONTHLY_INGESTION_MANIFEST.md` (monthly ingest spec)
- `README_DATED_SEED_FILES.md` (seed data dictionary)

## STEP 3 — KEEP (already in the folder, still current) (3)
- `Manual Reconciliation Process - Reverse-Engineered.docx`
- `Unified Vendor Intake Template.xlsx`
- `Engineering Ingestion Manifest.xlsx`

---

## Final folder = 20 files (17 copied + 3 kept)

## STEP 4 — Hand to Cortex
Paste `CORTEX_CLOSEOUT_PROMPT.md` to Cortex. It will: load the seeds, bake in the 3 fixes
(KeepIT 228,085 / Auvik CMS 34,007 / Kaseya 621), apply Marketplace suppression, backfill
2025-01→2026-06 coverage from the Snowflake billing base, validate parity across all 17
vendors, and — once green — begin `RECON_EXCEPTION_RULE_MASTER` (business-rule extraction).
