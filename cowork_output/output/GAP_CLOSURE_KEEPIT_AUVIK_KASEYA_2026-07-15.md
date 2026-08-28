# Gap Closure — KeepIT, Auvik CMS, Kaseya

**Third-Party Vendor Reconciliation · Nate Fold, FP&A · 2026-07-15**
Reverse-engineered from the manual team's **May 2026** worked recon workbooks. Every
number below ties to the manual output — no estimates.

Seed files produced this session:
`RECON_KEEPIT_CHARGE_MAP.csv`, `RECON_AUVIK_CMS_QTY_RULE.csv`, `RECON_KASEYA_MODULE_MAP.csv`.

---

## 1. KeepIT — exact Zuora charge-name scope  *(highest impact)*

**Source:** `KeepIT Recon May'26.xlsx` → `Zuora Usage` tab. The manual team hand-tags
every Zuora line in a **`Vendor SKU`** column (col 68) with the KI family, or `0` to
exclude. That tag column **is** the scope filter.

**In-scope charge names sum to 228,085 units** — matching your ~228K target. The gap
was never a partner/account issue; it was which CW charge names to count.

**INCLUDE (base backup seats — all four brand prefixes):**

| KI family | Zuora `Invoice Item: Charge Name` |
|---|---|
| **KI-M365-FUL** | CW RMM SaaS Backup Microsoft 365 Users · ConnectWise SaaS Backup Microsoft 365 Users · M2M RMM SaaS Backup Microsoft 365 Users · M2M-ConnectWise SaaS Backup Microsoft 365 Users |
| **KI-AZUR-CSP** | CW RMM SaaS Backup for Azure AD Advanced Users · M2M RMM SaaS Backup for Azure AD Advanced Users · ConnectWise SaaS Backup Azure AD Advanced Users |
| **KI-GOOG-FUL** | ConnectWise SaaS Backup Google Workspace User · CW RMM SaaS Backup Google Workspace Users · M2M RMM SaaS Backup Google Workspace Users · M2M-ConnectWise SaaS Backup Google User |
| **KI-D365-FUL** | CW RMM SaaS Backup for Dynamics (CRM) Users · ConnectWise SaaS Backup Microsoft Dynamics Users · M2M-ConnectWise SaaS Backup Microsoft Dynamics Users |
| **KI-SFDC-FUL** | CW RMM SaaS Backup Salesforce Users · M2M RMM SaaS Backup Salesforce Users · ConnectWise SaaS Backup Salesforce Users · M2M-ConnectWise SaaS Backup Salesforce Users |

**EXCLUDE (tagged `0` — 428,429 units):** any `Unlimited Retention Add-on` line
(retention is an add-on, not a seat), and every `Recover SaaS 3-year Promo` /
`3-year Promo` line (the Recover product line + promo are reconciled in the separate
**KeepIT Promo Recon** file, not the main recon).

**Why your filters missed:** narrow (`CW-RMM-SB-*` only) = 54% because it dropped the
`ConnectWise…`, `M2M RMM…`, and `M2M-ConnectWise…` prefixes, which **are** in scope.
Broad swept in the Retention Add-ons + Recover promo (the 428K). The exact keep-list
is the four prefixes above **on the base-seat product names only**.

---

## 2. Auvik CMS — committed device count  *(no missing field)*

**Source:** `Auvik CMS Recon May'26.xlsx` → `Zuora Usage` / `Data` tabs.

The committed count is **already in Zuora** — it's the native `Invoice Item: Quantity`
on the **`(Recurring)…Fixed Price`** package line. Zuora stores N there directly
(e.g. `…Package MSP 750 (Recurring)Fixed Price` → Quantity = **750**). The
`(Usage)…Overage` sibling line carries the overage quantity.

```
CW device qty (per family) = SUM(Invoice Item: Quantity) over BOTH
     Charge Type = 'Recurring'  (committed base, Qty already = N)
   + Charge Type = 'Usage'      (overage)
   split into Billable / Performance / ASM via RECON_AUVIK_PATTERN_MAP
```

**Validation:** SUM(Quantity) = **34,007** = **32,637 Billable + 1,370 Performance** —
exactly the manual `Data`-tab totals.

**Why your attempts missed:** raw = 45% because the recurring fixed-price lines were
counted as one package each (Qty=1) instead of their native N. Package-name parsing
(MSP 500 → 500) hit 126% by double-counting. **Fix: don't parse names and don't read
Included Units — use the recurring line's own Quantity.**

---

## 3. Kaseya — metric mismatch  *(users ≠ billing units)*

**Source:** `Kaseya Recon file May 2026.xlsx` (`Product-Summary`, `Pricing`, `Invoice`)
+ `RFT May 2026 Continuum.xlsx`.

`UserCount` (10,420) is total end-users — **not** a billing metric. Kaseya reconciles at
**member-per-module** grain. Each of the 945 active members carries boolean module
flags (`CTM_*`); the billed count per product = **count of members with that flag = Yes**.

Verified against the raw RFT (945 active members, 10,420 users):

| Product | RFT flag | Vendor report count | CW billed count | Unit $ |
|---|---|--:|--:|--:|
| Base | CTM_NA | 945 | 0 *(bundled — not a royalty line)* | 11 |
| Premium | CTM_NAP | 336 | 280 | 16 |
| Security | CTM_SA | 282 | 234 | 41 |
| Exchange | CTM_EA | 40 | 25 | 41 |
| SQL | CTM_MSSQL | 23 | 11 | 41 |
| Client Connector | CTM_CONNECTOR | 46 | 34 | 15 |
| Hipaa | CTM_HIPAA | 37 | 18 | 125 |
| PCI Compliance | CTM_PCI | 13 | 19 | 100 |

**CW billed total = 280+234+25+11+34+18+19 = 621** — exactly your Royalties figure.
So map vendor→CW on **member counts per CTM flag**, product by product; ignore
`UserCount`; Base is not separately billed.

---

## Bottom line
All three are now closed with source-tied logic: KeepIT scope = 228,085; Auvik CMS
qty = 34,007 (32,637 + 1,370); Kaseya billed = 621. Load the three CSVs and apply the
Auvik recurring-line rule — no further manual-team input required for these.
