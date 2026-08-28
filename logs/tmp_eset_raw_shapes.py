import json
from TEMPLATES.Python.connection import fetch_dataframe

files = [
'101258935_ESET_Software_Australia_PTY_LTD_6d32af91cf2e4f06_Invoice_12248819.pdf',
'101258936_ESET_Software_Australia_PTY_LTD_3ebcbd45a2af43ca_Invoice_12248820.pdf',
'101259680_ESET_UK_37d82a771ee94408_Invoice_151051082.pdf',
'101007984_ESET_LLC_17a006cd8fe1479d_Invoice_021801340.pdf',
]
for f in files:
    print('\n' + '='*120)
    print(f)
    df = fetch_dataframe(f"""
    select parsed_document
    from NETSUITE.DBO.PARSED_VENDOR_DATA
    where file_path ilike '%{f}%'
    limit 1
    """)
    if df.empty:
        print('not found');
        continue
    doc = json.loads(df.iloc[0]['PARSED_DOCUMENT'])
    pages = doc.get('pages',[])
    txt='\n\n'.join(p.get('content','') for p in pages)
    # print first chunk for context
    print('--- first 120 lines ---')
    for i,ln in enumerate(txt.splitlines()[:120], start=1):
        print(f"{i:03d}: {ln}")
    print('--- markdown-like lines containing pipes ---')
    for ln in txt.splitlines():
        s=ln.strip()
        if s.startswith('|'):
            print(s)
