from pathlib import Path
import sys
import pandas as pd
from TEMPLATES.Python.connection import fetch_dataframe

sys.path.insert(0, r'c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion')
import Acronis_Vendor_Usage_Ingestion_Prod as a

months = [f'2026-{m:02d}' for m in range(1, 13)]

month_gap = fetch_dataframe("""
with inv as (
  select billing_month::date as billing_month,
         sum(amount) as invoice_amount,
         sum(quantity) as invoice_qty,
         count(*) as invoice_lines
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoices
  where upper(vendor)='ACRONIS'
    and billing_month >= '2026-01-01'::date
  group by 1
), usg as (
  select billing_month::date as billing_month,
         sum(amount) as usage_amount,
         sum(quantity) as usage_qty,
         count(*) as usage_rows
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_usage_prod
  where upper(vendor)='ACRONIS'
    and billing_month >= '2026-01-01'::date
  group by 1
)
select
  inv.billing_month,
  inv.invoice_amount,
  usg.usage_amount,
  usg.usage_amount - inv.invoice_amount as delta_amount,
  abs(usg.usage_amount - inv.invoice_amount) as abs_delta_amount,
  inv.invoice_qty,
  usg.usage_qty,
  usg.usage_qty - inv.invoice_qty as delta_qty,
  inv.invoice_lines,
  usg.usage_rows
from inv
left join usg on usg.billing_month = inv.billing_month
order by 1
""")
print('=== MONTHLY INVOICE VS USAGE (ACRONIS) ===')
print(month_gap.to_string(index=False))

status = fetch_dataframe("""
select
  billing_month,
  source_status,
  count(*) as rows,
  sum(coalesce(vendor_invoice_amount,0)) as invoice_amount,
  sum(coalesce(vendor_raw_usage_amount,0)) as usage_amount,
  sum(coalesce(delta_amount,0)) as delta_amount
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where vendor='Acronis'
  and billing_month >= '2026-01-01'::date
group by 1,2
order by 1,2
""")
print('\n=== INTRA STATUS SPLIT BY MONTH ===')
print(status.to_string(index=False))

invoice_only = fetch_dataframe("""
select
  billing_month,
  sku,
  sum(vendor_invoice_amount) as invoice_only_amount,
  sum(vendor_invoice_seats) as invoice_only_qty
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where vendor='Acronis'
  and source_status='INVOICE_ONLY'
  and billing_month >= '2026-01-01'::date
group by 1,2
order by 1, invoice_only_amount desc
""")
print('\n=== INVOICE-ONLY SKU LINES ===')
print(invoice_only.to_string(index=False))

top_delta = fetch_dataframe("""
select *
from (
  select
    billing_month,
    sku,
    sum(vendor_invoice_amount) as invoice_amount,
    sum(vendor_raw_usage_amount) as usage_amount,
    sum(delta_amount) as delta_amount,
    row_number() over(partition by billing_month order by abs(sum(delta_amount)) desc) as rk
  from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
  where vendor='Acronis'
    and billing_month >= '2026-01-01'::date
  group by 1,2
)
where rk <= 6
order by billing_month, rk
""")
print('\n=== TOP 6 SKU DELTAS PER MONTH ===')
print(top_delta.to_string(index=False))

source_root = Path(r"C:/Users/Nate.Fold/OneDrive - ConnectWise, Inc/THIRD_PARTY_RECONCILIATION/Manual Recon Files 2026/Acronis")
coverage_rows = []
for m in months:
    try:
        raw_df, scan_df = a.parse_month(source_root, m)
    except Exception as e:
        coverage_rows.append({
            'month': m,
            'error': str(e),
        })
        continue
    base = {
        'month': m,
        'file_count': int(scan_df['source_file'].nunique()),
        'entities': int(raw_df['ENTITY'].nunique()) if not raw_df.empty else 0,
        'tenants': int(raw_df['TENANT'].nunique()) if not raw_df.empty else 0,
        'raw_rows_after_filters': int(len(raw_df)),
        'raw_rows_total': int(pd.to_numeric(scan_df['raw_rows'], errors='coerce').fillna(0).sum()),
        'kept_rows_total': int(pd.to_numeric(scan_df['kept_rows'], errors='coerce').fillna(0).sum()),
        'dropped_non_billable': int(pd.to_numeric(scan_df['dropped_non_billable_sku_prefix'], errors='coerce').fillna(0).sum()),
        'dropped_folder': int(pd.to_numeric(scan_df['dropped_folder_type'], errors='coerce').fillna(0).sum()),
        'dropped_root_row': int(pd.to_numeric(scan_df['dropped_root_row'], errors='coerce').fillna(0).sum()),
        'unknown_entity_files': int(pd.to_numeric(scan_df['unknown_entity'].astype(int), errors='coerce').fillna(0).sum()),
    }
    by_folder = scan_df.groupby('source_folder', as_index=False)[['raw_rows','kept_rows']].sum()
    for _, r in by_folder.iterrows():
        folder = str(r['source_folder'])
        base[f'{folder}_raw_rows'] = int(r['raw_rows'])
        base[f'{folder}_kept_rows'] = int(r['kept_rows'])
    coverage_rows.append(base)

coverage_df = pd.DataFrame(coverage_rows)
print('\n=== INGESTION COVERAGE BY MONTH ===')
print(coverage_df.fillna(0).to_string(index=False))

out_path = Path('logs/acronis_monthly_gap_and_coverage_20260825.csv')
coverage_df.to_csv(out_path, index=False)
print(f'\\nWrote coverage csv: {out_path}')
