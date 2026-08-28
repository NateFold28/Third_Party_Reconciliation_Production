"""
validate_v5_production_readiness.py
====================================
Full production-readiness validation for V5 renewal forecast.
Run after SP_V5_SANDBOX_RUN_PIPELINE to confirm all checks pass.

Checks:
  A — Backtest quality (MAE / Bias pp by segment after date-fix)
  B — September anomaly diagnosis (composition, base rates, calibration shift)
  C — Blending math verification (confirm formula is correct per horizon)
  D — Model quality (AUC proxy: sorted decile lift, RMSE by horizon)
  E — Forward rates by segment (confirm Emerging/Growth post anchor fix)
  F — Prediction stability (SCORE rows: do rates look distribution-stable?)
  G — Quarter-over-quarter board numbers (Q2 actual, Q3 forecast, Q4 forecast)
  H — Contract vs portfolio gap (should be 1-2pp, matches history)
  I — ATR reconciliation final check
"""
import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe
import pandas as pd
pd.set_option('display.float_format', '{:,.2f}'.format)

conn = get_snowflake_connection()

SEP = "=" * 70
def hdr(label): print(f"\n{SEP}\n{label}\n{SEP}")

# ──────────────────────────────────────────────────────────────────────────────
hdr("A — Backtest quality after DATE_TRUNC fix (sandbox backtest vs actuals)")
# ──────────────────────────────────────────────────────────────────────────────
qA = """
WITH bt AS (
    SELECT
        b.RENEWAL_MONTH,
        b.SEGMENT,
        b.PRED_RATE_PCT,
        b.ACTUAL_RATE_PCT,
        b.PRED_ARR_M,
        b.ACTUAL_ARR_M,
        b.ATR_M,
        (b.PRED_RATE_PCT - b.ACTUAL_RATE_PCT) AS ERROR_PP
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_BACKTEST b
    WHERE b.ATR_M > 0
      AND b.ACTUAL_RATE_PCT IS NOT NULL
      AND b.ACTUAL_RATE_PCT > 0
)
SELECT
    RENEWAL_MONTH,
    SEGMENT,
    ROUND(PRED_RATE_PCT, 2)   AS PRED_PCT,
    ROUND(ACTUAL_RATE_PCT, 2) AS ACTUAL_PCT,
    ROUND(ERROR_PP, 2)        AS ERROR_PP,
    ROUND(ATR_M, 2)           AS ATR_M,
    ROUND(ABS(ERROR_PP), 2)   AS ABS_ERROR_PP
FROM bt
ORDER BY RENEWAL_MONTH, SEGMENT
"""
try:
    dfA = fetch_dataframe(qA, conn=conn)
    if dfA.empty:
        print("  *** BACKTEST STILL EMPTY — rebuild tables in Snowsight then re-run ***")
    else:
        # Summary stats
        mae = dfA['ABS_ERROR_PP'].mean()
        bias = dfA['ERROR_PP'].mean()
        n_months_with_actuals = dfA['RENEWAL_MONTH'].nunique()
        print(dfA.to_string(index=False))
        print(f"\n  MAE  = {mae:.2f}pp | Bias = {bias:.2f}pp | Months w/ actuals = {n_months_with_actuals}")
        if mae > 5:
            print("  *** WARNING: MAE > 5pp — model accuracy concern ***")
        elif mae > 3:
            print("  *** WATCH: MAE 3-5pp — acceptable but investigate large outliers ***")
        else:
            print("  ✓  MAE <= 3pp — PASS")
except Exception as e:
    print(f"  ERROR: {e}")

