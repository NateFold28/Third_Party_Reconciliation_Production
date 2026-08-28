"""
TARGET CHAIN AUDIT — confirms the model trains on the same actuals Finance reports.

Chain being verified:
  CARR snap ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE
    → feature store TARGET__RENEWED_AMOUNT (capped at ATR)
    → feature store TARGET__IS_CHURN (ALLOCATED < ATR = any dollar loss)
    → app table ACTUAL_RETAINED_ARR (same ALLOCATED, no cap — for display)
    → app rollup ACTUAL_PCT = SUM(ACTUAL_RETAINED_ARR)/SUM(ATR)  ← Finance board number

Five checks:
  A) Feature store rate vs Finance board rate — should agree within ~0.5pp after cap effect
  B) TARGET__IS_CHURN rate vs true CARR dollar churn — should match
  C) Coverage gap — contracts in app table not in feature store
  D) ATR column agreement — feature store ATR vs app table ATR
  E) Training label positive-class rate by year — confirms ~20-25% churn, not 4%
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection()
FS   = "STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE"
APP  = "STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"

# ─────────────────────────────────────────────────────────────────────────────
# CHECK A  Feature store rate vs app table ACTUAL_PCT for settled months
# If targets are correct these should agree within ~0.5pp (the cap effect).
# Larger gaps mean different populations or wrong ATR column.
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("CHECK A — Feature store TARGET rate vs App table ACTUAL rate (2025-2026)")
print("=" * 70)
qA = f"""
WITH fs_rates AS (
    SELECT
        DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS MONTH,
        SUM(TARGET__RENEWED_AMOUNT)              AS FS_RENEWED,
        SUM(ATR)                                 AS FS_ATR,
        SUM(TARGET__RENEWED_AMOUNT) / NULLIF(SUM(ATR), 0) * 100 AS FS_RATE_PCT
    FROM {FS}
    WHERE HORIZON = 0
      AND TARGET__RENEWED_AMOUNT IS NOT NULL
      AND RENEWAL_MONTH >= '2025-01-01'
      AND RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
    GROUP BY 1
),
app_rates AS (
    SELECT
        DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS MONTH,
        SUM(ACTUAL_RETAINED_ARR)                 AS APP_RENEWED,
        SUM(ATR)                                 AS APP_ATR,
        SUM(ACTUAL_RETAINED_ARR) / NULLIF(SUM(ATR), 0) * 100 AS APP_RATE_PCT
    FROM {APP}
    WHERE IS_MATURED_MONTH = TRUE
    GROUP BY 1
)
SELECT
    COALESCE(f.MONTH, a.MONTH)          AS MONTH,
    ROUND(f.FS_RATE_PCT, 2)             AS FS_RATE_PCT,
    ROUND(a.APP_RATE_PCT, 2)            AS APP_RATE_PCT,
    ROUND(f.FS_RATE_PCT - a.APP_RATE_PCT, 2) AS DIFF_PP,
    ROUND(f.FS_ATR  / 1e6, 1)           AS FS_ATR_M,
    ROUND(a.APP_ATR / 1e6, 1)           AS APP_ATR_M,
    ROUND((f.FS_ATR - a.APP_ATR) / NULLIF(a.APP_ATR, 0) * 100, 1) AS ATR_DIFF_PCT
FROM fs_rates f
FULL OUTER JOIN app_rates a ON a.MONTH = f.MONTH
ORDER BY 1
"""
dfA = fetch_dataframe(qA, conn=conn)
print(dfA.to_string(index=False))
print("\nPASS if |DIFF_PP| < 1.0pp (cap effect on expanding contracts is tiny at portfolio level)")
print("FAIL if |DIFF_PP| > 2pp — means formula mismatch or population mismatch")

# ─────────────────────────────────────────────────────────────────────────────
# CHECK B  TARGET__IS_CHURN positive rate vs true CARR dollar churn rate
# Training on ~4% (full non-renewal only) would give a useless model.
# Correct Finance definition: ALLOCATED < ATR = ~20-25% positive class.
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("CHECK B — TARGET__IS_CHURN positive rate by year (must be ~20-25%, not ~4%)")
print("=" * 70)
qB = f"""
SELECT
    YEAR(RENEWAL_MONTH)                                AS YEAR,
    COUNT(*)                                           AS N_CONTRACTS,
    SUM(TARGET__IS_CHURN)                              AS N_CHURN,
    ROUND(SUM(TARGET__IS_CHURN) / COUNT(*) * 100, 1)   AS CHURN_RATE_PCT,
    ROUND(SUM(ATR) / 1e6, 1)                           AS ATR_M,
    ROUND((1 - SUM(TARGET__RENEWED_AMOUNT) / NULLIF(SUM(ATR), 0)) * 100, 1) AS DOLLAR_CHURN_RATE_PCT
