from pathlib import Path
import sys
sys.path.insert(0, r'c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion')
import Acronis_Vendor_Usage_Ingestion_Prod as a
source_root = Path(r"C:/Users/Nate.Fold/OneDrive - ConnectWise, Inc/THIRD_PARTY_RECONCILIATION/Manual Recon Files 2026/Acronis")
raw_df, scan_df = a.parse_month(source_root, '2026-01')
print(raw_df.columns.tolist())
print(raw_df.head(2).to_string(index=False))
