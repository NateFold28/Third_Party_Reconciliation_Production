"""
Finance dashboard (CARR__RENEWALS_CONTRACT_LVL) vs App portfolio grain
Jan-Jun 2026 side-by-side.

Finance Power BI measures map to:
  gross_realized_renewal_contract  -> ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE
  adj_atr_contract                 -> ADJ_ATR_C_BUDGET_RATE
  % gross_realized_renewal_rate    -> ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE / ADJ_ATR_C_BUDGET_RATE
"""
import sys
sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection, fetch_dataframe
import pandas as pd

conn = get_snowflake_connection()

# ------------------------------------------------------------------
# FINANCE SOURCE — contract grain from CARR view (what Power BI reads)
# Filter: INCLUDE_FLAG_C = 1 (active/in-scope contracts only)
# This is the exact same source the Finance dashboard is built from.
# ------------------------------------------------------------------
finance_q = """
SELECT
    DATE_TRUNC('MONTH', MASTER_DATE)                          AS MONTH,
    COUNT(*)                                                   AS FINANCE_N_CONTRACTS,
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE) / 1e6, 2)               AS FINANCE_ATR_M,
    ROUND(SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE, 0)) / 1e6, 2)  AS FINANCE_RENEWED_M,
    ROUND(
        SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE, 0))
        / NULLIF(SUM(ADJ_ATR_C_BUDGET_RATE), 0) * 100
    , 2)                                                       AS FINANCE_RATE_PCT
FROM ANALYTICS.DBO.CARR__RENEWALS_CONTRACT_LVL
WHERE INCLUDE_FLAG_C = 1
  AND DATE_TRUNC('MONTH', MASTER_DATE) BETWEEN '2026-01-01' AND '2026-06-01'
GROUP BY 1
ORDER BY 1
"""

# ------------------------------------------------------------------
# APP PORTFOLIO GRAIN — what the app actually displays
# The app patches ATR and ACTUAL from the PRODUCTION table
# (T_APP_DETAIL_TRUTH = V5_APP_CONTRACT_DETAIL, not sandbox).
# This is the Finance-blessed CARR spine — same source Power BI reads.
# ------------------------------------------------------------------
app_q = """
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)                        AS MONTH,
    COUNT(*)                                                   AS APP_N_ROWS,
    COUNT(DISTINCT CONTRACT_ID)                               AS APP_N_CONTRACTS,
    ROUND(SUM(ATR) / 1e6, 2)                                  AS APP_ATR_M,
    ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR, 0)) / 1e6, 2)    AS APP_RENEWED_M,
    ROUND(
        SUM(COALESCE(ACTUAL_RETAINED_ARR, 0))
        / NULLIF(SUM(ATR), 0) * 100
    , 2)                                                       AS APP_RATE_PCT,
    -- Flag: is this month fully settled (no open opps)?
    MAX(CASE WHEN IS_MATURED_MONTH = TRUE THEN 1 ELSE 0 END)  AS MONTH_IS_SETTLED
FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH BETWEEN '2026-01-01' AND '2026-06-01'
GROUP BY 1
ORDER BY 1
"""

print("Fetching Finance source (CARR__RENEWALS_CONTRACT_LVL, INCLUDE_FLAG_C=1)...")
fdf = fetch_dataframe(finance_q, conn=conn)

print("Fetching App portfolio grain (V5_SANDBOX_APP_CONTRACT_DETAIL)...")
adf = fetch_dataframe(app_q, conn=conn)

fdf['MONTH'] = pd.to_datetime(fdf['MONTH'])
adf['MONTH'] = pd.to_datetime(adf['MONTH'])

merged = fdf.merge(adf, on='MONTH', how='outer').sort_values('MONTH')

# Gap columns
merged['RATE_GAP_PP']    = (merged['FINANCE_RATE_PCT'] - merged['APP_RATE_PCT']).round(2)
merged['ATR_GAP_M']      = (merged['FINANCE_ATR_M'] - merged['APP_ATR_M']).round(2)
merged['RENEWED_GAP_M']  = (merged['FINANCE_RENEWED_M'] - merged['APP_RENEWED_M']).round(2)
merged['SETTLED']        = merged['MONTH_IS_SETTLED'].map({1: 'YES', 0: 'OPEN'}).fillna('?')

