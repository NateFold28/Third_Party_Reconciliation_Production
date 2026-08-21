# Combined Production Contract

## Vendor Scope

The combined production contract covers:

- Proofpoint
- SentinelOne
- Webroot
- Acronis
- KeepIT
- Auvik
- Bitdefender
- ESET
- Exium

## Shared Source Tables

`THIRD_PARTY_RECON_VENDOR_USAGE_PROD` is the shared vendor usage source.

Grain:

- billing_month
- vendor
- vendor_partner_name
- vendor_product_sku
- modifier

Columns:

- billing_month
- vendor
- vendor_partner_name
- vendor_product_sku
- modifier
- quantity
- unit_price
- amount
- currency

`THIRD_PARTY_RECON_SKU_MAP_PROD` is the shared SKU mapping source.

Columns:

- vendor
- vendor_product
- vendor_sku
- cw_sku
- sku_match_key
- mapping_notes
- contract_cost_rate
- cw_retail_rate

`THIRD_PARTY_RECON_PARTNER_MAP_PROD` is the shared partner mapping source.

Columns:

- vendor
- partner_name
- parent_company
- sf_id
- cms_id
- zuora_name

## Reconciliation Boundary

The combined production pipeline does not run a generic reconciliation engine.
Each vendor keeps its own reconciliation logic and writes its own detail and
summary tables. The combined layer unions those vendor-owned outputs.

## App-Facing Tables

The app-facing detail table is:

- `THIRD_PARTY_RECON_OUTPUT_PROD`

Combined summary table also built by the union step:

- `THIRD_PARTY_RECON_SUMMARY`

Success metric:

- exact vendor/month parity between `THIRD_PARTY_RECON_OUTPUT_PROD` and each
  vendor's own recon detail table
- exact vendor/month parity between `THIRD_PARTY_RECON_SUMMARY` and each
  vendor's own recon summary table

