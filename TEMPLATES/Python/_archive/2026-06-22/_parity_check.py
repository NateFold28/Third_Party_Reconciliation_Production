"""
Diagnose prod vs sandbox row count parity gap.
For July 2026: prod has 3,138 rows, sandbox has 2,027 rows — same ATR ($31.82M).
Goal: understand WHY and whether it causes display differences.
"""
import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection()

print("=" * 60)
print("1. CONTRACT_ID grain: prod vs sandbox for July 2026")
print("   How many unique CONTRACT_IDs in each?")
print("=" * 60)
df1 = fetch_dataframe("""
    SELECT 'PROD'    AS src,
           COUNT(DISTINCT CONTRACT_ID)    AS uniq_contracts,
           COUNT(DISTINCT CONTRACT_ID || '|' || PRODUCT_GROUP) AS uniq_cid_pg,
           COUNT(*)                       AS total_rows,
           ROUND(SUM(ATR)/1e6, 3)         AS atr_m
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-07-01'
    UNION ALL
    SELECT 'SANDBOX',
           COUNT(DISTINCT CONTRACT_ID),
           COUNT(DISTINCT CONTRACT_ID || '|' || PRODUCT_GROUP),
           COUNT(*),
           ROUND(SUM(ATR)/1e6, 3)
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-07-01'
""", conn=conn)
print(df1.to_string(index=False))
print()

print("=" * 60)
print("2. Segment distribution July 2026: prod vs sandbox")
print("=" * 60)
df2 = fetch_dataframe("""
    SELECT SEGMENT,
           COUNT(DISTINCT CASE WHEN src='PROD'    THEN cid_pg END) AS prod_contracts,
           COUNT(DISTINCT CASE WHEN src='SANDBOX' THEN cid_pg END) AS sandbox_contracts,
           ROUND(SUM(CASE WHEN src='PROD'    THEN ATR ELSE 0 END)/1e6, 3) AS prod_atr_m,
           ROUND(SUM(CASE WHEN src='SANDBOX' THEN ATR ELSE 0 END)/1e6, 3) AS sandbox_atr_m
    FROM (
      SELECT 'PROD' AS src, SEGMENT, CONTRACT_ID || '|' || PRODUCT_GROUP AS cid_pg, ATR
      FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
      WHERE RENEWAL_MONTH = '2026-07-01'
      UNION ALL
      SELECT 'SANDBOX', SEGMENT, CONTRACT_ID || '|' || PRODUCT_GROUP, ATR
      FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
      WHERE RENEWAL_MONTH = '2026-07-01'
    )
    GROUP BY SEGMENT ORDER BY SEGMENT
""", conn=conn)
print(df2.to_string(index=False))
print()

print("=" * 60)
print("3. COHORT distribution: are extra prod rows from a different cohort?")
print("=" * 60)
df3 = fetch_dataframe("""
    SELECT COHORT, src, COUNT(*) AS n, ROUND(SUM(ATR)/1e6, 3) AS atr_m
    FROM (
      SELECT 'PROD' AS src, COHORT, ATR
      FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
      WHERE RENEWAL_MONTH = '2026-07-01'
      UNION ALL
      SELECT 'SANDBOX', COHORT, ATR
      FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
      WHERE RENEWAL_MONTH = '2026-07-01'
    )
    GROUP BY COHORT, src ORDER BY COHORT, src
""", conn=conn)
print(df3.to_string(index=False))
print()

print("=" * 60)
print("4. PRODUCT_GROUP distribution July 2026: prod vs sandbox")
print("=" * 60)
df4 = fetch_dataframe("""
    SELECT PRODUCT_GROUP, src, COUNT(*) AS n, ROUND(SUM(ATR)/1e6,3) AS atr_m
    FROM (
      SELECT 'PROD' AS src, PRODUCT_GROUP, ATR
      FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
      WHERE RENEWAL_MONTH = '2026-07-01'
      UNION ALL
      SELECT 'SANDBOX', PRODUCT_GROUP, ATR
      FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
      WHERE RENEWAL_MONTH = '2026-07-01'
    )
    GROUP BY PRODUCT_GROUP, src ORDER BY PRODUCT_GROUP, src
""", conn=conn)
print(df4.to_string(index=False))
print()

print("=" * 60)
print("5. Are sandbox rows a strict subset of prod? (contracts in sandbox NOT in prod)")
print("=" * 60)
df5 = fetch_dataframe("""
    SELECT COUNT(*) AS sandbox_contracts_not_in_prod
    FROM (
      SELECT DISTINCT CONTRACT_ID, PRODUCT_GROUP
      FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
      WHERE RENEWAL_MONTH = '2026-07-01'
    ) s
    WHERE NOT EXISTS (
      SELECT 1 FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL p
      WHERE p.CONTRACT_ID = s.CONTRACT_ID
        AND p.PRODUCT_GROUP = s.PRODUCT_GROUP
        AND p.RENEWAL_MONTH = '2026-07-01'
    )
""", conn=conn)
print(df5.to_string(index=False))

print("=" * 60)
print("6. Contracts in PROD not in sandbox for July 2026 (top segments)")
print("=" * 60)
df6 = fetch_dataframe("""
    SELECT p.SEGMENT, COUNT(*) AS n_extra_in_prod, ROUND(SUM(p.ATR)/1e6, 3) AS atr_m
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL p
    WHERE p.RENEWAL_MONTH = '2026-07-01'
      AND NOT EXISTS (
        SELECT 1 FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL s
        WHERE s.CONTRACT_ID = p.CONTRACT_ID
          AND s.PRODUCT_GROUP = p.PRODUCT_GROUP
          AND s.RENEWAL_MONTH = '2026-07-01'
      )
    GROUP BY p.SEGMENT ORDER BY n_extra_in_prod DESC
""", conn=conn)
print(df6.to_string(index=False))
