from pathlib import Path
import sys
import pandas as pd
from TEMPLATES.Python.connection import fetch_dataframe

sys.path.insert(0, r'c:/Users/Nate.Fold/projects/PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion')
import Acronis_Vendor_Usage_Ingestion_Prod as a

source_root = Path(r"C:/Users/Nate.Fold/OneDrive - ConnectWise, Inc/THIRD_PARTY_RECONCILIATION/Manual Recon Files 2026/Acronis")
raw_df, scan_df = a.parse_month(source_root, '2026-05')
for c in ['Production usage','Trial usage','Total usage']:
    raw_df[c] = pd.to_numeric(raw_df[c], errors='coerce').fillna(0.0)
raw_df['SKU'] = raw_df['SKU'].astype(str).str.upper().str.strip()

usage_tot = raw_df.groupby('SKU', as_index=False).agg(
    total_usage=('Total usage','sum'),
    prod_usage=('Production usage','sum'),
    trial_usage=('Trial usage','sum')
)
inv = fetch_dataframe("""
select upper(trim(vendor_product_sku)) as sku,
       sum(quantity) as invoice_qty,
       sum(amount) as invoice_amt
from analytics_dev.dbt_nfold_transformation.third_party_recon_vendor_invoices
where upper(vendor)='ACRONIS' and billing_month='2026-05-01'::date
group by 1
""")
inv.columns = [c.lower() for c in inv.columns]
for c in ['invoice_qty','invoice_amt']:
    inv[c] = pd.to_numeric(inv[c], errors='coerce').fillna(0.0)

merged = usage_tot.merge(inv, how='outer', left_on='SKU', right_on='sku').fillna(0)
merged['delta_total_vs_inv'] = merged['total_usage'] - merged['invoice_qty']
merged['delta_prod_vs_inv'] = merged['prod_usage'] - merged['invoice_qty']
merged['abs_total_vs_inv'] = merged['delta_total_vs_inv'].abs()
merged['abs_prod_vs_inv'] = merged['delta_prod_vs_inv'].abs()
merged['prod_better'] = merged['abs_prod_vs_inv'] < merged['abs_total_vs_inv']

print('Rows where Production usage is closer to invoice qty than Total usage:')
print(merged[merged['prod_better']].sort_values('abs_total_vs_inv', ascending=False).head(40).to_string(index=False))

print('\nTop qty deltas using Total usage basis:')
print(merged.sort_values('abs_total_vs_inv', ascending=False).head(30)[['SKU','invoice_qty','total_usage','prod_usage','trial_usage','delta_total_vs_inv','delta_prod_vs_inv']].to_string(index=False))

print('\nAggregate abs seat delta comparison:')
print(merged[['abs_total_vs_inv','abs_prod_vs_inv']].sum().to_string())
