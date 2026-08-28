"""
audit_june_forecast.py
----------------------
Diagnoses the May 73.7% → June 69.7% apparent drop in the V5 sandbox model.

Questions answered:
  1. Is June genuinely a different contract cohort (different ATR mix)?
  2. What is driving the lower rate — segment mix, base rate, or ML delta?
  3. Is W_HORIZON at H=0 for June or H=1+? (affects how much ML signal applies)
  4. Per-segment predicted rates for May vs June — is one segment blowing up?
  5. Are the anchor-fallback contracts pulling the rate down?

Usage:
  .\.venv\Scripts\python.exe TEMPLATES\Python\audit_june_forecast.py
"""
from __future__ import annotations
import sys
sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")
from connection import get_snowflake_connection, fetch_dataframe
import pandas as pd
import numpy as np

conn = get_snowflake_connection()

# ── 1. Latest run ID ─────────────────────────────────────────────────────────
run_df = fetch_dataframe("""
    SELECT RUN_ID, MAX(PREDICTION_TS) AS TS
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
    GROUP BY RUN_ID
    ORDER BY TS DESC
    LIMIT 1
""", conn=conn)
run_id = run_df.iloc[0]["RUN_ID"]
print(f"\n{'='*70}")
print(f"Run ID: {run_id}")
print(f"{'='*70}")

# ── 2. May vs June cohort overview (all contracts, SCORE+history) ─────────────
print("\n\n── 2. MAY vs JUNE  contract cohort summary (model's view) ──────────────")
cohort = fetch_dataframe(f"""
    SELECT
        DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE  AS MONTH,
        SPLIT,
        COUNT(*)                                   AS N_CONTRACTS,
        ROUND(SUM(ATR)/1e6, 2)                     AS ATR_M,
        ROUND(AVG(ATR)/1e3, 1)                     AS AVG_CONTRACT_ATR_K,
        ROUND(MEDIAN(ATR)/1e3, 1)                  AS MEDIAN_CONTRACT_ATR_K,
        ROUND(SUM(FINAL_DOLLARS)/NULLIF(SUM(ATR),0)*100, 2) AS PRED_RATE_PCT,
        ROUND(AVG(PRED_RENEW_RATE_FINAL)*100, 2)   AS AVG_PRED_RATE_PCT,
        ROUND(AVG(W_HORIZON), 3)                   AS AVG_W_HORIZON,
        ROUND(AVG(HORIZON), 2)                     AS AVG_HORIZON,
        COUNT_IF(HORIZON = 0)                      AS N_H0,
        COUNT_IF(HORIZON = 1)                      AS N_H1,
        COUNT_IF(HORIZON = 2)                      AS N_H2
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
    WHERE RUN_ID = '{run_id}'
      AND DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE IN ('2026-05-01','2026-06-01')
    GROUP BY 1, 2
    ORDER BY 1, 2
""", conn=conn)
print(cohort.to_string(index=False))

# ── 3. App-table view: May vs June (full CARR spine, inc. anchor fallbacks) ──
print("\n\n── 3. APP TABLE: May vs June full cohort (portfolio grain) ─────────────")
app_cohort = fetch_dataframe("""
    SELECT
        DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE   AS MONTH,
        RUN_ID,
        COUNT(*)                                    AS N_ROWS,
        COUNT(DISTINCT CONTRACT_ID)                 AS N_CONTRACTS,
        ROUND(SUM(ATR)/1e6, 2)                      AS ATR_M,
        ROUND(SUM(RENEWAL_FORECAST)/NULLIF(SUM(ATR),0)*100, 2) AS FORECAST_PCT,
        ROUND(SUM(ML_FORECAST)/NULLIF(SUM(ATR),0)*100, 2)      AS ML_PCT,
        ROUND(SUM(ACTUAL_RETAINED_ARR)/NULLIF(SUM(ATR),0)*100, 2) AS ACTUAL_PCT,
        COUNT_IF(RUN_ID = 'V5_ANCHOR_FALLBACK')     AS N_FALLBACK
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH IN ('2026-05-01','2026-06-01')
    GROUP BY 1, 2
    ORDER BY 1
""", conn=conn)
print(app_cohort.to_string(index=False))