# ──────────────────────────────────────────────────────────────────────────────
hdr("B — September anomaly: segment composition vs other months")
# ──────────────────────────────────────────────────────────────────────────────
qB = """
SELECT
    RENEWAL_MONTH,
    SEGMENT,
    COUNT(DISTINCT CONTRACT_ID_UFR)         AS N_CONTRACTS,
    ROUND(SUM(ATR)/1e6, 2)                  AS ATR_M,
    ROUND(SUM(ATR) / SUM(SUM(ATR)) OVER (PARTITION BY RENEWAL_MONTH) * 100, 1) AS PCT_OF_MONTH_ATR,
    ROUND(AVG(BASE_RATE)*100, 2)            AS AVG_BASE_RATE_PCT,
    ROUND(SUM(BASE_RATE*ATR)/NULLIF(SUM(ATR),0)*100, 2) AS WTD_BASE_RATE_PCT,
    ROUND(AVG(W_HORIZON)*100, 2)            AS W_HORIZON_PCT,
    ROUND(SUM(PRED_RENEW_RATE_FINAL*ATR)/NULLIF(SUM(ATR),0)*100, 2) AS WTD_FINAL_RATE_PCT
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
WHERE SPLIT = 'SCORE'
  AND RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
  AND HORIZON = 0
GROUP BY 1,2
ORDER BY 1,2
"""
try:
    dfB = fetch_dataframe(qB, conn=conn)
    print(dfB.to_string(index=False))
    # Flag if Sep has unusual Strategic/Emerging weight
    if not dfB.empty:
        sep_rows = dfB[dfB['RENEWAL_MONTH'].astype(str).str.startswith('2026-09')]
        aug_rows = dfB[dfB['RENEWAL_MONTH'].astype(str).str.startswith('2026-08')]
        if not sep_rows.empty and not aug_rows.empty:
            sep_wtd = (sep_rows['WTD_FINAL_RATE_PCT'] * sep_rows['ATR_M']).sum() / sep_rows['ATR_M'].sum()
            aug_wtd = (aug_rows['WTD_FINAL_RATE_PCT'] * aug_rows['ATR_M']).sum() / aug_rows['ATR_M'].sum()
            print(f"\n  Sep WTD final = {sep_wtd:.2f}%  |  Aug WTD final = {aug_wtd:.2f}%")
            if sep_wtd < aug_wtd - 1.5:
                print("  *** Sep is compositionally lower — see WTD_BASE_RATE_PCT vs other months ***")
except Exception as e:
    print(f"  ERROR: {e}")

# ──────────────────────────────────────────────────────────────────────────────
hdr("C — Blending math check: confirm formula BASE + W*DELTA(capped at 7pp)")
# ──────────────────────────────────────────────────────────────────────────────
qC = """
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RM,
    HORIZON,
    W_HORIZON,
    ROUND(AVG(BASE_RATE)*100, 3)                              AS AVG_BASE,
    ROUND(AVG(ML_RAW_RATE)*100, 3)                            AS AVG_ML_RAW,
    ROUND(AVG(PRED_RENEW_RATE_FINAL)*100, 3)                  AS AVG_FINAL,
    -- Expected if formula = base + w * clip(ml-base, -0.07, 0.07)
    ROUND((AVG(BASE_RATE) + AVG(W_HORIZON) *
           GREATEST(-0.07, LEAST(0.07, AVG(ML_RAW_RATE) - AVG(BASE_RATE))))*100, 3) AS FORMULA_CHECK,
    ROUND(AVG(CALIBRATION_SHIFT)*100, 3)                      AS AVG_CALIB_SHIFT
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
WHERE SPLIT = 'SCORE'
  AND RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
  AND HORIZON IN (0,1,2,3,4,5)
  AND ATR > 0
GROUP BY 1,2,3
ORDER BY 1,2
"""
try:
    dfC = fetch_dataframe(qC, conn=conn)
    print(dfC.to_string(index=False))
    # Highlight Sep H3
    sep_h3 = dfC[(dfC['RM'].astype(str).str.startswith('2026-09')) & (dfC['HORIZON']==3)]
    if not sep_h3.empty:
        row = sep_h3.iloc[0]
        print(f"\n  Sep H3: Base={row['AVG_BASE']:.2f}% | ML_RAW={row['AVG_ML_RAW']:.2f}% | " +
              f"Final={row['AVG_FINAL']:.2f}% | FormulaCheck={row['FORMULA_CHECK']:.2f}% | " +
              f"CalibShift={row['AVG_CALIB_SHIFT']:.2f}pp")
        gap = row['AVG_FINAL'] - row['AVG_BASE']
        print(f"  Sep final vs base gap = {gap:.2f}pp")
        if gap < -2:
            print("  *** CALIBRATION SHIFT pulling Sep significantly below base — investigate ***")
except Exception as e:
    print(f"  ERROR: {e}")

