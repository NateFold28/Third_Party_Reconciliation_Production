from pathlib import Path
import sys
import pandas as pd
sys.path.insert(0, r'c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion')
import Acronis_Vendor_Usage_Ingestion_Prod as a

source_root = Path(r"C:/Users/Nate.Fold/OneDrive - ConnectWise, Inc/THIRD_PARTY_RECONCILIATION/Manual Recon Files 2026/Acronis")
raw_df, scan_df = a.parse_month(source_root, '2026-05')
usage_df = a.build_vendor_usage_frame(raw_df)

print('scan columns:', ', '.join(scan_df.columns.tolist()))
print('\nscan summary by source_folder:')
cols = [c for c in ['raw_rows','kept_rows','dropped_non_billable_sku_prefix','dropped_unknown_sku_prefix','dropped_folder_type','dropped_non_direct_child','dropped_root_row','dropped_excluded_tenant'] if c in scan_df.columns]
print(scan_df.groupby(['source_folder'])[cols].sum().to_string())

print('\nTop files by kept_rows:')
print(scan_df[['source_folder','source_file','entity','portal_tenant','raw_rows','kept_rows']].sort_values('kept_rows', ascending=False).head(30).to_string(index=False))

print('\nPre-load usage totals (build_vendor_usage_frame):')
print(usage_df[['QUANTITY','AMOUNT']].sum().to_string())
print('rows', len(usage_df), 'partners', usage_df['VENDOR_PARTNER_NAME'].nunique(), 'skus', usage_df['VENDOR_PRODUCT_SKU'].nunique())

print('\nTop SKU totals pre-load:')
print(usage_df.groupby('VENDOR_PRODUCT_SKU')[['QUANTITY','AMOUNT']].sum().sort_values('AMOUNT', ascending=False).head(20).to_string())

print('\nTop unit prices pre-load:')
print(usage_df.groupby('VENDOR_PRODUCT_SKU')['UNIT_PRICE'].max().sort_values(ascending=False).head(20).to_string())
