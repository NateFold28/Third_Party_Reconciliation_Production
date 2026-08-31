from TEMPLATES.Python.connection import fetch_dataframe
q = """
with base as (
  select
    billing_month,
    sku,
    coalesce(vendor_invoice_seats,0) as inv_qty,
    coalesce(vendor_raw_usage_seats,0) as use_qty,
    coalesce(delta_seats,0) as d_qty,
    coalesce(vendor_invoice_amount,0) as inv_amt,
    coalesce(vendor_raw_usage_amount,0) as use_amt,
    coalesce(delta_amount,0) as d_amt
  from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
  where vendor='Auvik' and billing_month in ('2026-02-01','2026-03-01','2026-04-01','2026-05-01')
)
select
  case
    when abs(d_amt) < 0.01 and (inv_amt = 0 and use_amt = 0) then 'zero_dollar_zero_amt_match'
    when abs(d_amt) < 0.01 then 'amount_matched'
    else 'amount_unmatched'
  end as bucket,
  sum(d_qty) as net_delta_qty,
  sum(abs(d_qty)) as abs_delta_qty,
  count(*) as row_count
from base
group by 1
order by 1
"""

df = fetch_dataframe(q)
print(df.to_string(index=False))

q2 = """
select billing_month, sku, vendor_invoice_seats, vendor_raw_usage_seats, delta_seats, vendor_invoice_amount, vendor_raw_usage_amount, delta_amount
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where vendor='Auvik' and billing_month in ('2026-02-01','2026-03-01','2026-04-01','2026-05-01')
order by abs(coalesce(delta_seats,0)) desc
limit 20
"""
print("\nTOP 20 ABS DELTA_SEATS (FEB-MAY):")
print(fetch_dataframe(q2).to_string(index=False))