# ──────────────────────────────────────────────────────────────────────────────
hdr("D — Backtest decile lift (model discrimination power)")
# ──────────────────────────────────────────────────────────────────────────────
qD = """
WITH preds AS (
    SELECT
        CONTRACT_ID_UFR,
        DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RM,
        SEGMENT,
        ATR,
        PRED_RENEW_RATE_FINAL,
        NTILE(5) OVER (PARTITION BY DATE_TRUNC('MONTH', RENEWAL_MONTH), SEGMENT
                       ORDER BY PRED_RENEW_RATE_FINAL) AS PRED_QUINTILE
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
    WHERE SPLIT IN ('CAL','VALIDATION')
      AND HORIZON = 0
      AND ATR > 0
),
actuals AS (
    SELECT
        CONTRACT_ID   AS CONTRACT_ID_UFR,
        RENEWAL_MONTH AS RM,
        ACTUAL_RETAINED_ARR,
        ATR
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH = TRUE
      AND ATR > 0
)
SELECT
    p.PRED_QUINTILE,
    ROUND(AVG(p.PRED_RENEW_RATE_FINAL)*100, 2)                          AS AVG_PRED_RATE,
    ROUND(SUM(a.ACTUAL_RETAINED_ARR) / NULLIF(SUM(a.ATR), 0) * 100, 2) AS ACTUAL_RATE,
    SUM(a.ATR)/1e6                                                        AS ATR_M,
    COUNT(*)                                                              AS N
FROM preds p
LEFT JOIN actuals a ON a.CONTRACT_ID_UFR = p.CONTRACT_ID_UFR AND a.RM = p.RM
GROUP BY 1
ORDER BY 1
"""
try:
    dfD = fetch_dataframe(qD, conn=conn)
    print(dfD.to_string(index=False))
    if not dfD.empty and dfD['ACTUAL_RATE'].notna().sum() > 0:
        q1 = dfD[dfD['PRED_QUINTILE']==1]['ACTUAL_RATE'].values
        q5 = dfD[dfD['PRED_QUINTILE']==5]['ACTUAL_RATE'].values
        if len(q1) > 0 and len(q5) > 0 and q1[0] is not None and q5[0] is not None:
            lift = float(q5[0]) - float(q1[0])
            print(f"\n  Q5 vs Q1 lift = {lift:.1f}pp  (>15pp = strong, >8pp = acceptable)")
            if lift > 15:
                print("  ✓  Strong discrimination — PASS")
            elif lift > 8:
                print("  ✓  Acceptable discrimination — PASS")
            else:
                print("  *** Weak discrimination — model may not rank contracts well ***")
        else:
            print("  *** Cannot compute lift — actuals still empty (backtest table not rebuilt) ***")
except Exception as e:
    print(f"  ERROR: {e}")

# ──────────────────────────────────────────────────────────────────────────────
hdr("E — Forward rates by segment after anchor fix (12-month window)")
# ──────────────────────────────────────────────────────────────────────────────
qE = """
SELECT
    DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
    p.SEGMENT,
    ROUND(SUM(p.ATR)/1e6, 2)                                                       AS ATR_M,
    ROUND(SUM(p.PRED_RENEW_RATE_FINAL*p.ATR)/NULLIF(SUM(p.ATR),0)*100, 2)         AS WTD_FINAL_RATE_PCT,
    ROUND(SUM(p.BASE_RATE*p.ATR)/NULLIF(SUM(p.ATR),0)*100, 2)                     AS WTD_BASE_RATE_PCT,
    ROUND(SUM(p.PRED_RENEW_RATE_FINAL*p.ATR)/NULLIF(SUM(p.ATR),0)*100
          - SUM(p.BASE_RATE*p.ATR)/NULLIF(SUM(p.ATR),0)*100, 2)                   AS FINAL_VS_BASE_PP
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS p
WHERE p.SPLIT = 'SCORE'
  AND p.RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
  AND p.HORIZON = 0
  AND p.ATR > 0
GROUP BY 1,2
ORDER BY 1,2
"""
try:
    dfE = fetch_dataframe(qE, conn=conn)
    print(dfE.to_string(index=False))
    # Check Emerging and Growth
    emerging = dfE[dfE['SEGMENT']=='Emerging']
    if not emerging.empty:
        em_rates = emerging['WTD_FINAL_RATE_PCT'].values
        print(f"\n  Emerging forward range: {em_rates.min():.1f}% – {em_rates.max():.1f}%")
        if em_rates.min() < 55:
            print("  *** Emerging still too low (<55%) — anchor fix may not have propagated yet ***")
        elif em_rates.min() >= 60:
            print("  ✓  Emerging anchor fix confirmed")
except Exception as e:
    print(f"  ERROR: {e}")

