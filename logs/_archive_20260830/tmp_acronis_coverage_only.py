from pathlib import Path
import sys
import pandas as pd

sys.path.insert(0, r'c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion')
import Acronis_Vendor_Usage_Ingestion_Prod as a

months = [f'2026-{m:02d}' for m in range(1, 13)]
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
        'entities': int(raw_df['Entity'].nunique()) if not raw_df.empty else 0,
        'tenants': int(raw_df['Tenant name'].nunique()) if not raw_df.empty else 0,
        'kept_rows_total': int(pd.to_numeric(scan_df['kept_rows'], errors='coerce').fillna(0).sum()),
        'dropped_non_billable': int(pd.to_numeric(scan_df['dropped_non_billable_sku_prefix'], errors='coerce').fillna(0).sum()),
        'dropped_folder': int(pd.to_numeric(scan_df['dropped_folder_type'], errors='coerce').fillna(0).sum()),
        'dropped_root_row': int(pd.to_numeric(scan_df['dropped_root_row'], errors='coerce').fillna(0).sum()),
    }

    by_folder = scan_df.groupby('source_folder', as_index=False)[['kept_rows','raw_rows']].sum()
    for _, r in by_folder.iterrows():
        folder = str(r['source_folder'])
        base[f"{folder}_kept_rows"] = int(r['kept_rows'])
        base[f"{folder}_raw_rows"] = int(r['raw_rows'])
    coverage_rows.append(base)

coverage_df = pd.DataFrame(coverage_rows)
print(coverage_df.fillna(0).to_string(index=False))
out_path = Path('logs/acronis_ingestion_coverage_trend_20260825.csv')
coverage_df.to_csv(out_path, index=False)
print(f'\\nWrote coverage csv: {out_path}')