FROM {FS}
WHERE HORIZON = 0
  AND TARGET__IS_CHURN IS NOT NULL
  AND RENEWAL_MONTH >= '2022-01-01'
  AND RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
GROUP BY 1
ORDER BY 1
"""
dfB = fetch_dataframe(qB, conn=conn)
print(dfB.to_string(index=False))
print("\nPASS: CHURN_RATE_PCT ~ 20-25% (ALLOCATED < ATR definition)")
print("FAIL: CHURN_RATE_PCT ~ 4% means IS_CHURN_MATURED bug (full non-renewal only)")

# ─────────────────────────────────────────────────────────────────────────────
# CHECK C  Coverage gap — contracts in app table (IS_MATURED) not in feature store
# Model population (feature store HORIZON=0 CAL+VAL) must be large fraction of app.
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("CHECK C — Coverage: app table vs feature store contract count (2025)")
print("=" * 70)
qC = f"""
WITH app_pop AS (
    SELECT DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS MONTH,
           COUNT(DISTINCT CONTRACT_ID) AS APP_N,
           SUM(ATR) AS APP_ATR
    FROM {APP}
    WHERE IS_MATURED_MONTH = TRUE
      AND RENEWAL_MONTH >= '2025-01-01'
      AND RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
    GROUP BY 1
),
fs_pop AS (
    SELECT DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS MONTH,
           COUNT(DISTINCT CONTRACT_ID_UFR) AS FS_N,
           SUM(ATR) AS FS_ATR
    FROM {FS}
    WHERE HORIZON = 0
      AND TARGET__IS_CHURN IS NOT NULL
      AND RENEWAL_MONTH >= '2025-01-01'
      AND RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
    GROUP BY 1
)
SELECT
    a.MONTH,
    a.APP_N,
    f.FS_N,
    ROUND(f.FS_N / NULLIF(a.APP_N, 0) * 100, 1) AS COVERAGE_PCT,
    ROUND(a.APP_ATR / 1e6, 1) AS APP_ATR_M,
    ROUND(f.FS_ATR  / 1e6, 1) AS FS_ATR_M,
    ROUND(f.FS_ATR / NULLIF(a.APP_ATR, 0) * 100, 1) AS ATR_COVERAGE_PCT
FROM app_pop a
LEFT JOIN fs_pop f ON f.MONTH = a.MONTH
ORDER BY 1
"""
dfC = fetch_dataframe(qC, conn=conn)
print(dfC.to_string(index=False))
print("\nPASS: ATR_COVERAGE_PCT >= 90% (missing rows are low-ATR or product-portfolio splits)")
print("FAIL: ATR_COVERAGE_PCT < 80% — churned contracts not making it into feature store")

# ─────────────────────────────────────────────────────────────────────────────
# CHECK D  ATR column — feature store ATR vs app table ATR for same contracts
# Must use ADJ_ATR_C_BUDGET_RATE, not INITIAL_ATR_C (the latter is the wrong denominator)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("CHECK D — ATR match: feature store vs app table for same contracts (sample 2026-05)")
print("=" * 70)
qD = f"""
WITH fs_atr AS (
    SELECT CONTRACT_ID_UFR, SUM(ATR) AS FS_ATR
    FROM {FS}
    WHERE HORIZON = 0
      AND RENEWAL_MONTH = '2026-05-01'
    GROUP BY 1
),
app_atr AS (
    SELECT CONTRACT_ID, SUM(ATR) AS APP_ATR
    FROM {APP}
    WHERE DATE_TRUNC('MONTH', RENEWAL_MONTH) = '2026-05-01'
    GROUP BY 1
),
joined AS (
    SELECT
        f.CONTRACT_ID_UFR,
        f.FS_ATR,
        a.APP_ATR,
        ABS(f.FS_ATR - a.APP_ATR) / NULLIF(a.APP_ATR, 0) AS REL_DIFF
    FROM fs_atr f
    JOIN app_atr a ON a.CONTRACT_ID = f.CONTRACT_ID_UFR
)
SELECT
    COUNT(*) AS N_MATCHED,
    ROUND(AVG(FS_ATR)) AS AVG_FS_ATR,
    ROUND(AVG(APP_ATR)) AS AVG_APP_ATR,
    ROUND(AVG(REL_DIFF) * 100, 2) AS AVG_REL_DIFF_PCT,
    COUNT_IF(REL_DIFF > 0.05) AS N_WITH_GT5PCT_DIFF,
    ROUND(SUM(FS_ATR)/1e6, 1) AS TOTAL_FS_ATR_M,
    ROUND(SUM(APP_ATR)/1e6, 1) AS TOTAL_APP_ATR_M