# ──────────────────────────────────────────────────────────────────────────────
hdr("F — Q-o-Q board numbers: Q2 actual / Q3 forecast / Q4 forecast")
# ──────────────────────────────────────────────────────────────────────────────
qF = """
WITH combined AS (
    -- Actuals: Q2 (Apr May Jun 2026 — Jun is in-flight)
    SELECT
        CASE
            WHEN RENEWAL_MONTH BETWEEN '2026-04-01' AND '2026-06-01' THEN 'Q2-2026 (Apr-Jun)'
            WHEN RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-09-01' THEN 'Q3-2026 (Jul-Sep)'
            WHEN RENEWAL_MONTH BETWEEN '2026-10-01' AND '2026-12-01' THEN 'Q4-2026 (Oct-Dec)'
        END AS QUARTER,
        RENEWAL_MONTH,
        SUM(ATR)                                    AS ATR,
        SUM(COALESCE(ACTUAL_RETAINED_ARR, 0))       AS ACTUAL_RETAINED,
        SUM(COALESCE(RENEWAL_FORECAST, 0))          AS MODEL_FORECAST,
        -- Current blend: actual-to-date + model on open
        SUM(COALESCE(ACTUAL_RETAINED_ARR, 0)
            + COALESCE(OPEN_OPP, 0))                AS FLOOR_FORECAST,
        COUNT(DISTINCT CONTRACT_ID)                 AS N_CONTRACTS,
        SUM(CASE WHEN IS_MATURED_MONTH THEN 1 ELSE 0 END) AS N_MATURED
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-04-01' AND '2026-12-01'
    GROUP BY 1,2
)
SELECT
    QUARTER,
    RENEWAL_MONTH,
    ROUND(ATR/1e6, 2)                                                 AS ATR_M,
    ROUND(ACTUAL_RETAINED/1e6, 2)                                     AS ACTUAL_RETAINED_M,
    ROUND(MODEL_FORECAST/1e6, 2)                                      AS MODEL_FORECAST_M,
    ROUND(ACTUAL_RETAINED/NULLIF(ATR,0)*100, 2)                       AS ACTUAL_RATE_TO_DATE,
    ROUND(MODEL_FORECAST/NULLIF(ATR,0)*100, 2)                        AS MODEL_RATE,
    N_MATURED, N_CONTRACTS
FROM combined
WHERE QUARTER IS NOT NULL
ORDER BY RENEWAL_MONTH
"""
try:
    dfF = fetch_dataframe(qF, conn=conn)
    print(dfF.to_string(index=False))

    # Q3 quarterly summary
    qF2 = """
    SELECT
        CASE
            WHEN RENEWAL_MONTH BETWEEN '2026-04-01' AND '2026-06-01' THEN 'Q2-2026'
            WHEN RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-09-01' THEN 'Q3-2026'
            WHEN RENEWAL_MONTH BETWEEN '2026-10-01' AND '2026-12-01' THEN 'Q4-2026'
        END AS QUARTER,
        ROUND(SUM(ATR)/1e6, 2)                                              AS TOTAL_ATR_M,
        ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR,0))/1e6, 2)                 AS ACTUAL_M,
        ROUND(SUM(COALESCE(RENEWAL_FORECAST,0))/1e6, 2)                    AS MODEL_M,
        ROUND(SUM(COALESCE(RENEWAL_FORECAST,0))/NULLIF(SUM(ATR),0)*100, 2) AS MODEL_RATE_PCT
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-04-01' AND '2026-12-01'
    GROUP BY 1
    HAVING QUARTER IS NOT NULL
    ORDER BY 1
    """
    dfF2 = fetch_dataframe(qF2, conn=conn)
    print("\n  --- Quarterly Rollup ---")
    print(dfF2.to_string(index=False))
    q3 = dfF2[dfF2['QUARTER']=='Q3-2026']['MODEL_RATE_PCT']
    if not q3.empty:
        print(f"\n  Q3-2026 forecast rate = {q3.values[0]:.2f}%")
        if q3.values[0] < 68:
            print("  *** Q3 forecast below 68% — Sep dip is dragging the quarter ***")
except Exception as e:
    print(f"  ERROR: {e}")

