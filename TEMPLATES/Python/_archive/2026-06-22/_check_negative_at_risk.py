"""
_check_negative_at_risk.py
Diagnose negative AT_RISK_DOLLARS in Business Management portfolio June 2026.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection()

print("=== June 2026 — AT_RISK_DOLLARS by segment ===")
df = fetch_dataframe("""
    SELECT
        SEGMENT,
        COUNT(*) AS CONTRACTS,
        ROUND(SUM(ATR)/1e6, 3)                                                         AS ATR_M,
        ROUND(SUM(AT_RISK_DOLLARS)/1e6, 3)                                             AS AT_RISK_M,
        SUM(CASE WHEN AT_RISK_DOLLARS < 0 THEN 1 ELSE 0 END)                           AS NEG_COUNT,
        ROUND(SUM(CASE WHEN AT_RISK_DOLLARS < 0 THEN AT_RISK_DOLLARS ELSE 0 END), 0)   AS NEG_TOTAL,
        ROUND(MIN(CHURN_PCT), 4)    AS MIN_CHURN_PCT,
        ROUND(MAX(CHURN_PCT), 4)    AS MAX_CHURN_PCT,
        ROUND(MIN(ATR), 2)          AS MIN_ATR,
        ROUND(MAX(ATR), 2)          AS MAX_ATR
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-06-01'
    GROUP BY SEGMENT
    ORDER BY ATR_M DESC
""", conn=conn)
print(df.to_string(index=False))

print()
print("=== Contracts with negative AT_RISK_DOLLARS June 2026 (all segments) ===")
df2 = fetch_dataframe("""
    SELECT CONTRACT_ID, PRODUCT_GROUP, SEGMENT,
           ROUND(ATR, 2)              AS ATR,
           ROUND(CHURN_PCT, 4)        AS CHURN_PCT,
           ROUND(ML_FORECAST, 2)      AS ML_FORECAST,
           ROUND(FINANCE_FORECAST, 2) AS FINANCE_FORECAST,
           ROUND(AT_RISK_DOLLARS, 2)  AS AT_RISK_DOLLARS
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-06-01'
      AND AT_RISK_DOLLARS < 0
    ORDER BY AT_RISK_DOLLARS ASC
    LIMIT 30
""", conn=conn)
print(f"Contracts with negative AT_RISK: {len(df2)}")
if not df2.empty:
    print(df2.to_string(index=False))
    print()
    # Is it ATR < 0 or CHURN_PCT < 0?
    neg_atr   = (df2["ATR"] < 0).sum()
    neg_churn = (df2["CHURN_PCT"] < 0).sum()
    print(f"  Negative ATR: {neg_atr}  |  Negative CHURN_PCT: {neg_churn}")

print()
print("=== Finance Forecast > ATR (over-forecast = negative implied churn) ===")
df3 = fetch_dataframe("""
    SELECT CONTRACT_ID, PRODUCT_GROUP, SEGMENT,
           ROUND(ATR, 2)              AS ATR,
           ROUND(FINANCE_FORECAST, 2) AS FINANCE_FORECAST,
           ROUND(FINANCE_FORECAST - ATR, 2) AS OVER_FORECAST,
           ROUND(CHURN_PCT, 4)        AS CHURN_PCT,
           ROUND(AT_RISK_DOLLARS, 2)  AS AT_RISK_DOLLARS
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-06-01'
      AND FINANCE_FORECAST > ATR
      AND ATR > 0
    ORDER BY OVER_FORECAST DESC
    LIMIT 20
""", conn=conn)
print(f"Contracts where FINANCE_FORECAST > ATR: {len(df3)}")
if not df3.empty:
    print(df3.to_string(index=False))
