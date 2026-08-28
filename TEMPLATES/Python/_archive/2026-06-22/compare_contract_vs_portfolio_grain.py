"""
Compare contract-grain (feature store) vs portfolio-grain (app table)
renewal rates for Jan-May 2026 — side by side for Finance dashboard alignment.

Contract grain  = ML_SANDBOX_V5_FEATURE_STORE  (one row per CONTRACT_ID_UFR)
Portfolio grain = V5_SANDBOX_APP_CONTRACT_DETAIL (one row per CONTRACT_ID + PRODUCT_PORTFOLIO)
"""
import sys
sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection()

# ------------------------------------------------------------------
# Contract grain — from feature store (Finance formula: RENEWED / ATR)
# TARGET__RENEWED_AMOUNT is already capped at ATR (expansion excluded)
# We also pull the uncapped version so we can see any expansion effect
# ------------------------------------------------------------------
contract_q = """
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)          AS MONTH,
    COUNT(DISTINCT CONTRACT_ID_UFR)              AS N_CONTRACTS,
    ROUND(SUM(ATR) / 1e6, 1)                     AS ATR_M,
    -- Contract rate using TARGET__RENEWED_AMOUNT (capped at ATR — expansion excluded)
    -- This IS the Finance formula at contract grain (expansion doesn't count above 100%)
    ROUND(SUM(TARGET__RENEWED_AMOUNT)
          / NULLIF(SUM(ATR), 0) * 100, 2)                     AS CONTRACT_RATE_PCT,
    ROUND(SUM(TARGET__RENEWED_AMOUNT) / 1e6, 1)               AS CONTRACT_RENEWED_M,
    -- Weighted avg of per-row rates (slightly different from aggregate — for reference)
    ROUND(AVG(TARGET__RENEWAL_RATE) * 100, 2)                 AS CONTRACT_RATE_ROW_AVG_PCT,
    -- Dollar churn rate = lost dollars / ATR
    ROUND((SUM(ATR) - SUM(TARGET__RENEWED_AMOUNT))
          / NULLIF(SUM(ATR), 0) * 100, 2)                     AS CONTRACT_DOLLAR_CHURN_PCT
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE
WHERE RENEWAL_MONTH BETWEEN '2026-01-01' AND '2026-05-01'
  AND HORIZON = 0              -- settlement row only (one row per contract per month)
  AND SPLIT IN ('CAL', 'VALIDATION', 'FORWARD')
GROUP BY 1
ORDER BY 1
"""

# ------------------------------------------------------------------
# Portfolio grain — from app table (IS_MATURED_MONTH = TRUE for settled months)
# This is the Finance board formula exactly
# ------------------------------------------------------------------
portfolio_q = """
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)                              AS MONTH,
    COUNT(*)                                                         AS N_PORTFOLIO_ROWS,
    COUNT(DISTINCT CONTRACT_ID)                                      AS N_UNIQUE_CONTRACTS,
    ROUND(SUM(ATR) / 1e6, 1)                                        AS ATR_M,
    -- Finance board formula
    ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR, 0))
          / NULLIF(SUM(ATR), 0) * 100, 2)                           AS PORTFOLIO_RATE_PCT,
    ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR, 0)) / 1e6, 1)          AS PORTFOLIO_RENEWED_M,
    -- Dollar churn at portfolio grain
    ROUND((SUM(ATR) - SUM(COALESCE(ACTUAL_RETAINED_ARR, 0)))
          / NULLIF(SUM(ATR), 0) * 100, 2)                           AS PORTFOLIO_DOLLAR_CHURN_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH BETWEEN '2026-01-01' AND '2026-05-01'
  AND IS_MATURED_MONTH = TRUE
GROUP BY 1
ORDER BY 1
"""

print("Fetching contract grain (feature store)...")
cdf = fetch_dataframe(contract_q, conn=conn)

print("Fetching portfolio grain (app table)...")
pdf = fetch_dataframe(portfolio_q, conn=conn)

# ------------------------------------------------------------------
# Merge and display side by side
# ------------------------------------------------------------------
import pandas as pd

cdf['MONTH'] = pd.to_datetime(cdf['MONTH'])
pdf['MONTH'] = pd.to_datetime(pdf['MONTH'])

merged = cdf.merge(pdf, on='MONTH', suffixes=('_c', '_p'))

# Key comparison columns
merged['RATE_GAP_PP'] = (merged['CONTRACT_RATE_PCT'] - merged['PORTFOLIO_RATE_PCT']).round(2)
merged['ATR_GAP_M'] = (merged['ATR_M_p'] - merged['ATR_M_c']).round(1)
merged['RENEWED_GAP_M'] = (merged['PORTFOLIO_RENEWED_M'] - merged['CONTRACT_RENEWED_M']).round(1)

print()
print("=" * 90)
print("SIDE-BY-SIDE: Contract Grain (feature store) vs Portfolio Grain (app table)")
print("=" * 90)

display_cols = [
    'MONTH',
    'CONTRACT_RATE_PCT',       # Contract grain renewal % (feature store, capped)
    'PORTFOLIO_RATE_PCT',      # Portfolio grain renewal % (Finance board)
    'RATE_GAP_PP',             # How much higher contract grain is
    'ATR_M_c', 'ATR_M_p', 'ATR_GAP_M',
    'CONTRACT_RENEWED_M', 'PORTFOLIO_RENEWED_M', 'RENEWED_GAP_M',
]

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 200)
pd.set_option('display.float_format', '{:.2f}'.format)

out = merged[display_cols].copy()
out['MONTH'] = out['MONTH'].dt.strftime('%Y-%m')
print(out.to_string(index=False))

print()
print("=" * 90)
print("INTERPRETATION GUIDE")
print("=" * 90)
print("""
CONTRACT_RATE_PCT      = Feature store grain (one row per contract, capped at ATR).
                         Formula: SUM(TARGET__RENEWED_AMOUNT) / SUM(ATR)
                         This is also what the ML model trains on.
                         Expansion contracts are capped at 100% (Finance standard).

PORTFOLIO_RATE_PCT     = App table grain (one row per contract × product portfolio)
                         Formula: SUM(ACTUAL_RETAINED_ARR) / SUM(ATR) — Finance board formula
                         This is what Finance sees and what the Sales team sees by portfolio.

RATE_GAP_PP            = CONTRACT_RATE - PORTFOLIO_RATE
                         If positive: contract grain reads higher than Finance board.
                         Main cause: the app splits multi-portfolio contracts into rows.
                         If Security portfolio churns but Unified renews, portfolio grain
                         shows one win + one loss. Contract grain may see just the combined.

ATR_GAP_M              = Portfolio ATR minus Contract ATR (should be ~0 if same data)
RENEWED_GAP_M          = Portfolio renewed minus Contract renewed (same source, different grain)
""")

print()
print("=" * 90)
print("CHURN COMPARISON (dollar churn %)")
print("=" * 90)
churn = merged[['MONTH', 'CONTRACT_DOLLAR_CHURN_PCT', 'PORTFOLIO_DOLLAR_CHURN_PCT']].copy()
churn['CHURN_GAP_PP'] = (churn['CONTRACT_DOLLAR_CHURN_PCT'] - churn['PORTFOLIO_DOLLAR_CHURN_PCT']).round(2)
print(churn.to_string(index=False))

conn.close()
