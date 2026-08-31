import importlib.util
from pathlib import Path
import pandas as pd
from TEMPLATES.Python.connection import fetch_dataframe

mod_path = Path('PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion/Netsuite_Invoice_JSON_Ingestion_Prod.py')
spec = importlib.util.spec_from_file_location('inv_ing', mod_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

src = fetch_dataframe("""
select file_path, parsed_document
from NETSUITE.DBO.PARSED_VENDOR_DATA
where lower(vendor_name) like '%sentinelone%'
  and regexp_substr(file_path, '^[0-9]{4}_[0-9]{2}') >= '2026_01'
order by file_path
""")

rows = []
for _, r in src.iterrows():
    fp = r['FILE_PATH']
    txt = mod._extract_text(r['PARSED_DOCUMENT'])
    parsed = mod._parse_sentinelone(txt, fp)
    qty = sum(float(x.get('quantity') or 0) for x in parsed)
    amt = sum(float(x.get('amount') or 0) for x in parsed)
    rows.append({'FILE_PATH': fp, 'PARSED_LINES': len(parsed), 'PARSED_QTY': qty, 'PARSED_AMT': amt})
parsed_df = pd.DataFrame(rows)

loaded = fetch_dataframe("""
select file_path, count(*) as loaded_lines,
       sum(quantity)::float as loaded_qty,
       sum(amount)::float as loaded_amt
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where upper(vendor)='SENTINELONE'
  and billing_month >= '2026-01-01'::date
group by 1
order by 1
""")

loaded.columns = [c.upper() for c in loaded.columns]
cmp = parsed_df.merge(loaded, on='FILE_PATH', how='outer')
for c in ['PARSED_LINES','PARSED_QTY','PARSED_AMT','LOADED_LINES','LOADED_QTY','LOADED_AMT']:
    cmp[c] = pd.to_numeric(cmp[c], errors='coerce').fillna(0)

cmp['QTY_DIFF'] = cmp['LOADED_QTY'] - cmp['PARSED_QTY']
cmp['AMT_DIFF'] = cmp['LOADED_AMT'] - cmp['PARSED_AMT']
cmp['LINE_DIFF'] = cmp['LOADED_LINES'] - cmp['PARSED_LINES']
print(cmp.to_string(index=False, max_colwidth=120))

nz = cmp[(cmp['QTY_DIFF'].abs()>1e-6) | (cmp['AMT_DIFF'].abs()>1e-6) | (cmp['LINE_DIFF']!=0)]
print('\nnon-zero diffs:')
print('(none)' if nz.empty else nz.to_string(index=False, max_colwidth=120))
