"""
Diagnose why July 2026 forecast jumped from ~70% to ~75.9% after grain fix.
Checks: netting pp, monthly rollup rates, model portfolio rates, and _load_prod_monthly_finance columns.
"""
import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection()

print("=" * 70)
print("A. V5_SANDBOX_APP_MONTHLY_ROLLUP — what does sandbox show for Jul 2026?")
print("   (model rate = FINANCE_FORECAST/ATR, effective = EFFECTIVE_FORECAST_FINANCE/ATR)")
print("=" * 70)
df_a = fetch_dataframe("""
    SELECT
        MONTH AS RENEWAL_MONTH,
        CONTRACTS,
        ROUND(ATR/1e6, 3) AS atr_m,
        ROUND(ML_FORECAST/1e6, 3) AS ml_fcst_m,
        ROUND(FINANCE_FORECAST/1e6, 3) AS finance_fcst_m,
        ROUND(EFFECTIVE_FORECAST_FINANCE/1e6, 3) AS eff_fcst_m,
        ROUND(FINANCE_FORECAST / NULLIF(ATR, 0) * 100, 2) AS model_rate_pct,
        ROUND(EFFECTIVE_FORECAST_FINANCE / NULLIF(ATR, 0) * 100, 2) AS eff_rate_pct,
        ROUND(ACTUAL/1e6, 3) AS actual_m,
        ROUND(ACTUAL / NULLIF(ATR, 0) * 100, 2) AS actual_pct
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_MONTHLY_ROLLUP
    WHERE MONTH >= '2026-01-01' AND MONTH <= '2026-09-01'
    ORDER BY MONTH
""", conn=conn)
print(df_a.to_string(index=False))

print()
print("=" * 70)
print("B. V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY — what does contract grain show?")
print("   (this is what _load_contract_monthly() returns)")
print("=" * 70)
df_b = fetch_dataframe("""
    SELECT
        RENEWAL_MONTH,
        CONTRACT_ATR/1e6 AS contract_atr_m,
        CONTRACT_RENEWED/1e6 AS contract_renewed_m,
        CONTRACT_RATE_PCT,
        CONTRACT_FORECAST_RATE_PCT,
        CONTRACT_FORECAST_DOLLARS/1e6 AS contract_fcst_m
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
    WHERE RENEWAL_MONTH >= '2026-01-01' AND RENEWAL_MONTH <= '2026-09-01'
    ORDER BY RENEWAL_MONTH
""", conn=conn)
print(df_b.to_string(index=False))

print()
print("=" * 70)
print("C. V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED — what does this return?")
print("   (this is _load_prod_monthly_finance() source — columns matter)")
print("=" * 70)
df_c = fetch_dataframe("""
    SELECT *
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED
    WHERE RENEWAL_MONTH >= '2026-01-01' AND RENEWAL_MONTH <= '2026-09-01'
    ORDER BY RENEWAL_MONTH
    LIMIT 30
""", conn=conn)
print(df_c.to_string(index=False))
print("Columns:", list(df_c.columns))

print()
print("=" * 70)
print("D. Computed netting pp (Jan-May 2026 matured months):")
print("   gap = CONTRACT_RATE_PCT - (ATR_portfolio_actual / ATR * 100)")
print("=" * 70)
df_d = fetch_dataframe("""
    WITH contract_m AS (
        SELECT
            RENEWAL_MONTH,
            CONTRACT_RATE_PCT
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
        WHERE RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
    ),
    portfolio_m AS (
        SELECT
            DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
            SUM(ACTUAL_RETAINED_ARR) AS actual_port,
            SUM(ATR) AS atr_port,
            SUM(ACTUAL_RETAINED_ARR) / NULLIF(SUM(ATR), 0) * 100 AS actual_pct_port
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE IS_MATURED_MONTH = TRUE
        GROUP BY 1
    )
    SELECT
        c.RENEWAL_MONTH,
        ROUND(c.CONTRACT_RATE_PCT, 2) AS contract_pct,
        ROUND(p.actual_pct_port, 2) AS portfolio_pct,
        ROUND(c.CONTRACT_RATE_PCT - p.actual_pct_port, 2) AS netting_gap_pp
    FROM contract_m c
    JOIN portfolio_m p ON c.RENEWAL_MONTH = p.RENEWAL_MONTH
    ORDER BY c.RENEWAL_MONTH
""", conn=conn)
print(df_d.to_string(index=False))
if not df_d.empty:
    import pandas as pd
    import numpy as np
    gaps = df_d["NETTING_GAP_PP"].dropna()
    if len(gaps) >= 4:
        _s = gaps.sort_values().reset_index(drop=True)
        trimmed = float(_s.iloc[2:-2].mean()) if len(_s) > 4 else float(_s.mean())
        print(f"\n  Trimmed-mean netting pp (what _get_blended_netting_pp returns): {trimmed:.3f}pp")
    else:
        print(f"  Only {len(gaps)} matured months — bootstrap 1.6pp fallback applies")

print()
print("=" * 70)
print("E. What is the prod V5_APP_CONTRACT_DETAIL showing for July?")
print("   Compare to sandbox to see if there's still a rate gap")
print("=" * 70)
df_e = fetch_dataframe("""
    SELECT
        'PROD' AS src,
        ROUND(SUM(FINANCE_FORECAST)/NULLIF(SUM(ATR),0)*100, 2) AS finance_rate,
        ROUND(SUM(EFFECTIVE_FORECAST_FINANCE)/NULLIF(SUM(ATR),0)*100, 2) AS eff_rate,
        ROUND(SUM(ATR)/1e6, 3) AS atr_m,
        COUNT(*) AS cnt
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-07-01'
    UNION ALL
    SELECT
        'SANDBOX',
        ROUND(SUM(FINANCE_FORECAST)/NULLIF(SUM(ATR),0)*100, 2),
        ROUND(SUM(EFFECTIVE_FORECAST_FINANCE)/NULLIF(SUM(ATR),0)*100, 2),
        ROUND(SUM(ATR)/1e6, 3),
        COUNT(*)
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-07-01'
""", conn=conn)
print(df_e.to_string(index=False))