FROM joined
"""
dfD = fetch_dataframe(qD, conn=conn)
print(dfD.to_string(index=False))
print("\nPASS: AVG_REL_DIFF_PCT < 1% and N_WITH_GT5PCT_DIFF is small")
print("FAIL: Large AVG_REL_DIFF_PCT means wrong ATR column (INITIAL_ATR_C vs ADJ_ATR_C)")

# ─────────────────────────────────────────────────────────────────────────────
# CHECK E  Verify the training rate formula exactly matches Finance
# Directly compare CARR source → feature store target → Finance app rate
# for a spot month to close the loop end-to-end
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("CHECK E — End-to-end formula spot-check for 2026-05")
print("  CARR source → Feature Store target → App actual")
print("=" * 70)
qE = f"""
WITH carr_raw AS (
    SELECT
        SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE, 0)) AS CARR_ALLOCATED,
        SUM(ADJ_ATR_C_BUDGET_RATE)                                  AS CARR_ATR,
        SUM(COALESCE(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE, 0))
            / NULLIF(SUM(ADJ_ATR_C_BUDGET_RATE), 0) * 100           AS CARR_RATE_PCT,
        COUNT(*) AS N
    FROM ANALYTICS.SNAPSHOTS.CARR__RENEWALS_CONTRACT_LVL_SNAP
    WHERE DATE_TRUNC('MONTH', MASTER_DATE) = '2026-05-01'
      AND INCLUDE_FLAG_C = 1
      AND ADJ_ATR_C_BUDGET_RATE > 0
),
fs_raw AS (
    SELECT
        SUM(TARGET__RENEWED_AMOUNT)                                  AS FS_RENEWED,
        SUM(ATR)                                                      AS FS_ATR,
        SUM(TARGET__RENEWED_AMOUNT) / NULLIF(SUM(ATR), 0) * 100      AS FS_RATE_PCT,
        COUNT(*) AS N
    FROM {FS}
    WHERE DATE_TRUNC('MONTH', RENEWAL_MONTH) = '2026-05-01'
      AND HORIZON = 0
      AND TARGET__RENEWED_AMOUNT IS NOT NULL
),
app_raw AS (
    SELECT
        SUM(ACTUAL_RETAINED_ARR)                                     AS APP_RENEWED,
        SUM(ATR)                                                      AS APP_ATR,
        SUM(ACTUAL_RETAINED_ARR) / NULLIF(SUM(ATR), 0) * 100         AS APP_RATE_PCT,
        COUNT(DISTINCT CONTRACT_ID) AS N
    FROM {APP}
    WHERE DATE_TRUNC('MONTH', RENEWAL_MONTH) = '2026-05-01'
      AND IS_MATURED_MONTH = TRUE
)
SELECT
    'CARR source'      AS SOURCE, ROUND(c.CARR_RATE_PCT, 2) AS RATE_PCT, c.N AS N_ROWS,
    ROUND(c.CARR_ATR/1e6,1) AS ATR_M, ROUND(c.CARR_ALLOCATED/1e6,1) AS RENEWED_M
FROM carr_raw c
UNION ALL
SELECT
    'Feature Store (training target)', ROUND(f.FS_RATE_PCT, 2), f.N,
    ROUND(f.FS_ATR/1e6,1), ROUND(f.FS_RENEWED/1e6,1)
FROM fs_raw f
UNION ALL
SELECT
    'App table (board/display)', ROUND(a.APP_RATE_PCT, 2), a.N,
    ROUND(a.APP_ATR/1e6,1), ROUND(a.APP_RENEWED/1e6,1)
FROM app_raw a
"""
dfE = fetch_dataframe(qE, conn=conn)
print(dfE.to_string(index=False))
print("\nExpected (May-26 known actual ~69.5% from app):")
print("  CARR source ≈ Feature Store target ≈ App table — all within ~0.5pp")
print("  If Feature Store >> App table: cap effect or wrong ATR")
print("  If Feature Store << App table: missing churned contracts in FS population")

conn.close()
print("\nDone.")
