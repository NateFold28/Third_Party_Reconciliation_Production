from pathlib import Path
from collections import defaultdict

root = Path(r"C:/Users/Nate.Fold/OneDrive - ConnectWise, Inc/THIRD_PARTY_RECONCILIATION/Manual Recon Files 2026/Acronis")
tokens = ['SPQCMSENS','SPBAMSENS','SPDAMSENS','SVEAMSENS']
counts = {t: defaultdict(int) for t in tokens}

for p in root.rglob('*.csv'):
    if '2026' not in p.as_posix() and 'May' not in p.name and 'MAY' not in p.name:
        pass
    text = p.read_text(encoding='utf-8', errors='ignore').upper()
    for t in tokens:
        c = text.count(t)
        if c:
            folder = p.parent.name
            counts[t][folder] += c

for t in tokens:
    total = sum(counts[t].values())
    print(f'\\n{t}: total_mentions={total}')
    for folder, c in sorted(counts[t].items(), key=lambda x: (-x[1], x[0])):
        print(f'  {folder}: {c}')
