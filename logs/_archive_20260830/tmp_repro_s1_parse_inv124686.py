import importlib.util
from pathlib import Path
from TEMPLATES.Python.connection import fetch_dataframe

mod_path = Path('PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion/Netsuite_Invoice_JSON_Ingestion_Prod.py')
spec = importlib.util.spec_from_file_location('inv_ing', mod_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

df = fetch_dataframe("""
select file_path, parsed_document
from NETSUITE.DBO.PARSED_VENDOR_DATA
where file_path ilike '%INV124686%'
  and vendor_name ilike '%SentinelOne%'
limit 1
""")
if df.empty:
    print('No source file found')
    raise SystemExit(1)
row = df.iloc[0]
text = mod._extract_text(row['PARSED_DOCUMENT'])
parsed = mod._parse_sentinelone(text, row['FILE_PATH'])
print('rows:', len(parsed))
for r in parsed:
    print(r)
print('sum_qty', sum((x.get('quantity') or 0) for x in parsed))
print('sum_amt', sum((x.get('amount') or 0) for x in parsed))
