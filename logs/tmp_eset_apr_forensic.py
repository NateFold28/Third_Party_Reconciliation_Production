import importlib.util
import re
from pathlib import Path
import pandas as pd
from TEMPLATES.Python.connection import fetch_dataframe

mod_path = Path('PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion/Netsuite_Invoice_JSON_Ingestion_Prod.py')
spec = importlib.util.spec_from_file_location('inv_ing', mod_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

src = fetch_dataframe("""
select file_path, vendor_name, parsed_document
from NETSUITE.DBO.PARSED_VENDOR_DATA
where vendor_name ilike '%eset%'
  and file_path ilike '2026_04/%'
order by file_path
""")

loaded = fetch_dataframe("""
select file_path, sum(amount)::float as loaded_amt, sum(quantity)::float as loaded_qty, count(*) as loaded_lines
from ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_INVOICES
where vendor ilike '%eset%'
  and billing_month='2026-04-01'::date
group by 1
""")
if not loaded.empty:
    loaded.columns = [c.lower() for c in loaded.columns]

rows=[]
for _,r in src.iterrows():
    fp = r['FILE_PATH']
    txt = mod._extract_text(r['PARSED_DOCUMENT'])
    parsed = mod._parse_eset(txt, fp)
    p_amt = float(sum((x.get('amount') or 0) for x in parsed))
    p_qty = float(sum((x.get('quantity') or 0) for x in parsed))
    # crude invoice total extraction for diagnostics
    totals = re.findall(r'(?:Total|Amount Due|Invoice Total)[^\d$]*\$?\s*([\d,]+(?:\.\d{2})?)', txt, flags=re.I)
    doc_total = float(totals[-1].replace(',','')) if totals else None
    rows.append({'file_path':fp,'vendor_name':r['VENDOR_NAME'],'parsed_lines':len(parsed),'parsed_qty':p_qty,'parsed_amt':p_amt,'doc_total_guess':doc_total})

cmp = pd.DataFrame(rows)
if not loaded.empty:
    cmp = cmp.merge(loaded, on='file_path', how='left')
else:
    cmp['loaded_amt']=None; cmp['loaded_qty']=None; cmp['loaded_lines']=None
for c in ['loaded_amt','loaded_qty','loaded_lines']:
    cmp[c]=pd.to_numeric(cmp[c], errors='coerce').fillna(0)
cmp['amt_diff_loaded_minus_parsed']=cmp['loaded_amt']-cmp['parsed_amt']
cmp['qty_diff_loaded_minus_parsed']=cmp['loaded_qty']-cmp['parsed_qty']
print(cmp.to_string(index=False, max_colwidth=120))
print('\nTotals:')
print(cmp[['parsed_amt','loaded_amt']].sum().to_string())
