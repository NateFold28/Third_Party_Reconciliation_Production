"""
Rebuild V5 sandbox tables with the PRODUCT_GROUP grain fix, then validate parity.

Step 1 — CALL SP_V5_BUILD_APP_TABLES_V5_SHADOW()
  Rebuilds V5_SANDBOX_APP_CONTRACT_DETAIL (and companion tables) using the
  updated 03_app_tables_v5.sql logic (PRODUCT_GROUP_UFR grain instead of
  PRODUCT_PORTFOLIO_UFR, product_group_map CTE removed).

Step 2 — Parity validation
  Compares row counts, unique contracts, ATR, and PRODUCT_GROUP distribution
  between sandbox and prod for July 2026 (most recent forward month).
  Goal: sandbox rows should match prod (~3,138 rows for July 2026).
"""
import sys
import time
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection()
cur = conn.cursor()

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: Call the SP
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("STEP 1 — Calling SP_V5_BUILD_APP_TABLES_V5_SHADOW()")
print("  This rebuilds sandbox tables. May take 2–5 minutes.")
print("=" * 70)

try:
    t0 = time.time()
    cur.execute("USE WAREHOUSE CORTEX_WH")
    cur.execute("CALL STREAMLIT_APPS.DBO.SP_V5_BUILD_APP_TABLES_V5_SHADOW()")
    result = cur.fetchone()
    elapsed = time.time() - t0
    print(f"  SP returned: {result}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print()
except Exception as e:
    print(f"  ERROR calling SP: {e}")
    print()
    print("  → Run this in Snowsight instead:")
    print("    USE WAREHOUSE CORTEX_WH;")
    print("    CALL STREAMLIT_APPS.DBO.SP_V5_BUILD_APP_TABLES_V5_SHADOW();")
    print()

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: Parity validation
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("STEP 2 — Parity validation: July 2026 (prod vs sandbox)")
print("=" * 70)

# 2a. Top-line row count + ATR
print("\n[A] Row counts, unique contracts, ATR")
df_top = fetch_dataframe("""
    SELECT 'PROD'    AS src,
           COUNT(*)                                              AS total_rows,
           COUNT(DISTINCT CONTRACT_ID)                          AS uniq_contracts,
           COUNT(DISTINCT CONTRACT_ID || '|' || PRODUCT_GROUP)  AS uniq_cid_pg,
           ROUND(SUM(ATR)/1e6, 3)                               AS atr_m
    FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-07-01'
    UNION ALL
    SELECT 'SANDBOX',
           COUNT(*),
           COUNT(DISTINCT CONTRACT_ID),
           COUNT(DISTINCT CONTRACT_ID || '|' || PRODUCT_GROUP),
           ROUND(SUM(ATR)/1e6, 3)
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH = '2026-07-01'
""", conn=conn)
print(df_top.to_string(index=False))

# 2b. Delta still present?
prod_rows    = int(df_top.loc[df_top["SRC"] == "PROD",    "TOTAL_ROWS"].iloc[0])
sand_rows    = int(df_top.loc[df_top["SRC"] == "SANDBOX", "TOTAL_ROWS"].iloc[0])
delta        = prod_rows - sand_rows
if abs(delta) <= 10:
    print(f"\n  ✅ PARITY ACHIEVED — delta = {delta:+d} rows (within tolerance)")
else:
    print(f"\n  ⚠️  DELTA STILL PRESENT — prod={prod_rows}, sandbox={sand_rows}, diff={delta:+d}")

# 2c. PRODUCT_GROUP breakdown
print("\n[B] PRODUCT_GROUP distribution July 2026: prod vs sandbox")
df_pg = fetch_dataframe("""
    SELECT PRODUCT_GROUP,
           SUM(CASE WHEN src='PROD'    THEN 1 ELSE 0 END) AS prod_rows,
           SUM(CASE WHEN src='SANDBOX' THEN 1 ELSE 0 END) AS sandbox_rows,
           SUM(CASE WHEN src='PROD'    THEN 1 ELSE 0 END)
             - SUM(CASE WHEN src='SANDBOX' THEN 1 ELSE 0 END) AS delta_rows,
           ROUND(SUM(CASE WHEN src='PROD'    THEN ATR ELSE 0 END)/1e6, 3) AS prod_atr_m,
           ROUND(SUM(CASE WHEN src='SANDBOX' THEN ATR ELSE 0 END)/1e6, 3) AS sbox_atr_m
    FROM (
        SELECT 'PROD'    AS src, PRODUCT_GROUP, ATR
        FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH = '2026-07-01'
        UNION ALL
        SELECT 'SANDBOX', PRODUCT_GROUP, ATR
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH = '2026-07-01'
    )
    GROUP BY PRODUCT_GROUP
    ORDER BY ABS(delta_rows) DESC
""", conn=conn)
print(df_pg.to_string(index=False))

# 2d. SEGMENT breakdown
print("\n[C] SEGMENT distribution July 2026")
df_seg = fetch_dataframe("""
    SELECT SEGMENT,
           SUM(CASE WHEN src='PROD'    THEN 1 ELSE 0 END) AS prod_rows,
           SUM(CASE WHEN src='SANDBOX' THEN 1 ELSE 0 END) AS sandbox_rows,
           SUM(CASE WHEN src='PROD'    THEN 1 ELSE 0 END)
             - SUM(CASE WHEN src='SANDBOX' THEN 1 ELSE 0 END) AS delta_rows
    FROM (
        SELECT 'PROD'    AS src, SEGMENT, ATR
        FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH = '2026-07-01'
        UNION ALL
        SELECT 'SANDBOX', SEGMENT, ATR
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH = '2026-07-01'
    )
    GROUP BY SEGMENT
    ORDER BY ABS(delta_rows) DESC
""", conn=conn)
print(df_seg.to_string(index=False))

# 2e. Total ATR agreement across all months (not just July)
print("\n[D] Total ATR agreement across all forward months (July–Dec 2026)")
df_atr = fetch_dataframe("""
    SELECT
        RENEWAL_MONTH,
        ROUND(SUM(CASE WHEN src='PROD'    THEN ATR ELSE 0 END)/1e6, 3) AS prod_atr_m,
        ROUND(SUM(CASE WHEN src='SANDBOX' THEN ATR ELSE 0 END)/1e6, 3) AS sbox_atr_m,
        ROUND((SUM(CASE WHEN src='PROD'    THEN ATR ELSE 0 END)
               - SUM(CASE WHEN src='SANDBOX' THEN ATR ELSE 0 END))/1e6, 3) AS delta_atr_m,
        SUM(CASE WHEN src='PROD'    THEN 1 ELSE 0 END) AS prod_rows,
        SUM(CASE WHEN src='SANDBOX' THEN 1 ELSE 0 END) AS sbox_rows
    FROM (
        SELECT 'PROD'    AS src, RENEWAL_MONTH, ATR
        FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH >= '2026-06-01'
        UNION ALL
        SELECT 'SANDBOX', RENEWAL_MONTH, ATR
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH >= '2026-06-01'
    )
    GROUP BY RENEWAL_MONTH
    ORDER BY RENEWAL_MONTH
""", conn=conn)
print(df_atr.to_string(index=False))

# 2f. Spot-check: any contracts in sandbox with 'Unknown' PRODUCT_GROUP still?
print("\n[E] Sandbox rows with PRODUCT_GROUP = 'Unknown' (should be 0 after fix)")
df_unk = fetch_dataframe("""
    SELECT RENEWAL_MONTH, COUNT(*) AS unknown_rows
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE PRODUCT_GROUP = 'Unknown'
      AND RENEWAL_MONTH >= '2026-06-01'
    GROUP BY RENEWAL_MONTH
    ORDER BY RENEWAL_MONTH
""", conn=conn)
if df_unk.empty or df_unk["UNKNOWN_ROWS"].sum() == 0:
    print("  ✅ Zero 'Unknown' PRODUCT_GROUP rows — grain fix confirmed.")
else:
    print(df_unk.to_string(index=False))

cur.close()
print("\nDone.")
