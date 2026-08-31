from pathlib import Path
import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion')
import Acronis_Vendor_Usage_Ingestion_Prod as a

source_root = Path(r"C:/Users/Nate.Fold/OneDrive - ConnectWise, Inc/THIRD_PARTY_RECONCILIATION/Manual Recon Files 2026/Acronis")
raw_df, scan_df = a.parse_month(source_root, '2026-05')
cols = ['source_folder','entity','raw_rows','kept_rows','dropped_non_billable_sku_prefix','dropped_folder_type','dropped_non_direct_child','dropped_root_row','unknown_entity']
print(scan_df[cols].sort_values(['source_folder','kept_rows'], ascending=[True,False]).to_string(index=False))
