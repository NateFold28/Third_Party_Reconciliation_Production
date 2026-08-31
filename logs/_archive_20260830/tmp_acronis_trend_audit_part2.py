from pathlib import Path
import sys
import pandas as pd
from TEMPLATES.Python.connection import fetch_dataframe

sys.path.insert(0, r'c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion')
import Acronis_Vendor_Usage_Ingestion_Prod as a

months = [f'2026-{m:02d}' for m in range(1, 13)]

status = fetch_dataframe("""
select
  billing_month,
  source_status,
  count(*) as row_count,
  sum(coalesce(vendor_invoice_amount,0)) as invoice_amount,
  sum(coalesce(vendor_raw_usage_amount,0)) as usage_amount,
  sum(coalesce(delta_amount,0)) as delta_amount
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoice_usage_intra_prod
where vendor='Acronis'
  and billing_month >= '2026-01-01'::date
group by 1,2
order by 1,2
""")
print('=== INTRA STATUS SPLIT BY MONTH ===')
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
        coverage_rows.append({'month': m, 'error': str(e)})
        continue
    base = {
        'month': m,
        'file_count': int(scan_df['source_file'].nunique()),
        'entities': int(raw_df['ENTITY'].nunique()) if not raw_df.empty else 0,
        'tenants': int(raw_df['TENANT'].nunique()) if not raw_df.empty else 0,
        'kept_rows_total': int(pd.to_numeric(scan_df['kept_rows'], errors='coerce').fillna(0).sum()),
        'dropped_non_billable': int(pd.to_numeric(scan_df['dropped_non_billable_sku_prefix'], errors='coerce').fillna(0).sum()),
        'dropped_folder': int(pd.to_numeric(scan_df['dropped_folder_type'], errors='coerce').fillna(0).sum()),
        'dropped_root_row': int(pd.to_numeric(scan_df['dropped_root_row'], errors='coerce').fillna(0).sum()),
    }
    by_folder = scan_df.groupby('source_folder', as_index=False)[['kept_rows']].sum()
    for _, r in by_folder.iterrows():
        base[f"{r['source_folder']}_kept_rows"] = int(r['kept_rows'])
    coverage_rows.append(base)

coverage_df = pd.DataFrame(coverage_rows)
print('\n=== INGESTION COVERAGE BY MONTH ===')
print(coverage_df.fillna(0).to_string(index=False))

out_path = Path('logs/acronis_ingestion_coverage_trend_20260825.csv')
coverage_df.to_csv(out_path, index=False)
print(f'\\nWrote coverage csv: {out_path}')
