from TEMPLATES.Python.connection import fetch_dataframe

print('ESET April intra deltas by SKU:')
print(fetch_dataframe("""
select sku, vendor_invoice_sku, vendor_usage_sku, vendor_invoice_seats, vendor_raw_usage_seats, vendor_invoice_amount, vendor_raw_usage_amount, delta_seats, delta_amount
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where upper(vendor)='ESET' and billing_month='2026-04-01'::date
order by abs(delta_amount) desc
""").to_string(index=False, max_colwidth=140))

print('\nESET mapping rows (encryption-ish):')
print(fetch_dataframe("""
select vendor_product, vendor_sku, cw_sku, sku_match_key, mapping_notes
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_SKU_MAP_PROD
where upper(vendor)='ESET'
  and (
    upper(coalesce(vendor_product,'')) like '%ENCRYPT%'
    or upper(coalesce(vendor_sku,'')) like '%ENCRYPT%'
    or upper(coalesce(cw_sku,'')) like '%ENCRYPT%'
    or upper(coalesce(sku_match_key,'')) like '%ENCRYPT%'
  )
order by 1,2,3
""").to_string(index=False, max_colwidth=140))

print('\nESET invoice raw SKUs April:')
print(fetch_dataframe("""
select vendor_product_sku, sum(quantity) qty, sum(amount) amt
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='ESET' and billing_month='2026-04-01'::date
group by 1 order by 1
""").to_string(index=False, max_colwidth=140))

print('\nESET usage raw SKUs April:')
print(fetch_dataframe("""
select vendor_product_sku, sum(quantity) qty, sum(amount) amt
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD
where upper(vendor)='ESET' and billing_month='2026-04-01'::date
group by 1 order by 1
""").to_string(index=False, max_colwidth=140))
