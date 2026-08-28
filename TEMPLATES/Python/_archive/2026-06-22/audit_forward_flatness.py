"""
Diagnose why forward forecast is flat and where the mid-horizon jump comes from.
Queries V5_SANDBOX_APP_CONTRACT_DETAIL (what the app reads) directly.
Checks:
  A) Per-month portfolio-level FINANCE_FORECAST rate for all forward months
  B) Per-segment per-month breakdown so we can see WHICH segment shifts
  C) Recent actuals (last 6 months) vs forward forecast — trend gap
  D) ATR and segment mix by forward month (mix shift check)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from connection import get_snowflake_connection, fetch_dataframe

conn = get_snowflake_connection()

APP = "STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"

# A) Portfolio-level forward rates — monthly
print("=" * 70)
print("CHECK A — Portfolio forward rates by month (from app table)")
print("=" * 70)
q = f"""
SELECT
    RENEWAL_MONTH,
    COUNT(DISTINCT CONTRACT_ID) AS N_CONTRACTS,
    SUM(ATR) AS TOTAL_ATR,
    SUM(FINANCE_FORECAST) AS TOTAL_FIN_FCST,
    SUM(ML_FORECAST) AS TOTAL_ML_RAW,
    SUM(FINANCE_FORECAST) / NULLIF(SUM(ATR), 0) * 100 AS FIN_RATE_PCT,
    SUM(ML_FORECAST) / NULLIF(SUM(ATR), 0) * 100 AS ML_RAW_RATE_PCT,
    AVG(FINANCE_ANCHOR_RATE) * 100 AS AVG_BASE_RATE_PCT,
    AVG(FINANCE_SHRINK_SCALE) AS AVG_W_HORIZON,
    AVG(V2_SHIFT_PP) AS AVG_SHIFT_PP
FROM {APP}
WHERE IS_MATURED_MONTH = FALSE
  AND RENEWAL_MONTH >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1
LIMIT 24
"""
df = fetch_dataframe(q, conn=conn)
print(df.to_string(index=False))

# B) Per-segment per-month forward breakdown
print("\n" + "=" * 70)
print("CHECK B — Per-segment forward rates (which segment drives the jump)")
print("=" * 70)
q2 = f"""
SELECT
    RENEWAL_MONTH,
    SEGMENT,
    COUNT(*) AS N,
    SUM(ATR) AS SEG_ATR,
    SUM(FINANCE_FORECAST) / NULLIF(SUM(ATR), 0) * 100 AS FIN_RATE_PCT,
    AVG(FINANCE_ANCHOR_RATE) * 100 AS AVG_BASE_PCT,
    AVG(FINANCE_SHRINK_SCALE) AS AVG_W_HORIZON,
    AVG(V2_SHIFT_PP) AS AVG_SHIFT_PP
FROM {APP}
WHERE IS_MATURED_MONTH = FALSE
  AND RENEWAL_MONTH >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY 1, 2
ORDER BY 1, SEG_ATR DESC
LIMIT 80
"""
df2 = fetch_dataframe(q2, conn=conn)
print(df2.to_string(index=False))

# C) Recent actuals vs first 6 forward months
print("\n" + "=" * 70)
print("CHECK C — Recent actuals (last 6 months) vs first 6 forward months")
print("=" * 70)
q3 = f"""
WITH actuals AS (
    SELECT
        RENEWAL_MONTH,
        'ACTUAL' AS TYPE,
        SUM(ACTUAL_RETAINED_ARR) AS DOLLARS,
        SUM(ATR) AS ATR,
        SUM(ACTUAL_RETAINED_ARR) / NULLIF(SUM(ATR), 0) * 100 AS RATE_PCT
    FROM {APP}
    WHERE IS_MATURED_MONTH = TRUE
      AND RENEWAL_MONTH >= ADD_MONTHS(DATE_TRUNC('month', CURRENT_DATE), -6)
    GROUP BY 1
),
forward AS (
    SELECT
        RENEWAL_MONTH,
        'FORWARD' AS TYPE,
        SUM(FINANCE_FORECAST) AS DOLLARS,
        SUM(ATR) AS ATR,
        SUM(FINANCE_FORECAST) / NULLIF(SUM(ATR), 0) * 100 AS RATE_PCT
    FROM {APP}
    WHERE IS_MATURED_MONTH = FALSE
      AND RENEWAL_MONTH >= DATE_TRUNC('month', CURRENT_DATE)
      AND RENEWAL_MONTH < ADD_MONTHS(DATE_TRUNC('month', CURRENT_DATE), 7)
    GROUP BY 1
)
SELECT * FROM actuals
UNION ALL
SELECT * FROM forward
ORDER BY TYPE DESC, RENEWAL_MONTH
"""
df3 = fetch_dataframe(q3, conn=conn)
print(df3.to_string(index=False))

# D) Portfolio ATR and segment mix by forward month
print("\n" + "=" * 70)
print("CHECK D — Segment ATR mix by forward month (% of portfolio each segment)")
print("=" * 70)
q4 = f"""
SELECT
    RENEWAL_MONTH,
    ROUND(SUM(CASE WHEN SEGMENT = 'Core' THEN ATR ELSE 0 END) / NULLIF(SUM(ATR),0) * 100, 1) AS CORE_PCT,
    ROUND(SUM(CASE WHEN SEGMENT = 'Growth' THEN ATR ELSE 0 END) / NULLIF(SUM(ATR),0) * 100, 1) AS GROWTH_PCT,
    ROUND(SUM(CASE WHEN SEGMENT = 'Strategic' THEN ATR ELSE 0 END) / NULLIF(SUM(ATR),0) * 100, 1) AS STRATEGIC_PCT,
    ROUND(SUM(CASE WHEN SEGMENT = 'Emerging' THEN ATR ELSE 0 END) / NULLIF(SUM(ATR),0) * 100, 1) AS EMERGING_PCT,
    ROUND(SUM(CASE WHEN SEGMENT NOT IN ('Core','Growth','Strategic','Emerging') THEN ATR ELSE 0 END) / NULLIF(SUM(ATR),0) * 100, 1) AS OTHER_PCT,
    SUM(ATR) AS TOTAL_ATR
FROM {APP}
WHERE IS_MATURED_MONTH = FALSE
  AND RENEWAL_MONTH >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1
LIMIT 24
"""
df4 = fetch_dataframe(q4, conn=conn)
print(df4.to_string(index=False))

# E) BASE_RATE per segment — is it constant or does it vary?
print("\n" + "=" * 70)
print("CHECK E — BASE_RATE per segment (should be constant = anchor)")
print("=" * 70)
q5 = f"""
SELECT
    SEGMENT,
    MIN(FINANCE_ANCHOR_RATE) * 100 AS BASE_MIN_PCT,
    MAX(FINANCE_ANCHOR_RATE) * 100 AS BASE_MAX_PCT,
    AVG(FINANCE_ANCHOR_RATE) * 100 AS BASE_AVG_PCT,
    MIN(FINANCE_SHRINK_SCALE) AS W_HOR_MIN,
    MAX(FINANCE_SHRINK_SCALE) AS W_HOR_MAX,
    AVG(V2_SHIFT_PP) AS AVG_SHIFT_PP,
    STDDEV(V2_SHIFT_PP) AS STD_SHIFT_PP
FROM {APP}
WHERE IS_MATURED_MONTH = FALSE
GROUP BY 1
ORDER BY 1
"""
df5 = fetch_dataframe(q5, conn=conn)
print(df5.to_string(index=False))

conn.close()
print("\nDone.")