# ──────────────────────────────────────────────────────────────────────────────
hdr("G — Contract vs portfolio gap (board rate vs Finance rate)")
# ──────────────────────────────────────────────────────────────────────────────
qG = """
WITH contract_view AS (
    SELECT
        RENEWAL_MONTH,
        ROUND(SUM(CONTRACT_ATR)/1e6, 2)                                            AS CONTRACT_ATR_M,
        ROUND(SUM(CONTRACT_FORECAST_RATE_PCT * CONTRACT_ATR)/NULLIF(SUM(CONTRACT_ATR),0)*100, 2) AS CONTRACT_RATE_PCT
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
    WHERE RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
    GROUP BY 1
),
portfolio_view AS (
    SELECT
        RENEWAL_MONTH,
        ROUND(SUM(ATR)/1e6, 2)                                              AS PORT_ATR_M,
        ROUND(SUM(RENEWAL_FORECAST)/NULLIF(SUM(ATR),0)*100, 2)             AS PORT_RATE_PCT
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
    GROUP BY 1
)
SELECT
    c.RENEWAL_MONTH,
    c.CONTRACT_ATR_M,
    c.CONTRACT_RATE_PCT                  AS CONTRACT_BOARD_RATE,
    p.PORT_RATE_PCT                      AS PORTFOLIO_RATE,
    ROUND(c.CONTRACT_RATE_PCT - p.PORT_RATE_PCT, 2) AS GAP_PP
FROM contract_view c
JOIN portfolio_view p ON p.RENEWAL_MONTH = c.RENEWAL_MONTH
ORDER BY c.RENEWAL_MONTH
"""
try:
    dfG = fetch_dataframe(qG, conn=conn)
    print(dfG.to_string(index=False))
    if not dfG.empty:
        avg_gap = dfG['GAP_PP'].mean()
        print(f"\n  Average contract vs portfolio gap = {avg_gap:.2f}pp (historical: 0.87-2.70pp)")
        if avg_gap < 0:
            print("  *** Gap is NEGATIVE — contract rate lower than portfolio, check architecture ***")
        elif 0.5 <= avg_gap <= 3.5:
            print("  ✓  Gap in expected range — PASS")
        else:
            print(f"  *** Gap {avg_gap:.2f}pp outside expected 0.5-3.5pp range ***")
except Exception as e:
    print(f"  ERROR: {e}")

# ──────────────────────────────────────────────────────────────────────────────
hdr("H — Prediction stability: SCORE rate distribution (check for outliers)")
# ──────────────────────────────────────────────────────────────────────────────
qH = """
SELECT
    DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RM,
    ROUND(AVG(PRED_RENEW_RATE_FINAL)*100, 2)                           AS AVG_RATE,
    ROUND(STDDEV(PRED_RENEW_RATE_FINAL)*100, 2)                        AS STDDEV_PP,
    ROUND(MIN(PRED_RENEW_RATE_FINAL)*100, 2)                           AS MIN_RATE,
    ROUND(PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY PRED_RENEW_RATE_FINAL)*100,2) AS P5_RATE,
    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY PRED_RENEW_RATE_FINAL)*100,2) AS MEDIAN_RATE,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY PRED_RENEW_RATE_FINAL)*100,2) AS P95_RATE,
    ROUND(MAX(PRED_RENEW_RATE_FINAL)*100, 2)                           AS MAX_RATE,
    COUNT(*)                                                            AS N,
    SUM(CASE WHEN PRED_RENEW_RATE_FINAL < 0.10 THEN 1 ELSE 0 END)     AS N_BELOW_10PCT,
    SUM(CASE WHEN PRED_RENEW_RATE_FINAL > 0.99 THEN 1 ELSE 0 END)     AS N_ABOVE_99PCT
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
WHERE SPLIT = 'SCORE'
  AND RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
  AND HORIZON = 0
  AND ATR > 0
GROUP BY 1
ORDER BY 1
"""
try:
    dfH = fetch_dataframe(qH, conn=conn)
    print(dfH.to_string(index=False))
    if not dfH.empty:
        below10 = dfH['N_BELOW_10PCT'].sum()
        above99 = dfH['N_ABOVE_99PCT'].sum()
        if below10 > 0:
            print(f"  *** {int(below10)} contracts predicted < 10% renewal rate — review ***")
        if above99 > 0:
            print(f"  *** {int(above99)} contracts predicted > 99% renewal rate — review ***")
        if below10 == 0 and above99 == 0:
            print("  ✓  No extreme outliers — PASS")
except Exception as e:
    print(f"  ERROR: {e}")

