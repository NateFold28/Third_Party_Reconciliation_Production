from TEMPLATES.Python.connection import fetch_dataframe

print('Acronis invoice May totals after parser fix:')
print(fetch_dataframe("""
select sum(quantity) as qty, sum(amount) as amount, count(*) as row_count
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoices
where upper(vendor)='ACRONIS' and billing_month='2026-05-01'::date
""").to_string(index=False))

print('\nAcronis May invoice unit price sanity (top 20 by unit_price):')
print(fetch_dataframe("""
select vendor_product_sku, min(unit_price) as min_up, max(unit_price) as max_up,
       sum(quantity) as qty, sum(amount) as amount
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoices
where upper(vendor)='ACRONIS' and billing_month='2026-05-01'::date
group by 1
order by max_up desc
limit 20
""").to_string(index=False))
