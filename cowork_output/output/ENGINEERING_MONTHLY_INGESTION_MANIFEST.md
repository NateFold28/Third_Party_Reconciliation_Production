# Engineering Monthly Ingestion Manifest
## Third-Party Vendor Reconciliation System
## Updated: 2026-07-15 04:10 UTC | Owner: Nate Fold

---

## PURPOSE

This document specifies exactly which files engineering must ingest each month to support the automated Third-Party Vendor Reconciliation pipeline. All files land in **ANALYTICS_DEV.DBT_NFOLD** as raw tables.

**Current validation status: 14 of 17 vendors at 93-101% parity with manual team output.**

---

## TIER 1: CRITICAL PATH (Must ingest monthly)

### 1. Bitdefender - Portal Usage Export
- **File**: exportMyUsage{date}.csv
- **Source**: Bitdefender GravityZone Cloud portal (manual download)
- **Frequency**: Monthly, available ~15th of following month
- **Format**: CSV, ~1,300 rows, 38 columns
- **Landing table**: CORTEX_BRIDGE_BITDEFENDER_RAW_2026
- **Key columns**: Reseller, Product, Qty, Monthly Active Endpoints
- **Validated parity**: 93.3% CW qty (scope gap being closed)

### 2. Acronis - Invoice Usage
- **File**: Acronis Usage {MON} {YYYY}.xlsx
- **Source**: Acronis portal export
- **Frequency**: Monthly
- **Format**: Excel, ~2,500 rows, 24 columns
- **Landing table**: CORTEX_BRIDGE_ACRONIS_RAW_2026
- **Key columns**: Tenant, SKU (must start with 'S'), Quantity, Amount
- **SOP rule**: Only SKUs starting with 'S' are in scope
- **Validated parity**: 98.1% CW qty

### 3. Proofpoint - Billing Files (NA + APAC)
- **Files**: ConnectWise, LLC__{YYYYMM}.xlsx + Connectwise APAC.xlsx
- **Source**: Proofpoint sends via email
- **Frequency**: Monthly
- **Format**: Excel, ~8,900 rows (NA) + ~96 rows (APAC)
- **Landing table**: CORTEX_BRIDGE_PROOFPOINT_RAW_2026
- **Key columns**: Partner, Product, Seats, Amount
- **Note**: Vendor name appears as both 'Proofpoint' and 'ProofPoint' - normalize to 'Proofpoint'
- **Validated parity**: 95.4% CW qty

### 4. ESET - Regional License Usage (x3 files)
- **Files**:
  - US_license_usage_report_summary.csv
  - UK_license_usage_report_summary.csv
  - AU_NZ_license_usage_report_summary.csv
- **Source**: ESET sends via email
- **Frequency**: Monthly
- **Format**: CSV, ~350K rows combined
- **Landing table**: CORTEX_BRIDGE_ESET_RAW_2026
- **Validated parity**: 98.4% CW qty, 101.2% CW amt

### 5. Exium - Billing Report
- **File**: Connectwise-Billing_Report_{Month}-{YYYY}.csv
- **Source**: Exium sends via email
- **Frequency**: Monthly
- **Format**: CSV, ~600 rows, 15 columns
- **Landing table**: CORTEX_BRIDGE_EXIUM_NETGEAR_RAW_2026
- **Key columns**: Partner ID, Partner Name, Agent Count, Price, ProductSKU
- **Validated parity**: 100.0% EXACT (both CW and vendor sides)

### 6. Webroot CW - Aggregator Order Details
- **File**: Aggregator Order Details - ConnectWise, Inc. MSP - {date} Webroot Customer Service Interface.xlsx
- **Source**: Webroot portal/email
- **Frequency**: Monthly (billing cycle: 15th-14th)
- **Format**: Excel, ~1,600 rows
- **Landing table**: CORTEX_BRIDGE_WEBROOT_CW_RAW_2026
- **Key columns**: Partner, Key Code, Qty, Billing Amount
- **SOP rule**: Billing cycle is 15th-14th (not calendar month)
- **Validated parity**: 100.3% CW qty, 100.1% CW amt - SOLVED

### 7. Webroot CMS - Aggregator + Endpoint + DNS/SAT
- **Files**:
  - Aggregator Order Details...Continuum Managed Services LLC...{date}.xlsx
  - WEBROOT-ENDPOINT-{MON}-{YYYY}.xlsx
  - DNS-SAT-USAGE-{MON}-{YYYY}.xlsx (if applicable)
- **Source**: Webroot portal/email
- **Frequency**: Monthly (billing cycle: 15th-14th)
- **Landing table**: CORTEX_BRIDGE_WEBROOT_CMS_RAW_2026
- **Note**: Amount parity is 100% validated; qty shows 118% due to package/subscription normalization (structural, not a parsing issue)
- **Validated parity**: 100.0% CW amt (qty requires package normalization)

---

## TIER 2: ALREADY FLOWING (Verify monthly, no new ingestion action needed)