# ── 4. Per-segment breakdown for May vs June (SCORE split, best horizon) ─────
print("\n\n── 4. PER-SEGMENT: May vs June predicted rates ─────────────────────────")
seg = fetch_dataframe(f"""
    WITH scored AS (
        SELECT
            DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE  AS MONTH,
            SEGMENT,
            ATR,
            FINAL_DOLLARS,
            PRED_RENEW_RATE_FINAL,
            W_HORIZON,
            BASE_RATE,
            ML_DELTA,
            HORIZON
        FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
        WHERE RUN_ID = '{run_id}'
          AND DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE IN ('2026-05-01','2026-06-01')
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY CONTRACT_ID_UFR, DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE
            ORDER BY IFF(SPLIT = 'SCORE', 0, 1), HORIZON ASC
        ) = 1
    )
    SELECT
        MONTH,
        SEGMENT,
        COUNT(*)                                          AS N,
        ROUND(SUM(ATR)/1e6, 2)                            AS ATR_M,
        ROUND(SUM(ATR)/1e6 / SUM(SUM(ATR)/1e6) OVER (PARTITION BY MONTH) * 100, 1) AS ATR_SHARE_PCT,
        ROUND(AVG(ATR)/1e3, 0)                            AS AVG_CONTRACT_ATR_K,
        ROUND(SUM(FINAL_DOLLARS)/NULLIF(SUM(ATR),0)*100, 2) AS PRED_RATE_PCT,
        ROUND(AVG(BASE_RATE)*100, 2)                      AS AVG_BASE_RATE_PCT,
        ROUND(AVG(ML_DELTA)*100, 3)                       AS AVG_ML_DELTA_PP,
        ROUND(AVG(W_HORIZON), 3)                          AS AVG_W_HORIZON,
        ROUND(AVG(HORIZON), 2)                            AS AVG_HORIZON
    FROM scored
    GROUP BY 1, 2
    ORDER BY 1, ATR_M DESC
""", conn=conn)
print(seg.to_string(index=False))

# ── 5. ATR concentration: top 20 June contracts ──────────────────────────────
print("\n\n── 5. TOP 20 JUNE CONTRACTS BY ATR (what drives the dollar-weighted rate) ─")
top20 = fetch_dataframe(f"""
    WITH best AS (
        SELECT
            CONTRACT_ID_UFR,
            SEGMENT,
            ATR,
            FINAL_DOLLARS,
            PRED_RENEW_RATE_FINAL,
            BASE_RATE,
            ML_DELTA,
            W_HORIZON,
            HORIZON,
            SPLIT
        FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
        WHERE RUN_ID = '{run_id}'
          AND DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE = '2026-06-01'
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY CONTRACT_ID_UFR
            ORDER BY IFF(SPLIT = 'SCORE', 0, 1), HORIZON ASC
        ) = 1
    )
    SELECT
        CONTRACT_ID_UFR,
        SEGMENT,
        ROUND(ATR/1e3, 0)                              AS ATR_K,
        ROUND(PRED_RENEW_RATE_FINAL*100, 1)            AS PRED_RATE_PCT,
        ROUND(BASE_RATE*100, 1)                        AS BASE_RATE_PCT,
        ROUND(ML_DELTA*100, 2)                         AS ML_DELTA_PP,
        ROUND(W_HORIZON, 3)                            AS W_HORIZON,
        HORIZON,
        SPLIT
    FROM best
    ORDER BY ATR DESC
    LIMIT 20
""", conn=conn)
print(top20.to_string(index=False))

# ── 6. Dollar-concentration check: how much of June ATR is in top-10 contracts?
print("\n\n── 6. DOLLAR CONCENTRATION: June (does a handful of large contracts drive rate?) ─")
conc = fetch_dataframe(f"""
    WITH best AS (
        SELECT
            CONTRACT_ID_UFR,
            ATR,
            FINAL_DOLLARS,
            PRED_RENEW_RATE_FINAL
        FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
        WHERE RUN_ID = '{run_id}'
          AND DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE = '2026-06-01'
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY CONTRACT_ID_UFR
            ORDER BY IFF(SPLIT = 'SCORE', 0, 1), HORIZON ASC
        ) = 1
    ),
    ranked AS (
        SELECT *,
            ROW_NUMBER() OVER (ORDER BY ATR DESC) AS ATR_RANK,
            SUM(ATR) OVER ()                       AS TOTAL_ATR
        FROM best
    )
    SELECT
        ATR_RANK,
        ROUND(ATR/1e3, 0)                          AS ATR_K,
        ROUND(PRED_RENEW_RATE_FINAL*100, 1)        AS PRED_RATE_PCT,
        ROUND(SUM(ATR) OVER (ORDER BY ATR DESC ROWS UNBOUNDED PRECEDING) / TOTAL_ATR * 100, 1) AS CUMUL_ATR_PCT
    FROM ranked
    WHERE ATR_RANK <= 20
    ORDER BY ATR_RANK
""", conn=conn)
print(conc.to_string(index=False))

