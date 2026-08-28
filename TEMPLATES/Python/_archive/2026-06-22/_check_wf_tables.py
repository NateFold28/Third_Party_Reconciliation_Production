"""Schema check for backtest/walk-forward tables + sample data."""
import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from connection import fetch_dataframe

for tbl, n in [("ML_SANDBOX_V5_WALK_FORWARD", 20), ("V5_SANDBOX_APP_BACKTEST", 20)]:
    df = fetch_dataframe(
        f"SELECT * FROM STREAMLIT_APPS.DBO.{tbl} LIMIT {n}"
    )
    print(f"\n=== {tbl} (first {n} rows) ===")
    print(df.to_string())
    print(f"Columns: {list(df.columns)}")
