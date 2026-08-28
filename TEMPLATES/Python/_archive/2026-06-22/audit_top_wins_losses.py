"""
Validation audit for Top Wins/Losses and Renewal % issues.

Investigates:
1. CONTRACT_RISK_PCTL_IN_SEG — is it 0-100 or 0-1 scale?
   (The app does / 100, so if the column is already 0-1 it becomes 0-0.01
   → WIN_VALUE = EFF * 0.99 → high-risk contracts appear as top wins)
2. AT_RISK_DOLLARS presence + distribution on forward rows
3. Forward month CHURN_PCT distribution (looking for inflation)
4. Why renewal % alternates (69.9 / 70.2 / 69.9 / 70.2) — check
   CONTRACT_RISK_TIER distribution across months
5. Quick sample of the rows that would rank as Top Wins right now
6. ML forecast vs EFFECTIVE_FORECAST comparison by month (forward)

Run from the TEMPLATES/Python directory or the repo root with .venv active.
"""
import sys
import os
from pathlib import Path

# ── make the TEMPLATES/Python helpers importable ──
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from connection import fetch_dataframe

TABLE = "STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"
TODAY = "2026-06-10"
FORWARD_START = "2026-06-01"

separator = "\n" + "=" * 80 + "\n"

# ─────────────────────────────────────────────────────────────────────────────
# 1. CONTRACT_RISK_PCTL_IN_SEG scale check
#    If max ~ 100 → column is on 0-100 scale (correct for /100 in app)
#    If max ~ 1   → column is on 0-1 scale  (BUG: /100 makes it 0-0.01)
# ─────────────────────────────────────────────────────────────────────────────
q1 = f"""
SELECT
    MIN(CONTRACT_RISK_PCTL_IN_SEG)          AS MIN_PCTL,
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY CONTRACT_RISK_PCTL_IN_SEG) AS P25,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY CONTRACT_RISK_PCTL_IN_SEG) AS P50,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY CONTRACT_RISK_PCTL_IN_SEG) AS P75,
    MAX(CONTRACT_RISK_PCTL_IN_SEG)          AS MAX_PCTL,
    AVG(CONTRACT_RISK_PCTL_IN_SEG)          AS AVG_PCTL,
    COUNT(*)                                 AS N_ROWS,
    COUNT_IF(CONTRACT_RISK_PCTL_IN_SEG IS NULL) AS N_NULL
FROM {TABLE}
WHERE RENEWAL_MONTH >= '{FORWARD_START}'
"""

print(separator)
print("CHECK 1 — CONTRACT_RISK_PCTL_IN_SEG scale (forward rows)")
df1 = fetch_dataframe(q1)
print(df1.to_string(index=False))
print("\n⚠️  If MAX ~ 1.0 → the column is on 0-1 scale → the app's /100 is WRONG")
print("   If MAX ~ 100  → the column is on 0-100 scale → app logic is correct")

# ─────────────────────────────────────────────────────────────────────────────
# 2. CONTRACT_RISK_TIER distribution — check if HIGH tier is filtering wins
# ─────────────────────────────────────────────────────────────────────────────
q2 = f"""
SELECT
    CONTRACT_RISK_TIER,
    CONTRACT_RISK_TIER_RELATIVE,
    COUNT(*)                              AS N,
    ROUND(AVG(CONTRACT_RISK_PCTL_IN_SEG), 4)  AS AVG_PCTL,
    ROUND(AVG(CHURN_PCT), 2)              AS AVG_CHURN_PCT,
    ROUND(SUM(AT_RISK_DOLLARS) / 1e6, 2) AS AT_RISK_M
FROM {TABLE}
WHERE RENEWAL_MONTH >= '{FORWARD_START}'
GROUP BY 1, 2
ORDER BY 1, 2
"""

