"""
Deep-dive: understand why prod has 3,138 rows for July 2026 and sandbox has 2,027.
Goal: find the exact source-table grain that produces 3,138 in prod.
"""
import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection()

print("=" * 70)
print("A. What does CARR__RENEWALS_PORTFOLIO_LVL give at PRODUCT_GROUP_UFR grain?")
print("   Grouping exactly as 02_app_precompute.sql does")
print("=" * 70)
df_a = fetch_dataframe("""
    SELECT
        COUNT(*)                                               AS total_rows,
        COUNT(DISTINCT CONTRACT_ID_UFR)                        AS uniq_contracts,
        COUNT(DISTINCT TRIM(CONTRACT_ID_UFR) || '|' || TRIM(PRODUCT_GROUP_UFR)) AS uniq_cid_pg,
        ROUND(SUM(ADJ_ATR_C_BUDGET_RATE)/1e6, 3)              AS atr_m
    FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
    WHERE INCLUDE_FLAG_C = 1
      AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-07-01'
      -- raw rows (no grouping) to understand native grain
""", conn=conn)
print("Raw rows in CARR__RENEWALS_PORTFOLIO_LVL for July 2026:")
print(df_a.to_string(index=False))

print()
df_a2 = fetch_dataframe("""
    SELECT
        COUNT(*)                                               AS total_rows_after_groupby,
        COUNT(DISTINCT TRIM(CONTRACT_ID_UFR))                  AS uniq_contracts,
        ROUND(SUM(ATR)/1e6, 3)                                 AS atr_m
    FROM (
        SELECT
            TRIM(CONTRACT_ID_UFR) AS CONTRACT_ID_UFR,
            TRIM(PRODUCT_GROUP_UFR) AS PRODUCT_GROUP_UFR,
            DATE_TRUNC('MONTH', MASTER_DATE)::DATE AS RENEWAL_MONTH,
            SUM(COALESCE(ADJ_ATR_C_BUDGET_RATE, 0)) AS ATR
        FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
        WHERE INCLUDE_FLAG_C = 1
          AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-07-01'
        GROUP BY 1, 2, 3
    )
""", conn=conn)
print("After GROUP BY (CONTRACT_ID_UFR, PRODUCT_GROUP_UFR, month) — same as 02_app_precompute.sql:")
print(df_a2.to_string(index=False))

print()
print("=" * 70)
print("B. What does PRODUCT_PORTFOLIO_UFR grain give?")
print("   (old sandbox approach)")
print("=" * 70)
df_b = fetch_dataframe("""
    SELECT
        COUNT(*) AS total_rows_after_groupby,
        COUNT(DISTINCT TRIM(CONTRACT_ID_UFR)) AS uniq_contracts,
        ROUND(SUM(ATR)/1e6, 3) AS atr_m
    FROM (
        SELECT
            TRIM(CONTRACT_ID_UFR) AS CONTRACT_ID_UFR,
            TRIM(PRODUCT_PORTFOLIO_UFR) AS PRODUCT_PORTFOLIO_UFR,
            DATE_TRUNC('MONTH', MASTER_DATE)::DATE AS RENEWAL_MONTH,
            SUM(COALESCE(ADJ_ATR_C_BUDGET_RATE, 0)) AS ATR
        FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
        WHERE INCLUDE_FLAG_C = 1
          AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-07-01'
        GROUP BY 1, 2, 3
    )
""", conn=conn)
print("After GROUP BY (CONTRACT_ID_UFR, PRODUCT_PORTFOLIO_UFR, month):")
print(df_b.to_string(index=False))

print()
print("=" * 70)
print("C. What PRODUCT_GROUP distribution does CARR__RENEWALS_PORTFOLIO_LVL give?")
print("   vs what prod V5_APP_CONTRACT_DETAIL has")
print("=" * 70)
df_c = fetch_dataframe("""
    SELECT
        TRIM(PRODUCT_GROUP_UFR) AS PRODUCT_GROUP_UFR,
        COUNT(*) AS raw_rows,
        COUNT(DISTINCT TRIM(CONTRACT_ID_UFR)) AS uniq_contracts
    FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
    WHERE INCLUDE_FLAG_C = 1
      AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-07-01'
    GROUP BY 1
    ORDER BY raw_rows DESC
""", conn=conn)
print("PRODUCT_GROUP_UFR in CARR__RENEWALS_PORTFOLIO_LVL (raw):")
print(df_c.to_string(index=False))

print()
df_c2 = fetch_dataframe("""
    SELECT PRODUCT_GROUP, COUNT(*) AS row_cnt, COUNT(DISTINCT CONTRACT_ID) AS uniq_contracts
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-07-01'
    GROUP BY 1
    ORDER BY row_cnt DESC
""", conn=conn)
print("PRODUCT_GROUP in V5_APP_CONTRACT_DETAIL (prod):")
print(df_c2.to_string(index=False))

print()
print("=" * 70)
print("D. Is there a PRODUCT_GROUP column on CARR__RENEWALS_PORTFOLIO_LVL")
print("   that differs from PRODUCT_GROUP_UFR?")
print("=" * 70)
df_d = fetch_dataframe("""
    SELECT DISTINCT
        TRIM(PRODUCT_GROUP_UFR) AS product_group_ufr,
        TRIM(PRODUCT_PORTFOLIO_UFR) AS product_portfolio_ufr
    FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
    WHERE INCLUDE_FLAG_C = 1
      AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-07-01'
    ORDER BY 1, 2
    LIMIT 80
""", conn=conn)
print("Distinct (PRODUCT_GROUP_UFR, PRODUCT_PORTFOLIO_UFR) combinations:")
print(df_d.to_string(index=False))