merged['MONTH_STR'] = merged['MONTH'].dt.strftime('%b %Y')

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 220)
pd.set_option('display.float_format', '{:.2f}'.format)

# ------------------------------------------------------------------
# TABLE 1: Rate comparison
# ------------------------------------------------------------------
print()
print("=" * 110)
print("TABLE 1 — RENEWAL RATE: Finance dashboard vs App portfolio grain  (Jan–Jun 2026)")
print("=" * 110)
rate_cols = ['MONTH_STR', 'SETTLED',
             'FINANCE_N_CONTRACTS', 'FINANCE_ATR_M', 'FINANCE_RENEWED_M', 'FINANCE_RATE_PCT',
             'APP_N_CONTRACTS',     'APP_ATR_M',     'APP_RENEWED_M',     'APP_RATE_PCT',
             'RATE_GAP_PP', 'ATR_GAP_M', 'RENEWED_GAP_M']
print(merged[rate_cols].to_string(index=False))

print()
print("  FINANCE_RATE_PCT  = SUM(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE) / SUM(ADJ_ATR_C_BUDGET_RATE)")
print("                      This is exactly 'CARR__RENEWALS_CONTRACT_LVL'[% gross_realized_renewal_rate]")
print("  APP_RATE_PCT      = SUM(ACTUAL_RETAINED_ARR) / SUM(ATR)  — portfolio grain from V5_APP_CONTRACT_DETAIL (production, Finance-blessed)")
print("  RATE_GAP_PP       = Finance rate MINUS App rate (positive = Finance shows higher)")
print("  SETTLED           = YES if open_opp = 0 and past month-end (IS_MATURED_MONTH = TRUE)")

# ------------------------------------------------------------------
# TABLE 2: Contract count reconciliation
# ------------------------------------------------------------------
print()
print("=" * 110)
print("TABLE 2 — CONTRACT COUNT reconciliation")
print("=" * 110)
count_cols = ['MONTH_STR', 'SETTLED', 'FINANCE_N_CONTRACTS', 'APP_N_CONTRACTS', 'APP_N_ROWS']
t2 = merged[count_cols].copy()
t2['CONTRACT_GAP']   = (merged['FINANCE_N_CONTRACTS'] - merged['APP_N_CONTRACTS']).astype('Int64')
t2['PORTFOLIO_ROWS_PER_CONTRACT'] = (merged['APP_N_ROWS'] / merged['APP_N_CONTRACTS']).round(2)
print(t2.to_string(index=False))

print()
print("  FINANCE_N_CONTRACTS     = one row per contract in CARR source")
print("  APP_N_CONTRACTS         = unique CONTRACT_IDs in app table")
print("  APP_N_ROWS              = total portfolio rows (contracts × portfolios)")
print("  PORTFOLIO_ROWS_PER_CONTRACT = how many portfolio splits per contract on avg")

# ------------------------------------------------------------------
# TABLE 3: Dollar-level ATR and renewed
# ------------------------------------------------------------------
print()
print("=" * 110)
print("TABLE 3 — DOLLAR TOTALS  ($M)")
print("=" * 110)
dollar_cols = ['MONTH_STR', 'SETTLED',
               'FINANCE_ATR_M', 'APP_ATR_M', 'ATR_GAP_M',
               'FINANCE_RENEWED_M', 'APP_RENEWED_M', 'RENEWED_GAP_M']
print(merged[dollar_cols].to_string(index=False))

print()
print("  ATR_GAP_M     = Finance ATR minus App ATR  (should be ~0 if same source)")
print("  RENEWED_GAP_M = Finance renewed minus App renewed")
print("  Small positive RENEWED_GAP is expected: Finance caps at ATR (no expansion credit);")
print("  App ACTUAL_RETAINED_ARR can include expansion above ATR from ALLOCATED column.")

conn.close()
print()
print("Done.")