print(separator)
print("CHECK 2 — CONTRACT_RISK_TIER vs PCTL_IN_SEG vs CHURN_PCT (forward rows)")
df2 = fetch_dataframe(q2)
print(df2.to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# 3. AT_RISK_DOLLARS — presence and distribution on forward rows
# ─────────────────────────────────────────────────────────────────────────────
q3 = f"""
SELECT
    RENEWAL_MONTH,
    COUNT(*)                                    AS N,
    COUNT_IF(AT_RISK_DOLLARS IS NOT NULL)       AS N_ATRISK_NOTNULL,
    ROUND(SUM(AT_RISK_DOLLARS) / 1e6, 2)       AS SUM_ATRISK_M,
    ROUND(SUM(ATR) / 1e6, 2)                   AS SUM_ATR_M,
    ROUND(SUM(CHURN_PCT * ATR / 100) / 1e6, 2) AS CHURN_PCT_TIMES_ATR_M,
    ROUND(AVG(CHURN_PCT), 2)                    AS AVG_CHURN_PCT
FROM {TABLE}
WHERE RENEWAL_MONTH >= '{FORWARD_START}'
  AND RENEWAL_MONTH <= DATEADD('month', 6, '{FORWARD_START}')
GROUP BY 1
ORDER BY 1
"""

print(separator)
print("CHECK 3 — AT_RISK_DOLLARS vs CHURN_PCT×ATR by month (forward)")
df3 = fetch_dataframe(q3)
print(df3.to_string(index=False))
print("\nIf N_ATRISK_NOTNULL = 0, app falls back to CHURN_PCT×ATR as the loss proxy")

# ─────────────────────────────────────────────────────────────────────────────
# 4. CHURN_PCT distribution — forward vs historical (inflation check)
# ─────────────────────────────────────────────────────────────────────────────
q4 = f"""
SELECT
    CASE WHEN IS_MATURE = TRUE OR IS_MATURED_MONTH = TRUE THEN 'Matured'
         ELSE 'Forward' END              AS REGIME,
    ROUND(AVG(CHURN_PCT), 2)             AS AVG_CHURN,
    ROUND(MEDIAN(CHURN_PCT), 2)          AS MED_CHURN,
    ROUND(PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY CHURN_PCT), 2) AS P10,
    ROUND(PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY CHURN_PCT), 2) AS P90,
    ROUND(MAX(CHURN_PCT), 2)             AS MAX_CHURN,
    COUNT(*)                             AS N
FROM {TABLE}
WHERE RENEWAL_MONTH >= '2026-01-01'
GROUP BY 1
ORDER BY 1
"""

print(separator)
print("CHECK 4 — CHURN_PCT distribution: Matured vs Forward (inflation check)")
df4 = fetch_dataframe(q4)
print(df4.to_string(index=False))
print("\nIf AVG forward CHURN >> matured CHURN → covariate-shift inflation confirmed")

# ─────────────────────────────────────────────────────────────────────────────
# 5. WIN_VALUE simulation — show what the app would rank as Top 15 Wins
#    Using BOTH the current app logic (PCTL/100) and the corrected logic
# ─────────────────────────────────────────────────────────────────────────────
q5 = f"""
WITH fwd AS (
    SELECT
        PARTNER,
        SEGMENT,
        RENEWAL_DATE,
        CONTRACT_RISK_TIER,
        CONTRACT_RISK_PCTL_IN_SEG,
        CHURN_PCT,
        ATR,
        ML_FORECAST,
        COALESCE(FINANCE_FORECAST, ML_FORECAST)           AS EFFECTIVE_FORECAST,
        -- APP'S CURRENT LOGIC: divides by 100 regardless of scale
        COALESCE(FINANCE_FORECAST, ML_FORECAST)
            * (1.0 - COALESCE(CONTRACT_RISK_PCTL_IN_SEG, 50) / 100.0)  AS WIN_VALUE_CURRENT,
        -- CORRECTED if already 0-1 scale: no /100 needed
        COALESCE(FINANCE_FORECAST, ML_FORECAST)
            * (1.0 - COALESCE(CONTRACT_RISK_PCTL_IN_SEG, 0.5))         AS WIN_VALUE_0_1_SCALE
    FROM {TABLE}
    WHERE RENEWAL_MONTH >= '{FORWARD_START}'
      AND RENEWAL_MONTH <= DATEADD('month', 6, '{FORWARD_START}')
      AND COALESCE(ATR, 0) > 0
      AND COALESCE(IS_MATURE, FALSE) = FALSE
      AND COALESCE(IS_MATURED_MONTH, FALSE) = FALSE
)
SELECT
    PARTNER,
    SEGMENT,
    TO_CHAR(RENEWAL_DATE, 'Mon YYYY')         AS RENEWAL,
    CONTRACT_RISK_TIER                         AS TIER,
    ROUND(CONTRACT_RISK_PCTL_IN_SEG, 4)       AS PCTL_RAW,
    ROUND(CHURN_PCT, 1)                        AS CHURN_PCT,
    ROUND(ATR / 1e3, 0)                        AS ATR_K,
    ROUND(EFFECTIVE_FORECAST / 1e3, 0)         AS EFF_FCST_K,
    ROUND(WIN_VALUE_CURRENT / 1e3, 0)          AS WIN_VAL_CURRENT_K,
    ROUND(WIN_VALUE_0_1_SCALE / 1e3, 0)        AS WIN_VAL_IF_0_1_K
FROM fwd
WHERE CONTRACT_RISK_TIER != 'HIGH'
  AND WIN_VALUE_CURRENT > 0
ORDER BY WIN_VALUE_CURRENT DESC
LIMIT 15
"""

print(separator)
print("CHECK 5 — Simulated TOP 15 WINS (current app formula vs corrected)")
df5 = fetch_dataframe(q5)
print(df5.to_string(index=False))
print("\nKey: if PCTL_RAW < 1.0 → column is 0-1 → WIN_VALUE_CURRENT is ~= EFF_FCST (bug)")

# ─────────────────────────────────────────────────────────────────────────────
# 6. Renewal % alternating issue — monthly ML vs EFFECTIVE by month
# ─────────────────────────────────────────────────────────────────────────────
q6 = f"""
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE   AS MONTH,
    COUNT(*)                                    AS N_CONTRACTS,
    ROUND(SUM(ATR) / 1e6, 2)                  AS ATR_M,
    ROUND(SUM(ML_FORECAST) / 1e6, 2)          AS ML_FCST_M,
    ROUND(SUM(COALESCE(FINANCE_FORECAST, ML_FORECAST)) / 1e6, 2) AS FIN_FCST_M,
    ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 2)       AS ML_RATE_PCT,
    ROUND(SUM(COALESCE(FINANCE_FORECAST, ML_FORECAST)) / NULLIF(SUM(ATR), 0) * 100, 2) AS FIN_RATE_PCT,
    -- Segment mix check — imbalanced month mix causes oscillation
    COUNT_IF(SEGMENT = 'Strategic')            AS N_STRATEGIC,
    COUNT_IF(SEGMENT = 'Core')                 AS N_CORE,
    COUNT_IF(SEGMENT = 'Growth')               AS N_GROWTH,
    COUNT_IF(SEGMENT = 'Emerging')             AS N_EMERGING
FROM {TABLE}
WHERE RENEWAL_MONTH >= '{FORWARD_START}'
  AND RENEWAL_MONTH <= DATEADD('month', 6, '{FORWARD_START}')
  AND COALESCE(IS_MATURE, FALSE) = FALSE
GROUP BY 1
ORDER BY 1
"""

print(separator)
print("CHECK 6 — Monthly ML vs Finance rates + segment mix (forward months)")
df6 = fetch_dataframe(q6)
print(df6.to_string(index=False))
print("\nIf FIN_RATE_PCT alternates but ML_RATE_PCT doesn't → anchor/recon layer issue")
print("If N_STRATEGIC alternates → segment composition driving oscillation")

# ─────────────────────────────────────────────────────────────────────────────
# 7. Per-segment anchor rates feeding FINANCE_FORECAST — do they match V5 expected?
# ─────────────────────────────────────────────────────────────────────────────
q7 = f"""
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE   AS MONTH,
    SEGMENT,
    COUNT(*)                                    AS N,
    ROUND(SUM(ATR) / 1e6, 2)                  AS ATR_M,
    ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 2)       AS ML_RATE,
    ROUND(SUM(COALESCE(FINANCE_FORECAST, ML_FORECAST)) / NULLIF(SUM(ATR), 0) * 100, 2) AS FIN_RATE,
    ROUND(AVG(CHURN_PCT), 2)                   AS AVG_CHURN
FROM {TABLE}
WHERE RENEWAL_MONTH >= '{FORWARD_START}'
  AND RENEWAL_MONTH <= DATEADD('month', 3, '{FORWARD_START}')
  AND COALESCE(IS_MATURE, FALSE) = FALSE
GROUP BY 1, 2
ORDER BY 1, 2
"""

print(separator)
print("CHECK 7 — Per-segment ML vs Finance rates (Jun-Aug 2026 forward)")
df7 = fetch_dataframe(q7)
print(df7.to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# 8. Compare to Finance's own forward-looking numbers (if available)
#    Checks whether the model is systematically lower than finance expects
# ─────────────────────────────────────────────────────────────────────────────
q8 = f"""
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE   AS MONTH,
    ROUND(SUM(ML_FORECAST) / 1e6, 2)          AS ML_M,
    ROUND(SUM(COALESCE(FINANCE_FORECAST, ML_FORECAST)) / 1e6, 2)  AS FIN_M,
    ROUND(SUM(ACTUAL_RETAINED_ARR) / 1e6, 2)  AS ACTUAL_M,
    ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 2)        AS ML_RATE,
    ROUND(SUM(COALESCE(FINANCE_FORECAST, ML_FORECAST)) / NULLIF(SUM(ATR), 0) * 100, 2) AS FIN_RATE,
    ROUND(SUM(ACTUAL_RETAINED_ARR) / NULLIF(SUM(ATR), 0) * 100, 2) AS ACTUAL_RATE
FROM {TABLE}
WHERE RENEWAL_MONTH >= '2026-01-01'
GROUP BY 1
ORDER BY 1
"""

print(separator)
print("CHECK 8 — Full Jan-Dec 2026 actuals vs ML vs Finance rate by month")
df8 = fetch_dataframe(q8)
print(df8.to_string(index=False))
print("\nActual_rate on forward months will be ~0 (nothing closed yet) — expected")

print(separator)
print("AUDIT COMPLETE")
