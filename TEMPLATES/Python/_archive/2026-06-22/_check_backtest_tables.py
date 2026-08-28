"""Find backtest/validation tables and check their columns."""
import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from connection import fetch_dataframe

# What backtest/validation tables exist?
df1 = fetch_dataframe("""
    SELECT TABLE_NAME, ROW_COUNT, LAST_ALTERED
    FROM STREAMLIT_APPS.INFORMATION_SCHEMA.TABLES
    WHERE TABLE_TYPE = 'BASE TABLE'
      AND TABLE_NAME ILIKE '%BACKTEST%' OR TABLE_NAME ILIKE '%VALID%' OR TABLE_NAME ILIKE '%SANDBOX%'
    ORDER BY TABLE_NAME
""")
print("=== TABLES ===")
print(df1.to_string())

# Feature store columns
df2 = fetch_dataframe("""
    SELECT COLUMN_NAME
    FROM STREAMLIT_APPS.INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'ML_SANDBOX_V5_FEATURE_STORE'
    ORDER BY ORDINAL_POSITION
""")
print("\n=== FEATURE STORE COLUMNS ===")
print(df2.to_string())

# Backtest table columns (sandbox)
df3 = fetch_dataframe("""
    SELECT COLUMN_NAME
    FROM STREAMLIT_APPS.INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'V5_SANDBOX_APP_BACKTEST'
    ORDER BY ORDINAL_POSITION
""")
print("\n=== V5_SANDBOX_APP_BACKTEST COLUMNS ===")
print(df3.to_string())
