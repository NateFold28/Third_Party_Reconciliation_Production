"""Diagnose SHAP join failure and backtest method name."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from connection import fetch_dataframe

DB = "STREAMLIT_APPS.DBO"

# 1. RUN_ID comparison across tables
df1 = fetch_dataframe(f"SELECT MAX(RUN_ID) AS MAX_RUN FROM {DB}.ML_SANDBOX_V5_CONTRACT_SHAP")
df2 = fetch_dataframe(f"SELECT MAX(RUN_ID) AS MAX_RUN FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL WHERE RUN_ID != 'V5_ANCHOR_FALLBACK'")
df3 = fetch_dataframe(f"SELECT MAX(RUN_ID) AS MAX_RUN FROM {DB}.V5_SANDBOX_APP_RUNS")
print("SHAP source latest RUN_ID:     ", df1['MAX_RUN'].iloc[0])
print("Contract detail latest RUN_ID: ", df2['MAX_RUN'].iloc[0])
print("App runs latest RUN_ID:        ", df3['MAX_RUN'].iloc[0])

# 2. Check SHAP date vs detail date alignment
df4 = fetch_dataframe(f"""
    SELECT s.MASTER_DATE,
           DATE_TRUNC('MONTH', s.MASTER_DATE)::DATE AS TRUNC_DATE,
           d.RENEWAL_MONTH,
           d.CONTRACT_ID
    FROM {DB}.ML_SANDBOX_V5_CONTRACT_SHAP s
    JOIN {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL d
      ON s.CONTRACT_ID_UFR = d.CONTRACT_ID
    WHERE s.RUN_ID = (SELECT MAX(RUN_ID) FROM {DB}.ML_SANDBOX_V5_CONTRACT_SHAP)
    LIMIT 5
""")
print("\nJoin sample (SHAP MASTER_DATE vs contract RENEWAL_MONTH):")
print(df4.to_string())

# 3. Check distinct run IDs in SHAP
df5 = fetch_dataframe(f"""
    SELECT RUN_ID, COUNT(*) AS N, COUNT(DISTINCT CONTRACT_ID_UFR) AS CONTRACTS
    FROM {DB}.ML_SANDBOX_V5_CONTRACT_SHAP
    GROUP BY RUN_ID
    ORDER BY RUN_ID DESC
    LIMIT 5
""")
print("\nSHAP source run distribution:")
print(df5.to_string())

# 4. Backtest methods available
df6 = fetch_dataframe(f"""
    SELECT METHOD, COUNT(*) AS N
    FROM {DB}.V5_SANDBOX_APP_BACKTEST
    WHERE RUN_ID = (SELECT MAX(RUN_ID) FROM {DB}.V5_SANDBOX_APP_BACKTEST)
    GROUP BY METHOD
""")
print("\nBacktest methods for latest run:")
print(df6.to_string())

# 5. Anchor fallback diagnostics
df7 = fetch_dataframe(f"""
    SELECT FINANCE_ANCHOR_SOURCE, COUNT(*) AS N,
           ROUND(AVG(CHURN_PCT),1) AS AVG_CHURN_PCT,
           ROUND(SUM(ATR)/1e6,2) AS ATR_M,
           SUM(EARLY_WARNING_FLAG) AS EW
    FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_DISPLAY_CAL = FALSE
    GROUP BY FINANCE_ANCHOR_SOURCE
    ORDER BY N DESC
""")
print("\nAnchor source breakdown (all forward):")
print(df7.to_string())

# 6. Understand anchor contracts — what's different about them?
df8 = fetch_dataframe(f"""
    SELECT SEGMENT, COUNT(*) AS N,
           ROUND(AVG(CHURN_PCT),1) AS AVG_CHURN_PCT
    FROM {DB}.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_DISPLAY_CAL = FALSE
      AND FINANCE_ANCHOR_SOURCE = 'SEGMENT'
    GROUP BY SEGMENT
    ORDER BY N DESC
""")
print("\nAnchor fallback contracts by segment:")
print(df8.to_string())
