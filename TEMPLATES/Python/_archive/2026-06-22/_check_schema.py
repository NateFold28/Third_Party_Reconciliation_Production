"""Quick schema check for ML_SANDBOX_V5_PREDICTIONS."""
import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from connection import fetch_dataframe

df = fetch_dataframe(
    "SELECT COLUMN_NAME FROM STREAMLIT_APPS.INFORMATION_SCHEMA.COLUMNS "
    "WHERE TABLE_NAME='ML_SANDBOX_V5_PREDICTIONS' "
    "ORDER BY ORDINAL_POSITION"
)
print(df.to_string())
