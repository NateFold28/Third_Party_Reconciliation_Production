from TEMPLATES.Python.connection import fetch_dataframe
print(fetch_dataframe("""
select
  billing_month,
  sum(case when abs(coalesce(delta_seats,0)) < 0.0001 then coalesce(delta_amount,0) else 0 end) as amt_delta_when_seats_match,
  sum(case when abs(coalesce(delta_seats,0)) >= 0.0001 then coalesce(delta_amount,0) else 0 end) as amt_delta_when_seats_mismatch,
  sum(case when abs(coalesce(delta_seats,0)) < 0.0001 then abs(coalesce(delta_amount,0)) else 0 end) as abs_amt_match_seats,
  sum(case when abs(coalesce(delta_seats,0)) >= 0.0001 then abs(coalesce(delta_amount,0)) else 0 end) as abs_amt_mismatch_seats,
  sum(case when abs(coalesce(delta_seats,0)) < 0.0001 then 1 else 0 end) as rows_seats_match,
  sum(case when abs(coalesce(delta_seats,0)) >= 0.0001 then 1 else 0 end) as rows_seats_mismatch
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where upper(vendor)='ESET'
  and billing_month in ('2026-04-01'::date,'2026-05-01'::date)
group by 1
order by 1
""").to_string(index=False))

print('\nESET June invoice source files currently present:')
print(fetch_dataframe("""
select billing_month, file_path, vendor_product_sku, quantity, unit_price, amount
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='ESET' and billing_month='2026-06-01'::date
order by file_path
""").to_string(index=False, max_colwidth=160))
