import importlib.util
import re
from pathlib import Path
from TEMPLATES.Python.connection import fetch_dataframe

mod_path = Path('PROJECTS/Third_Party_Reconciliation/Combined_Recon_Prod_Pipeline/Ingestion/Netsuite_Invoice_JSON_Ingestion_Prod.py')
spec = importlib.util.spec_from_file_location('inv_ing', mod_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

row = fetch_dataframe("""
select parsed_document from NETSUITE.DBO.PARSED_VENDOR_DATA
where file_path ilike '%INV124686%' and vendor_name ilike '%sentinelone%'
limit 1
""").iloc[0]
text = mod._extract_text(row['PARSED_DOCUMENT'])

results = []
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

rows = mod._parse_markdown_table("\n".join(normalized_lines))
print('rows total', len(rows))
header_idx = None
last_line_item_idx = None
for ridx,row in enumerate(rows):
    cells=[str(c if c is not None else '').strip() for c in row]
    lowered=[c.lower() for c in cells]
    if (
        any('product code' in c for c in lowered)
        and any('inv qty' in c or c == 'qty' for c in lowered)
        and any(c == 'rate' for c in lowered)
        and any('amount' in c for c in lowered)
    ):
        i_code = next(i for i,c in enumerate(lowered) if 'product code' in c)
        i_qty = next(i for i,c in enumerate(lowered) if 'inv qty' in c or c == 'qty')
        i_rate = next(i for i,c in enumerate(lowered) if c == 'rate')
        i_amt = next(i for i,c in enumerate(lowered) if 'amount' in c)
        header_idx=(i_code,i_qty,i_rate,i_amt)
        print('header at',ridx,header_idx,cells)
        last_line_item_idx=None
        continue
    if header_idx is None:
        continue
    i_code,i_qty,i_rate,i_amt=header_idx
    if len(cells)<=max(i_code,i_qty,i_rate,i_amt):
        print('skip short',ridx,cells)
        continue
    code_cell=cells[i_code]
    qty=mod._num_sentinelone_qty(cells[i_qty])
    rate=mod._num(cells[i_rate])
    amt=mod._num(cells[i_amt])
    if qty is None and rate is None and amt is None:
        if last_line_item_idx is not None and code_cell:
            desc_text=re.sub(r'\s+',' ',code_cell).strip()
            if desc_text and not re.match(r'^(subtotal|tax total|total)\b',desc_text,flags=re.I):
                prior=results[last_line_item_idx].get('description') or ''
                results[last_line_item_idx]['description']=(f"{prior} {desc_text}".strip() if prior else desc_text)
                print('attach desc',ridx,results[last_line_item_idx]['sku'])
        continue
    if qty is None:
        print('skip no qty',ridx,cells)
        continue
    code_text=re.sub(r'\s+',' ',code_cell).strip()
    m=re.match(r'^([A-Z0-9]+(?:-[A-Z0-9]+)+)\b\s*(.*)$',code_text)
    if not m:
        print('skip msku',ridx,code_text,qty,rate,amt)
        continue
    sku=m.group(1).strip(); desc=m.group(2).strip()
    results.append({'sku':sku,'description':desc,'quantity':qty,'unit_price':rate,'amount':amt})
    last_line_item_idx=len(results)-1
    print('add',ridx,sku,qty,rate,amt)

print('final',len(results))
for r in results: print(r)
