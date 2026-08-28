from TEMPLATES.Python.connection import fetch_dataframe

print('Acronis May usage total:')
print(fetch_dataframe("""
select sum(quantity) as qty, sum(amount) as amount, count(*) as row_count
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_usage_prod
where upper(vendor)='ACRONIS' and billing_month='2026-05-01'::date
""").to_string(index=False))

print('\nAcronis May usage by modifier/entity and source signals:')
print(fetch_dataframe("""
select coalesce(modifier,'(NULL)') as modifier,
       count(*) as row_count,
       sum(quantity) as qty,
       sum(amount) as amount,
       count(distinct vendor_partner_name) as partners,
       count(distinct vendor_product_sku) as skus
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_usage_prod
where upper(vendor)='ACRONIS' and billing_month='2026-05-01'::date
group by 1
order by 4 desc
""").to_string(index=False))

print('\nAcronis May invoice total:')
print(fetch_dataframe("""
select sum(quantity) as qty, sum(amount) as amount, count(*) as row_count
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoices
where upper(vendor)='ACRONIS' and billing_month='2026-05-01'::date
""").to_string(index=False))
