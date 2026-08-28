# Third-Party Recon Issue Audit (2026-08-26)

## Scope
- Reviewed app issue exports (narrow + wide filters).
- Traced behavior to recon SQL and unified map build.
- Ran Snowflake diagnostics on RECON_PARTNER_MAP and THIRD_PARTY_RECON_OUTPUT_PROD.

## Key Results

### 1) Partner-map cardinality is a real root cause (legitimate data-quality defect)
- `RECON_PARTNER_MAP` partner_name -> multiple sf_id: **50 partner keys**.
- `RECON_PARTNER_MAP` sf_id -> multiple partner_name: **1565 sf_ids**.
  - Many-to-one is expected for aliases/merged names.
  - One-to-many (partner -> many sf_id) is problematic and can misroute rows.

Examples of partner keys with multiple sf_id:
- `SECURE NETWORKS` -> `ACT-00144936 | ACT-00224787 | ACT-00272688`
- `EBC GROUP (UK) LTD` -> `ACT-00022528 | ACT-00420329`
- `DANMARK COMMUNICATIONS` -> `ACT-00061566 | ACT-00141079`
- `COMMERCIAL NETWORKS LTD` -> `ACT-00107189 | ACT-00209449`

### 2) Null partner names are mostly mapping/display hygiene, not pure unmapped leakage
- Output rows since 2026-01-01: **90,273**
- Null/blank `VENDOR_PARTNER_NAME`: **4,241**
- Of null partner rows:
  - `SF_ID present`: **4,238**
  - `SF_ID missing`: **3**
  - `vendor_quantity > 0`: **0** rows
  - `total_billing_quantity > 0`: **2,508** rows

Interpretation:
- Null partner is usually a billing-only row where partner label was not backfilled from SF/account map.
- This is mostly a data-completeness/UX issue, not true partner-unmapped root cause.

Top contributors:
- Webroot: 2,937 null partner rows
- Auvik: 595
- Exium: 435
- Bitdefender: 256

### 3) Pipe-delimited names are largely alias aggregation artifacts
- Pipe partner rows: **412**
- Concentrated in Proofpoint/KeepIT/Acronis/SentinelOne.
- Most pipe rows have exactly one SF_ID (alias-display artifact, not necessarily mapping failure).
- SentinelOne has a distinct subset of pipe rows with missing SF_ID under `Unmapped Partner`.

### 4) Big leakage queue still exists after excluding obvious mapping hygiene
- From wide export, likely-legit rows (non-null partner, no pipe, actionable exception types): **7,755**
  - Vendor Billing, No CW Billing: 3,018
  - API Usage, Insufficient CW Billing: 1,382
  - Vendor Billing, Insufficient CW Billing: 1,375
  - Vendor SKU, No CW SKU: 1,060
  - CW Billing, No Vendor Billing: 920

This indicates map cleanup will help quality, but not eliminate core leakage exposure.

## Why This Is Happening In SQL

### Root issue A: vendor key removed from RECON_PARTNER_MAP
- `Maps/sql/02_unified_reference_maps.sql` builds `RECON_PARTNER_MAP` without a vendor column and dedupes globally.
- This can collapse same-name partners across vendors and create ambiguous joins.

### Root issue B: billing-only rows in several vendor scripts preserve SF_ID but not partner label
- Auvik and Exium joined datasets keep `v.vendor_partner_name` (vendor side) and do not reliably backfill partner name for billing-only rows.
- Webroot also carries `u.vendor_partner_name` from usage side in the joined layer.
- KeepIT explicitly sets partner name to NULL in `zuora_only`, but then correctly backfills in a later `joined` CTE.

## Immediate Cleanup Plan (safe, high ROI)

### Step 1: fix partner->sf_id one-to-many in THIRD_PARTY_RECON_PARTNER_MAP_PROD
- Target only partner keys with `COUNT(DISTINCT SF_ID) > 1`.
- Resolve each to one canonical SF_ID (using merged-account canonical IDs and current billing ownership).
- Keep aliases (many partner names -> one SF_ID) intact.

Suggested audit SQL:

```sql
SELECT UPPER(TRIM(PARTNER_NAME)) AS partner_key,
       COUNT(DISTINCT SF_ID) AS sf_id_count,
       LISTAGG(DISTINCT SF_ID, ' | ') WITHIN GROUP (ORDER BY SF_ID) AS sf_ids
FROM ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.RECON_PARTNER_MAP
WHERE PARTNER_NAME IS NOT NULL AND TRIM(PARTNER_NAME) <> ''
  AND SF_ID IS NOT NULL AND TRIM(SF_ID) <> ''
GROUP BY 1
HAVING COUNT(DISTINCT SF_ID) > 1
ORDER BY sf_id_count DESC, partner_key;
```

### Step 2: backfill partner labels for billing-only rows in Auvik/Exium/Webroot
- Apply KeepIT pattern: add `sf_id -> partner_name` and/or Salesforce account-name fallback in final joined CTE.
- This should remove the majority of null partner rows without changing financial math.

### Step 3: normalize display for pipe names
- Use canonical partner label per SF_ID for display fields (keep raw alias list in metadata field if needed).
- Preserve raw `partner_match_methods` and source fields for traceability.

### Step 4: rerun and compare key KPIs
- Null partner rows
- Unmapped partner rows (with SF_ID actually null)
- Finance queue $ impact
- Top 100 cases movement by exception type

## Practical Triage Rule (for current queue)
- Treat as mapping/data-quality first:
  - Partner is null but SF_ID present
  - Partner contains pipe and SF_ID present
- Treat as true recon issue first:
  - Exception in {Vendor Billing, No CW Billing; Vendor Billing, Insufficient CW Billing; API Usage, Insufficient CW Billing; Vendor SKU, No CW SKU; CW Billing, No Vendor Billing}
  - Partner non-null and non-pipe
  - SF_ID present

## Notes
- Some API-usage rows can have zero amount delta and still be valid quantity-leakage indicators.
- Map cleanup should reduce noise and improve assignment, but not remove all high-impact true leakage exceptions.
