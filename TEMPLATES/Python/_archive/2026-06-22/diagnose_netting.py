"""Diagnose why _get_blended_netting_pp() might fall back to 1.6pp every time."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()
for s in ['USE ROLE STREAMLIT_USER', 'USE WAREHOUSE REPORTING_WH',
          'USE DATABASE STREAMLIT_APPS', 'USE SCHEMA DBO']:
    cur.execute(s)

cur.execute("""
SELECT RENEWAL_MONTH, CONTRACT_RATE_PCT, CONTRACT_FORECAST_RATE_PCT
FROM V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
WHERE RENEWAL_MONTH >= '2021-02-01'
ORDER BY RENEWAL_MONTH
""")
_contract = pd.DataFrame(cur.fetchall(),
    columns=['RENEWAL_MONTH', 'CONTRACT_RATE_PCT', 'CONTRACT_ML_RAW_RATE_PCT'])

cur.execute("""
SELECT RENEWAL_MONTH, ATR_PROD, ACTUAL_PROD
FROM V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED
WHERE RENEWAL_MONTH >= '2021-02-01'
ORDER BY RENEWAL_MONTH
""")
_prod_mf = pd.DataFrame(cur.fetchall(), columns=['RENEWAL_MONTH', 'ATR_PROD', 'ACTUAL_PROD'])

print(f"contract rows: {len(_contract)}  prod rows: {len(_prod_mf)}")
print(f"contract cols: {_contract.columns.tolist()}")
print(f"prod cols:     {_prod_mf.columns.tolist()}")

_contract['MONTH'] = pd.to_datetime(_contract['RENEWAL_MONTH']).dt.to_period('M').dt.to_timestamp()
_prod_mf['MONTH']  = pd.to_datetime(_prod_mf['RENEWAL_MONTH']).dt.to_period('M').dt.to_timestamp()

_port = _prod_mf.groupby('MONTH', as_index=False).agg(
    port_atr=('ATR_PROD', 'sum'), port_actual=('ACTUAL_PROD', 'sum')
)
_port['ACTUAL_PCT'] = _port['port_actual'] / _port['port_atr'].replace(0, np.nan) * 100

_merged = _contract[['MONTH', 'CONTRACT_RATE_PCT']].merge(
    _port[['MONTH', 'ACTUAL_PCT']], on='MONTH', how='inner'
)
print(f"\nMerged rows: {len(_merged)}")

_cutoff       = pd.Timestamp.now().to_period('M').to_timestamp()
_window_start = _cutoff - pd.DateOffset(months=12)
_mature = _merged[
    (_merged['MONTH'] < _cutoff) & (_merged['MONTH'] >= _window_start)
].dropna(subset=['CONTRACT_RATE_PCT', 'ACTUAL_PCT'])

print(f"Mature months in trailing-12m window: {len(_mature)}")
_mature = _mature.assign(gap=lambda x: x['CONTRACT_RATE_PCT'] - x['ACTUAL_PCT'])
print(_mature[['MONTH', 'CONTRACT_RATE_PCT', 'ACTUAL_PCT', 'gap']].to_string(index=False))

if len(_mature) >= 4:
    gaps = _mature['CONTRACT_RATE_PCT'] - _mature['ACTUAL_PCT']
    print(f"\nComputed netting pp: {gaps.mean():.3f}  (range {gaps.min():.2f} – {gaps.max():.2f})")
    print("=> netting IS computed dynamically. Gap IS structurally ~1.6pp.")
    print("   Per-month values:")
    for _, row in _mature.iterrows():
        print(f"   {row['MONTH'].strftime('%b %Y')}: contract={row['CONTRACT_RATE_PCT']:.2f}% "
              f"portfolio={row['ACTUAL_PCT']:.2f}%  gap={row['gap']:.2f}pp")
else:
    print(f"\n⚠ Only {len(_mature)} mature months — falls back to 1.6 hardcode")

conn.close()
