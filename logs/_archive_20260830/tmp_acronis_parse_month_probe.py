from pathlib import Path
import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion')
import Acronis_Vendor_Usage_Ingestion_Prod as a

source_root = Path(r"C:/Users/Nate.Fold/OneDrive - ConnectWise, Inc/THIRD_PARTY_RECONCILIATION/Manual Recon Files 2026/Acronis")
usage_df, scan_df = a.parse_month(source_root, '2026-05')
print('usage rows', len(usage_df), 'scan rows', len(scan_df))
print('\nscan summary by source_folder:')
print(scan_df.groupby(['source_folder'])[['raw_rows','kept_rows','dropped_folder_type','dropped_not_direct_child','dropped_excluded_tenant','dropped_missing_sku','dropped_disallowed_sku_prefix']].sum().to_string())
print('\nTop files by kept_rows:')
print(scan_df[['source_folder','file','entity','portal_tenant','raw_rows','kept_rows']].sort_values('kept_rows', ascending=False).head(40).to_string(index=False))

print('\nUsage amount by entity:')
print(usage_df.groupby('Entity',dropna=False)[['AMOUNT','QUANTITY']].sum().sort_values('AMOUNT', ascending=False).to_string())

print('\nDistinct files loaded:', scan_df['file'].nunique())
