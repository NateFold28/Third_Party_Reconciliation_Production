from TEMPLATES.Python.connection import fetch_dataframe

queries = {
"S1_INTRA_MONTHLY": """
select billing_month,
       sum(coalesce(vendor_invoice_seats,0)) as invoice_qty,
       sum(coalesce(vendor_raw_usage_seats,0)) as usage_qty,
       sum(coalesce(delta_seats,0)) as delta_qty,
       sum(coalesce(vendor_invoice_amount,0)) as invoice_amt,
       sum(coalesce(vendor_raw_usage_amount,0)) as usage_amt,
       sum(coalesce(delta_amount,0)) as delta_amt,
       sum(case when source_status='MATCH' then 1 else 0 end) as match_rows,
       count(*) as total_rows
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where vendor='SentinelOne' and billing_month between '2026-01-01' and '2026-07-01'
group by 1 order by 1
""",
"S1_INTRA_BY_SKU": """
select billing_month, sku, vendor_invoice_seats, vendor_raw_usage_seats, delta_seats,
       vendor_invoice_amount, vendor_raw_usage_amount, delta_amount, source_status
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where vendor='SentinelOne' and billing_month between '2026-01-01' and '2026-07-01'
order by billing_month, abs(coalesce(delta_amount,0)) desc, abs(coalesce(delta_seats,0)) desc
""",
"S1_SKU_LIST": """
select distinct sku
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICE_USAGE_INTRA_PROD
where vendor='SentinelOne' and billing_month between '2026-01-01' and '2026-07-01'
order by 1
""",
"S1_INVOICE_FILE_TOTALS": """
select billing_month, file_path,
       regexp_substr(file_path, 'INV[-_]?([0-9]+)', 1, 1, 'e', 1) as inv_no,
       sum(quantity) as qty,
       sum(amount) as amt,
       count(*) as lines
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where vendor='SentinelOne' and billing_month between '2026-01-01' and '2026-07-01'
group by 1,2,3
order by 1,2
""",
"S1_INVOICE_ANOMALIES": """
select billing_month, file_path, vendor_product_sku, description, quantity, unit_price, amount
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where vendor='SentinelOne'
  and billing_month between '2026-01-01' and '2026-07-01'
  and (
    quantity is null
    or amount is null
    or vendor_product_sku is null
    or trim(vendor_product_sku) in ('', 'UNKNOWN')
  )
order by billing_month, file_path
"""
}

for name,q in queries.items():
    print(f"\n=== {name} ===")
    df = fetch_dataframe(q)
    if df is None or df.empty:
        print('(no rows)')
    else:
        print(df.to_string(index=False, max_colwidth=140))
