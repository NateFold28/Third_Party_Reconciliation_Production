"""Mirror _get_blended_netting_pp() exactly to find where it fails."""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()

def safe_query(sql):
    import pandas as pd
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return pd.DataFrame(rows, columns=cols)

# --- Mirror _load_contract_monthly ---
_contract = safe_query("""
    SELECT RENEWAL_MONTH, N_CONTRACTS AS CONTRACT_N,
           CONTRACT_ATR, CONTRACT_RENEWED,
           CONTRACT_RATE_PCT, CONTRACT_FORECAST_RATE_PCT,
           CONTRACT_ML_RAW_RATE_PCT, CONTRACT_FORECAST_DOLLARS,
           CONTRACT_ACTUAL_VS_FORECAST_PP
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
    WHERE RENEWAL_MONTH >= '2021-02-01'
    ORDER BY RENEWAL_MONTH
""")

# --- Mirror _load_prod_monthly_finance ---
_prod_mf = safe_query("""
    SELECT RENEWAL_MONTH, ATR_PROD, ACTUAL_PROD
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED
    WHERE RENEWAL_MONTH >= '2021-02-01'
    ORDER BY RENEWAL_MONTH
""")

print(f"_contract rows: {len(_contract)}, empty={_contract.empty}")
print(f"_prod_mf rows:  {len(_prod_mf)}, empty={_prod_mf.empty}")
print()

print("_contract RENEWAL_MONTH dtype:", _contract["RENEWAL_MONTH"].dtype)
print("_prod_mf  RENEWAL_MONTH dtype:", _prod_mf["RENEWAL_MONTH"].dtype)
print("_contract CONTRACT_RATE_PCT dtype:", _contract["CONTRACT_RATE_PCT"].dtype)
print("_contract CONTRACT_RATE_PCT sample:", _contract["CONTRACT_RATE_PCT"].head(3).tolist())
print()

# Step-by-step mirror
_contract["MONTH"] = pd.to_datetime(_contract["RENEWAL_MONTH"], errors="coerce") \
                        .dt.to_period("M").dt.to_timestamp()
_prod_mf["MONTH"]  = pd.to_datetime(_prod_mf["RENEWAL_MONTH"], errors="coerce") \
                        .dt.to_period("M").dt.to_timestamp()

print("_contract MONTH sample:", _contract["MONTH"].head(3).tolist())
print("_prod_mf  MONTH sample:", _prod_mf["MONTH"].head(3).tolist())
print()

_port = (
    _prod_mf.groupby("MONTH", as_index=False)
    .agg(port_atr=("ATR_PROD", "sum"), port_actual=("ACTUAL_PROD", "sum"))
)
_port["ACTUAL_PCT"] = _port["port_actual"] / _port["port_atr"].replace(0, np.nan) * 100
print("_port ACTUAL_PCT sample:", _port["ACTUAL_PCT"].head(3).tolist())
print()

_merged = _contract[["MONTH", "CONTRACT_RATE_PCT"]].merge(
    _port[["MONTH", "ACTUAL_PCT"]], on="MONTH", how="inner"
)
print(f"Merged rows: {len(_merged)}")

_mature = _merged[
    _merged["MONTH"] < pd.Timestamp.now().to_period("M").to_timestamp()
].dropna(subset=["CONTRACT_RATE_PCT", "ACTUAL_PCT"])
print(f"Mature rows: {len(_mature)}")

if len(_mature) >= 4:
    print("CONTRACT_RATE_PCT dtype in merged:", _mature["CONTRACT_RATE_PCT"].dtype)
    print("ACTUAL_PCT dtype in merged:        ", _mature["ACTUAL_PCT"].dtype)
    try:
        _gaps = _mature["CONTRACT_RATE_PCT"] - _mature["ACTUAL_PCT"]
        print("Gaps computed OK:", _gaps.head(3).tolist())
        _sorted = _gaps.sort_values().reset_index(drop=True)
        result = float(_sorted.iloc[2:-2].mean()) if len(_sorted) > 4 else float(_sorted.mean())
        print(f"\n>>> RESULT: {result:.4f} pp  (would be this in the app, not 1.6)")
    except Exception as e:
        print(f"EXCEPTION in gap calc: {e}")
else:
    print(">>> FALLBACK: < 4 mature rows → returns 1.6")

conn.close()
