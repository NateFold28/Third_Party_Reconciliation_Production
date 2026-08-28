import pandas as pd
from pathlib import Path
path = Path(r"C:/Users/Nate.Fold/projects/logs/SentinelOne_Mappings_copy.xlsx")
xl = pd.ExcelFile(path)
print("SHEETS", xl.sheet_names)
if "PARTNER_MAPPING" in xl.sheet_names:
    df = xl.parse("PARTNER_MAPPING")
    print("ROWS", len(df), "COLS", len(df.columns))
    print("COLUMNS", list(df.columns))
    print(df.head(12).to_string(index=False))
