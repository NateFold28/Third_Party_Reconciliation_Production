import importlib.util
import re
from pathlib import Path
from TEMPLATES.Python.connection import fetch_dataframe

mod_path = Path('PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion/Netsuite_Invoice_JSON_Ingestion_Prod.py')
spec = importlib.util.spec_from_file_location('inv_ing', mod_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

df = fetch_dataframe("""
select parsed_document
from NETSUITE.DBO.PARSED_VENDOR_DATA
where file_path ilike '%INV124686%'
  and vendor_name ilike '%SentinelOne%'
limit 1
""")
text = mod._extract_text(df.iloc[0]['PARSED_DOCUMENT'])

normalized_lines = []
block_parts = []
in_wrapped_row = False
row_start_re = re.compile(r"^\|\s*[A-Z0-9]+(?:-[A-Z0-9]+)+\s*$")
row_end_re = re.compile(r"\|\s*\$?[\d,]+(?:\.\d+)?\s*\|\s*$")
for ln in text.splitlines():
    s = ln.rstrip()
    if not in_wrapped_row:
        if row_start_re.match(s):
            in_wrapped_row = True
            block_parts = [s]
        else:
            normalized_lines.append(ln)
        continue
    block_parts.append(s)
    if row_end_re.search(s):
        collapsed = " ".join(part.strip() for part in block_parts if part.strip())
        normalized_lines.append(collapsed)
        in_wrapped_row = False
        block_parts = []
if block_parts:
    normalized_lines.append(" ".join(part.strip() for part in block_parts if part.strip()))

norm = "\n".join(normalized_lines)
rows = mod._parse_markdown_table(norm)
print('rows', len(rows))
for i,r in enumerate(rows[:25]):
    print(i, len(r), r)