# ── 7. May vs June BASE_RATE distribution (is June structurally lower?) ──────
print("\n\n── 7. BASE_RATE distribution: May vs June (count-weighted histogram) ───")
hist = fetch_dataframe(f"""
    WITH best AS (
        SELECT
            DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS MONTH,
            BASE_RATE,
            ATR
        FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
        WHERE RUN_ID = '{run_id}'
          AND DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE IN ('2026-05-01','2026-06-01')
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY CONTRACT_ID_UFR, DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE
            ORDER BY IFF(SPLIT = 'SCORE', 0, 1), HORIZON ASC
        ) = 1
    )
    SELECT
        MONTH,
        ROUND(AVG(BASE_RATE)*100, 2)          AS AVG_BASE_PCT,
        ROUND(MEDIAN(BASE_RATE)*100, 2)       AS MEDIAN_BASE_PCT,
        -- ATR-dollar-weighted base rate
        ROUND(SUM(BASE_RATE * ATR)/NULLIF(SUM(ATR),0)*100, 2) AS ATR_WTD_BASE_PCT,
        ROUND(STDDEV(BASE_RATE)*100, 2)       AS STDDEV_BASE_PP,
        ROUND(MIN(BASE_RATE)*100, 1)          AS MIN_BASE_PCT,
        ROUND(MAX(BASE_RATE)*100, 1)          AS MAX_BASE_PCT,
        -- quintile breakdown
        ROUND(PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY BASE_RATE)*100, 1) AS P10,
        ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY BASE_RATE)*100, 1) AS P25,
        ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY BASE_RATE)*100, 1) AS P75,
        ROUND(PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY BASE_RATE)*100, 1) AS P90
    FROM best
    GROUP BY 1
    ORDER BY 1
""", conn=conn)
print(hist.to_string(index=False))

# ── 8. Headline summary ───────────────────────────────────────────────────────
print("\n\n── 8. HEADLINE SUMMARY ─────────────────────────────────────────────────")
summary = fetch_dataframe(f"""
    WITH best AS (
        SELECT
            DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS MONTH,
            ATR, FINAL_DOLLARS, PRED_RENEW_RATE_FINAL, BASE_RATE, W_HORIZON
        FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
        WHERE RUN_ID = '{run_id}'
          AND DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE IN ('2026-05-01','2026-06-01')
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY CONTRACT_ID_UFR, DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE
            ORDER BY IFF(SPLIT = 'SCORE', 0, 1), HORIZON ASC
        ) = 1
    )
    SELECT
        MONTH,
        COUNT(*)                                                             AS N_CONTRACTS,
        ROUND(SUM(ATR)/1e6, 2)                                              AS ATR_M,
        ROUND(SUM(ATR)/SUM(COUNT(*)) OVER () / 1e3, 0)                      AS AVG_ATR_K_ACROSS_MONTHS,
        ROUND(SUM(FINAL_DOLLARS)/NULLIF(SUM(ATR),0)*100, 2)                 AS DOLLAR_WTD_PRED_RATE_PCT,
        ROUND(AVG(PRED_RENEW_RATE_FINAL)*100, 2)                            AS COUNT_WTD_PRED_RATE_PCT,
        ROUND(SUM(BASE_RATE*ATR)/NULLIF(SUM(ATR),0)*100, 2)                 AS DOLLAR_WTD_BASE_RATE_PCT,
        ROUND(AVG(BASE_RATE)*100, 2)                                        AS COUNT_WTD_BASE_RATE_PCT,
        ROUND(AVG(W_HORIZON), 3)                                            AS AVG_W_HORIZON
    FROM best
    GROUP BY 1
    ORDER BY 1
""", conn=conn)
print(summary.to_string(index=False))
print("""
KEY INTERPRETATION GUIDE
─────────────────────────────────────────────────────────────────────────
DOLLAR_WTD_PRED_RATE_PCT   = what the chart shows (large contracts dominate)
COUNT_WTD_PRED_RATE_PCT    = what you'd get if all contracts were equal size
DOLLAR_WTD_BASE_RATE_PCT   = the anchor/seasonal floor for large contracts
AVG_W_HORIZON              = 0.65 at H=0, 0.55 at H=1 — lower = less ML signal

If DOLLAR_WTD_BASE_RATE_PCT for June << May:
  → June has a genuinely lower-renewing set of large contracts (cohort effect)
  → The drop is real and should be communicated to Finance

If DOLLAR_WTD_BASE_RATE_PCT for June ≈ May but DOLLAR_WTD_PRED_RATE_PCT << May:
  → One or two large contracts are being scored low by ML — investigate top-20
  → May be a specific at-risk account the model is correctly flagging

If COUNT_WTD ≈ May but DOLLAR_WTD << May:
  → Concentration: a few very large contracts with low predicted rates are driving it
  → Check section 5 (top 20) and 6 (concentration)
─────────────────────────────────────────────────────────────────────────
""")

conn.close()