# ──────────────────────────────────────────────────────────────────────────────
hdr("I — Sandbox anchor rates after 12-month fix (confirm Emerging > 60%)")
# ──────────────────────────────────────────────────────────────────────────────
# These are the fallback anchors now embedded in V5_SANDBOX_APP_CONTRACT_DETAIL
qI = """
SELECT
    SEGMENT,
    SUM(CASE WHEN RUN_ID = 'V5_ANCHOR_FALLBACK' THEN 1 ELSE 0 END) AS N_FALLBACK,
    SUM(CASE WHEN RUN_ID != 'V5_ANCHOR_FALLBACK' THEN 1 ELSE 0 END) AS N_SCORED,
    ROUND(SUM(CASE WHEN RUN_ID = 'V5_ANCHOR_FALLBACK' THEN ATR ELSE 0 END)/1e6, 3) AS FALLBACK_ATR_M,
    ROUND(
        SUM(CASE WHEN RUN_ID = 'V5_ANCHOR_FALLBACK' THEN RENEWAL_FORECAST ELSE 0 END)
        / NULLIF(SUM(CASE WHEN RUN_ID = 'V5_ANCHOR_FALLBACK' THEN ATR ELSE 0 END), 0) * 100
    , 2) AS FALLBACK_RATE_PCT,
    ROUND(
        SUM(CASE WHEN RUN_ID != 'V5_ANCHOR_FALLBACK' THEN RENEWAL_FORECAST ELSE 0 END)
        / NULLIF(SUM(CASE WHEN RUN_ID != 'V5_ANCHOR_FALLBACK' THEN ATR ELSE 0 END), 0) * 100
    , 2) AS SCORED_RATE_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
GROUP BY SEGMENT
ORDER BY SEGMENT
"""
try:
    dfI = fetch_dataframe(qI, conn=conn)
    print(dfI.to_string(index=False))
    em = dfI[dfI['SEGMENT']=='Emerging']
    if not em.empty:
        fb_rate = em['FALLBACK_RATE_PCT'].values[0]
        print(f"\n  Emerging fallback rate = {fb_rate:.1f}%")
        if fb_rate < 55:
            print("  *** Still using old 24-month anchor (46%). Tables not rebuilt with fix yet.")
            print("      Run: CALL STREAMLIT_APPS.DBO.SP_V5_BUILD_APP_TABLES_V5_SHADOW(); ***")
        elif fb_rate >= 60:
            print("  ✓  Anchor fix propagated — Emerging fallback now realistic")
        else:
            print("  ⚠  Emerging fallback 55-60% — slightly improved but still conservative")
except Exception as e:
    print(f"  ERROR: {e}")

# ──────────────────────────────────────────────────────────────────────────────
hdr("J — Prediction locking: do SCORE row rates change on rebuild?")
# ──────────────────────────────────────────────────────────────────────────────
# Check if prediction table has a LOCKED flag or snapshot date
qJ = """
SELECT
    SPLIT,
    COUNT(DISTINCT RUN_ID)   AS N_RUN_IDS,
    MIN(RUN_ID)              AS FIRST_RUN_ID,
    MAX(RUN_ID)              AS LATEST_RUN_ID,
    MAX(PREDICTION_TS)       AS LATEST_TS,
    COUNT(*)                 AS N_ROWS
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
GROUP BY 1
ORDER BY 1
"""
try:
    dfJ = fetch_dataframe(qJ, conn=conn)
    print(dfJ.to_string(index=False))

    # Check if there's a LOCKED_FLAG or similar column
    qJ2 = """
    SELECT COLUMN_NAME, DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'DBO'
      AND TABLE_NAME = 'ML_SANDBOX_V5_PREDICTIONS'
      AND TABLE_CATALOG = 'STREAMLIT_APPS'
    ORDER BY ORDINAL_POSITION
    """
    dfJ2 = fetch_dataframe(qJ2, conn=conn)
    print("\n  Prediction table columns:")
    print(dfJ2[['COLUMN_NAME','DATA_TYPE']].to_string(index=False))
    has_lock = 'LOCKED' in dfJ2['COLUMN_NAME'].str.upper().values
    has_score_date = 'SCORE_DATE' in dfJ2['COLUMN_NAME'].str.upper().values
    print(f"\n  Has LOCKED flag: {has_lock} | Has SCORE_DATE: {has_score_date}")
    if not has_lock:
        print("  *** NO prediction locking mechanism — SCORE predictions will change on every rebuild ***")
        print("      Fix needed: add LOCKED_AS_OF DATE column + lock_predictions logic in app ***")
except Exception as e:
    print(f"  ERROR: {e}")

print(f"\n{SEP}\nDONE — review all sections above\n{SEP}")
conn.close()
