from TEMPLATES.Python.connection import fetch_dataframe

sql = """
with base as (
  select billing_month, upper(sku) sku,
         sum(coalesce(vendor_invoice_seats,0)) inv_qty,
         sum(coalesce(vendor_raw_usage_seats,0)) use_qty,
         sum(coalesce(vendor_invoice_amount,0)) inv_amt,
         sum(coalesce(vendor_raw_usage_amount,0)) use_amt
  from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
  where upper(vendor)='SENTINELONE'
  group by 1,2
), tagged as (
  select *,
    case
      when sku='PURPLE AI' then 'KNOWN_PURPLE_AI'
      when sku='REMOTEOPS' then 'KNOWN_FORENSICS_RSO'
      when sku in ('MSSP','CLOUD FUNNEL') then 'MAPPING_MSSP_CLOUD_FUNNEL'
      when sku like 'S1ES %' then 'LEGACY_S1ES_SKUS'
      when sku in ('CORE','RANGER AD','S1 SING ID') then 'RAW_USAGE_ONLY_MINOR'
      else 'OTHER'
    end bucket
  from base
)
select billing_month, bucket,
       sum(use_qty-inv_qty) as delta_qty,
       sum(use_amt-inv_amt) as delta_amt
from tagged
group by 1,2
order by 1,2
"""
print(fetch_dataframe(sql).to_string(index=False))

print('\nMonthly residual excluding known Purple/RemoteOps:')
sql2 = """
with base as (
  select billing_month, upper(sku) sku,
         sum(coalesce(vendor_invoice_seats,0)) inv_qty,
         sum(coalesce(vendor_raw_usage_seats,0)) use_qty,
         sum(coalesce(vendor_invoice_amount,0)) inv_amt,
         sum(coalesce(vendor_raw_usage_amount,0)) use_amt
  from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
  where upper(vendor)='SENTINELONE'
  group by 1,2
)
select billing_month,
       sum(case when sku not in ('PURPLE AI','REMOTEOPS') then use_qty-inv_qty else 0 end) as residual_delta_qty,
       sum(case when sku not in ('PURPLE AI','REMOTEOPS') then use_amt-inv_amt else 0 end) as residual_delta_amt
from base
group by 1
order by 1
"""
print(fetch_dataframe(sql2).to_string(index=False))