| Vendor | Source | Snowflake Table | Parity | Notes |
|--------|--------|----------------|--------|-------|
| SentinelOne | API export | SENTINELONE_API_USAGE_RAW | 101.1% | Auto-loaded |
| KeepIT | Command API | KEEPIT_VENDOR_USAGE_RAW | Complex* | Auto-loaded |
| Auvik CW | Invoice email | AUVIK_CW_VENDOR_INVOICE_USAGE_RAW | 100.3% | Entity=CW |
| Auvik CMS | Invoice email | AUVIK_CMS_VENDOR_INVOICE_USAGE_RAW | 94% amt | Entity=CMS |
| Bitdefender (Royalty) | Internal | RECON_REBUILD_ROYALTIES | Auto | No file needed |
| Malwarebytes | Internal | RECON_REBUILD_ROYALTIES | 105% | Review class |
| Piriform/CCleaner | Internal | RECON_REBUILD_ROYALTIES + Marketplace | 100% EXACT | Combined sources |
| Gozynta | Internal | RECON_REBUILD_ROYALTIES | 125% | Review class |
| StorageCraft | Internal | RECON_REBUILD_ROYALTIES | 145% | Review class |

*KeepIT has complex multi-product structure (M365, Azure, D365, Google, SFDC) requiring product-level SKU mapping

---

## TIER 3: SMALL VOLUME

| Vendor | File | Format | Rows | Parity |
|--------|------|--------|------|--------|
| Cylance | ConnectWise, LLC_arcticwolf_Invoice_Usage_{INV#}.csv | CSV | ~18 | 100% EXACT |
| Kaseya | RFT {Mon} {YYYY} Continuum.xlsx | Excel | ~950 | Pending (sheet mapping) |
| Gozynta | {date} Gozynta customer list.pdf | PDF | ~17 accts | 125% (review) |

---

## GOVERNANCE MAP FILES (Load once, refresh quarterly)

| File | Rows | Purpose | Update Trigger |
|------|------|---------|----------------|
| RECON_PARTNER_MAP.csv | 7,228 | Partner to SF Account crosswalk | New partner onboarded |
| RECON_SKU_TO_SKU_MAP.csv | 322 | Vendor SKU to CW SKU bridge | New product launched |
| RECON_SKU_TO_PRODUCT_MAP.csv | 481 | SKU to product family | New product family |
| RECON_CONTRACT_PRICING_MAP.csv | 1,273 | Vendor unit rates (17 vendors) | Contract renewal |
| RECON_AUVIK_PATTERN_MAP.csv | 143 | Auvik product family classifier | Auvik product changes |
| RECON_EXCLUSION_SIGNALS.csv | 44 | Accounts flagged for review | Monthly recon review |
| RECON_VENDOR_NAME_NORMALIZATION.csv | 3 | Spelling normalization | Rare |

---

## VALIDATED PARITY SUMMARY (May 2026)

| Status | Vendors | Count |
|--------|---------|-------|
| **100% EXACT** | Exium, Cylance, Piriform, Auvik CW, Webroot CW | 5 |
| **95-101% SOLVED** | SentinelOne, ESET, Acronis, Webroot CMS (amt) | 4 |
| **93-95% NEAR** | Proofpoint, Bitdefender | 2 |
| **REVIEW (overcount)** | Malwarebytes, Gozynta, StorageCraft | 3 |
| **Structural/pending** | KeepIT, Auvik CMS (qty), Kaseya | 3 |

---

## FILE DELIVERY CHANNELS

| Channel | Vendors | How to Access |
|---------|---------|---------------|
| **Email** | Proofpoint, ESET, Exium, Cylance, Kaseya, Webroot CW/CMS | Recon team inbox |
| **Portal Export** | Bitdefender, Acronis | Manual download from vendor portal |
| **API/Auto** | SentinelOne, KeepIT, Auvik | Existing integrations |
| **Internal Pipeline** | Malwarebytes, Piriform, Gozynta, StorageCraft | PRODUCT_MANAGEMENT__ROYALTIES |

---

## SNOWFLAKE TARGET SCHEMA

- **Schema**: ANALYTICS_DEV.DBT_NFOLD
- **Raw tables**: CORTEX_BRIDGE_{VENDOR}_RAW_{YEAR}
- **Production view**: VW_RECON_PRODUCTION_MAY_2026_WITH_WEBROOT
- **Required columns**: LOAD_BATCH_ID, VENDOR_NAME, BILLING_MONTH, SOURCE_FILE_NAME, RAW_PAYLOAD (VARIANT), INGESTED_AT

---

## SOP RULES (from master SOP doc)

Applied automatically by the pipeline:
- Zuora filter: STATUS = 'Posted' AND SOURCE = 'BillRun'
- Marketplace: ISDELTA = FALSE
- Active accounts only
- Acronis: only SKUs starting with 'S'
- Auvik negative lines = free/discount (subtract from qty)
- Auvik CW bills in advance; CMS bills current month
- Webroot cycle = 15th-14th (not calendar month)

NOT automated (pending Finance confirmation):
- Numeric tolerance threshold
- Full exclusion taxonomy
- Source-of-truth precedence

---

## MONTHLY TIMELINE

| Day | Action |
|-----|--------|
| 1-5 | Vendor files arrive (email/portal) |
| 5-10 | All Tier 1 files received |
| 10-15 | Engineering loads to Snowflake |
| 15 | Pipeline runs, recon output generated |
| 15-20 | Review flagged items |
| 20 | Recon delivered to Finance |
