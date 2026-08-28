"""
Find why sandbox is missing 896 rows (379 contracts) vs prod for July 2026.
After PRODUCT_GROUP grain fix, all ATR matches — so CARR spine is correct.
But sandbox is missing some contracts. Likely cause: the V5 ML prediction join
or the 'carr_contract_atr' CTE is filtering them out.
"""
import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe
conn = get_snowflake_connection()

print("=" * 70)
print("A. What contracts are in CARR but NOT in sandbox for July 2026?")
print("   These are the 'dropped' contracts we need to recover.")
print("=" * 70)
df_a = fetch_dataframe("""
    WITH carr_july AS (
        SELECT
            TRIM(CONTRACT_ID_UFR) AS CONTRACT_ID,
            TRIM(PRODUCT_GROUP_UFR) AS PRODUCT_GROUP,
            SUM(COALESCE(ADJ_ATR_C_BUDGET_RATE, 0)) AS ATR
        FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
        WHERE INCLUDE_FLAG_C = 1
          AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-07-01'
        GROUP BY 1, 2
    ),
    sbox_july AS (
        SELECT CONTRACT_ID, PRODUCT_GROUP, ATR
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH = '2026-07-01'
    )
    SELECT
        COALESCE(c.CONTRACT_ID, s.CONTRACT_ID) AS CONTRACT_ID,
        COALESCE(c.PRODUCT_GROUP, s.PRODUCT_GROUP) AS PRODUCT_GROUP,
        CASE WHEN c.CONTRACT_ID IS NULL THEN 'SANDBOX_ONLY'
             WHEN s.CONTRACT_ID IS NULL THEN 'CARR_NOT_SANDBOX'
             ELSE 'BOTH' END AS status,
        COALESCE(c.ATR, 0) AS carr_atr,
        COALESCE(s.ATR, 0) AS sbox_atr
    FROM carr_july c
    FULL OUTER JOIN sbox_july s
        ON c.CONTRACT_ID = s.CONTRACT_ID AND c.PRODUCT_GROUP = s.PRODUCT_GROUP
    WHERE c.CONTRACT_ID IS NULL OR s.CONTRACT_ID IS NULL
    LIMIT 20
""", conn=conn)
print("Sample missing or extra (CONTRACT_ID, PRODUCT_GROUP) combos:")
print(df_a.to_string(index=False))

print()
print("=" * 70)
print("B. Summary: how many (CID, PG) are CARR_NOT_SANDBOX vs SANDBOX_ONLY?")
print("=" * 70)
df_b = fetch_dataframe("""
    WITH carr_july AS (
        SELECT
            TRIM(CONTRACT_ID_UFR) AS CONTRACT_ID,
            TRIM(PRODUCT_GROUP_UFR) AS PRODUCT_GROUP,
            SUM(COALESCE(ADJ_ATR_C_BUDGET_RATE, 0)) AS ATR
        FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
        WHERE INCLUDE_FLAG_C = 1
          AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-07-01'
        GROUP BY 1, 2
    ),
    sbox_july AS (
        SELECT CONTRACT_ID, PRODUCT_GROUP, ATR
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH = '2026-07-01'
    )
    SELECT
        CASE WHEN c.CONTRACT_ID IS NULL THEN 'SANDBOX_ONLY'
             WHEN s.CONTRACT_ID IS NULL THEN 'CARR_NOT_SANDBOX'
             ELSE 'BOTH' END AS status,
        COUNT(*) AS cnt,
        ROUND(SUM(COALESCE(c.ATR, 0) + COALESCE(s.ATR, 0))/1e6, 3) AS atr_m
    FROM carr_july c
    FULL OUTER JOIN sbox_july s
        ON c.CONTRACT_ID = s.CONTRACT_ID AND c.PRODUCT_GROUP = s.PRODUCT_GROUP
    GROUP BY 1
    ORDER BY cnt DESC
""", conn=conn)
print(df_b.to_string(index=False))

print()
print("=" * 70)
print("C. Are missing contracts present in V5 ML predictions for July 2026?")
print("   If NO → they're being filtered out because of missing ML score + fallback failure")
print("=" * 70)
df_c = fetch_dataframe("""
    WITH carr_july AS (
        SELECT
            TRIM(CONTRACT_ID_UFR) AS CONTRACT_ID,
            SUM(COALESCE(ADJ_ATR_C_BUDGET_RATE, 0)) AS CARR_ATR
        FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
        WHERE INCLUDE_FLAG_C = 1
          AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-07-01'
        GROUP BY 1
    ),
    sbox_july AS (
        SELECT DISTINCT CONTRACT_ID FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH = '2026-07-01'
    ),
    missing AS (
        SELECT c.CONTRACT_ID, c.CARR_ATR
        FROM carr_july c
        LEFT JOIN sbox_july s ON c.CONTRACT_ID = s.CONTRACT_ID
        WHERE s.CONTRACT_ID IS NULL
    ),
    latest_run AS (
        SELECT RUN_ID FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
        GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
    ),
    v5_scored AS (
        SELECT DISTINCT TRIM(pred.CONTRACT_ID_UFR) AS CONTRACT_ID
        FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS pred
        WHERE pred.RUN_ID = (SELECT RUN_ID FROM latest_run)
          AND DATE_TRUNC('MONTH', pred.MASTER_DATE)::DATE = '2026-07-01'
    )
    SELECT
        CASE WHEN v5.CONTRACT_ID IS NOT NULL THEN 'IN_V5_PREDICTIONS'
             ELSE 'NOT_IN_V5_PREDICTIONS' END AS v5_status,
        COUNT(*) AS missing_contracts,
        ROUND(SUM(m.CARR_ATR)/1e6, 3) AS atr_m
    FROM missing m
    LEFT JOIN v5_scored v5 ON m.CONTRACT_ID = v5.CONTRACT_ID
    GROUP BY 1
""", conn=conn)
print(df_c.to_string(index=False))

print()
print("=" * 70)
print("D. How does the sandbox sp get its SEGMENT for these missing contracts?")
print("   Look at the sandbox join chain for missing contracts.")
print("   Does the sandbox have a filter on SEGMENT <> 'Unknown' or similar?")
print("=" * 70)
# Check if there's a WHERE on the sandbox output that might filter out records
df_d = fetch_dataframe("""
    -- Are there SEGMENT = 'Unknown' rows in sandbox that might be filtered?
    SELECT SEGMENT, COUNT(*) AS cnt
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-07-01'
    GROUP BY 1
    ORDER BY cnt DESC
""", conn=conn)
print("Sandbox SEGMENT distribution July 2026:")
print(df_d.to_string(index=False))

print()
print("E. What is the ATR=0 filter situation?")
print("   Prod comments say 'No ATR > 0 filter'")
df_e = fetch_dataframe("""
    SELECT
        CASE WHEN ATR = 0 THEN 'ATR=0' ELSE 'ATR>0' END AS atr_bucket,
        COUNT(*) AS cnt
    FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
    WHERE INCLUDE_FLAG_C = 1
      AND DATE_TRUNC('MONTH', MASTER_DATE)::DATE = '2026-07-01'
    GROUP BY 1
""", conn=conn)
print("CARR July 2026 ATR distribution:")
print(df_e.to_string(index=False))
