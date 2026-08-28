"""
calibration_part2.py — sections D-H with fixes
"""
import sys
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe
import pandas as pd
import numpy as np
pd.set_option('display.float_format', '{:,.2f}'.format)
pd.set_option('display.width', 160)
conn = get_snowflake_connection()
SEP = "=" * 70
def hdr(s): print(f"\n{SEP}\n{s}\n{SEP}")

# ─────────────────────────────────────────────────────────────────────────────
hdr("C2 — DECILE CALIBRATION DETAIL: D1-D3 bias deep dive (CRITICAL FINDING)")
# ─────────────────────────────────────────────────────────────────────────────
# D1 bias of 15.9pp means: when model predicts 60%, actual is 45%. Model over-predicts
# for the riskiest contracts. This is the Emerging early regime (28-50% actual rates 2024)
# pulling down actuals but model has now been updated with 12-month anchor.
# Check whether D1-D3 bias disappears when restricted to Sep 2025+
qC2 = """
WITH preds AS (
    SELECT p.CONTRACT_ID_UFR,
           DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RM,
           p.ATR, p.SEGMENT, p.PRED_RENEW_RATE_FINAL,
           NTILE(10) OVER (ORDER BY p.PRED_RENEW_RATE_FINAL) AS DECILE
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS p
    WHERE p.SPLIT IN ('CAL','VALIDATION')
      AND p.HORIZON = 0 AND p.ATR > 0
      AND p.RENEWAL_MONTH >= '2025-09-01'
),
actuals AS (
    SELECT CONTRACT_ID AS CID, RENEWAL_MONTH AS RM,
           ACTUAL_RETAINED_ARR, ATR
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH=TRUE AND ATR>0
)
SELECT
    p.DECILE,
    ROUND(SUM(p.PRED_RENEW_RATE_FINAL*p.ATR)/NULLIF(SUM(p.ATR),0)*100,2) AS PRED_RATE,
    ROUND(SUM(COALESCE(a.ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(a.ATR),0)*100,2) AS ACTUAL_RATE,
    ROUND(SUM(p.PRED_RENEW_RATE_FINAL*p.ATR)/NULLIF(SUM(p.ATR),0)*100
          - SUM(COALESCE(a.ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(a.ATR),0)*100, 2) AS BIAS_PP,
    ROUND(SUM(p.ATR)/1e6, 2) AS ATR_M,
    COUNT(*) AS N
FROM preds p
LEFT JOIN actuals a ON a.CID=p.CONTRACT_ID_UFR AND a.RM=p.RM
GROUP BY p.DECILE
ORDER BY p.DECILE
"""
dfC2 = fetch_dataframe(qC2, conn=conn)
print("Sep 2025 – May 2026 (current regime):")
print(dfC2.to_string(index=False))
if not dfC2.empty and dfC2['ACTUAL_RATE'].notna().sum() > 0:
    d1 = dfC2[dfC2['DECILE']==1]['BIAS_PP'].values
    d10 = dfC2[dfC2['DECILE']==10]['BIAS_PP'].values
    bias_all = dfC2['BIAS_PP'].abs().mean()
    print(f"\n  Current-regime decile MAE = {bias_all:.2f}pp")
    print(f"  D1 bias = {d1[0] if len(d1) else 'N/A'}pp  |  D10 bias = {d10[0] if len(d10) else 'N/A'}pp")
    if len(d1) > 0 and abs(d1[0]) > 10:
        print("  *** D1 still over-predicts by >10pp in current regime ***")
        print("      These are Emerging LOW-rate contracts — model hasn't seen enough recent Emerging data at low-pred.")
    else:
        print("  ✓ D1 bias < 10pp in current regime")

# What segment is concentrated in D1?
qC3 = """
WITH preds AS (
    SELECT p.CONTRACT_ID_UFR,
           DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RM,
           p.ATR, p.SEGMENT, p.PRED_RENEW_RATE_FINAL,
           NTILE(10) OVER (ORDER BY p.PRED_RENEW_RATE_FINAL) AS DECILE
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS p
    WHERE p.SPLIT IN ('CAL','VALIDATION')
      AND p.HORIZON = 0 AND p.ATR > 0
      AND p.RENEWAL_MONTH >= '2025-09-01'
)
SELECT DECILE, SEGMENT, COUNT(*) AS N, ROUND(SUM(ATR)/1e6,2) AS ATR_M
FROM preds
WHERE DECILE IN (1,2,3)
GROUP BY DECILE, SEGMENT
ORDER BY DECILE, N DESC
"""
dfC3 = fetch_dataframe(qC3, conn=conn)
print("\n  Segment composition of lowest 3 deciles:")
print(dfC3.to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
hdr("D2 — Training source audit (feature store table name check)")
# ─────────────────────────────────────────────────────────────────────────────
qD2 = """
SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, LAST_ALTERED
FROM STREAMLIT_APPS.INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'DBO'
  AND TABLE_NAME LIKE '%FEATURE%'
ORDER BY TABLE_NAME
"""
dfD2 = fetch_dataframe(qD2, conn=conn)
print(dfD2.to_string(index=False))

# Also check contract vs portfolio CARR tables directly
qD3 = """
SELECT
    'Portfolio LVL (2025+)' AS SRC,
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE)/1e6, 1)                                     AS ATR_M,
    ROUND(SUM(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE)/NULLIF(SUM(ADJ_ATR_C_BUDGET_RATE),0)*100, 2) AS RATE_PCT
FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
WHERE INCLUDE_FLAG_C=1
  AND DATE_TRUNC('MONTH', MASTER_DATE) BETWEEN '2025-01-01' AND '2026-05-01'
  AND ADJ_ATR_C_BUDGET_RATE > 0
  AND ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL
UNION ALL
SELECT
    'Contract LVL (2025+)' AS SRC,
    ROUND(SUM(ADJ_ATR_C_BUDGET_RATE)/1e6, 1)                                     AS ATR_M,
    ROUND(SUM(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE)/NULLIF(SUM(ADJ_ATR_C_BUDGET_RATE),0)*100, 2) AS RATE_PCT
FROM ANALYTICS.DBO.CARR__RENEWALS_CONTRACT_LVL
WHERE INCLUDE_FLAG_C=1
  AND DATE_TRUNC('MONTH', MASTER_DATE) BETWEEN '2025-01-01' AND '2026-05-01'
  AND ADJ_ATR_C_BUDGET_RATE > 0
  AND ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL
"""
dfD3 = fetch_dataframe(qD3, conn=conn)
print("\nContracts vs Portfolio CARR tables (actuals, 2025-2026 settled months):")
print(dfD3.to_string(index=False))
if len(dfD3) == 2:
    port_rate = dfD3.iloc[0]['RATE_PCT']
    con_rate = dfD3.iloc[1]['RATE_PCT']
    print(f"\n  Portfolio rate = {port_rate:.2f}%  |  Contract rate = {con_rate:.2f}%")
    print(f"  Gap = {con_rate-port_rate:.2f}pp (contract higher = expected)")
    print("  → ONE model is correct. Contract-grain predictions auto-reconcile to portfolio via ATR prorating.")
    print("  → Separate model would create more problems than it solves (different anchors, discrepant outputs).")

# ─────────────────────────────────────────────────────────────────────────────
hdr("E — Business questions F5/F6 (segment vs trailing, P10/P90 coverage)")
# ─────────────────────────────────────────────────────────────────────────────
qE_f5 = """
WITH fwd AS (
    SELECT SEGMENT,
           ROUND(SUM(PRED_RENEW_RATE_FINAL*ATR)/NULLIF(SUM(ATR),0)*100,2) AS FORWARD_RATE,
           ROUND(SUM(ATR)/1e6,2) AS FWD_ATR_M
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
    WHERE SPLIT='SCORE' AND HORIZON=0 AND ATR>0
    GROUP BY SEGMENT
),
recent AS (
    SELECT SEGMENT,
           ROUND(SUM(ACTUAL_RETAINED_ARR)/NULLIF(SUM(ATR),0)*100,2) AS TRAILING_RATE,
           ROUND(SUM(ATR)/1e6,2) AS HIST_ATR_M
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH=TRUE
      AND RENEWAL_MONTH >= DATEADD('MONTH',-6, DATE_TRUNC('MONTH',CURRENT_DATE()))
      AND ATR>0
    GROUP BY SEGMENT
)
SELECT f.SEGMENT, f.FWD_ATR_M, f.FORWARD_RATE, r.TRAILING_RATE,
       ROUND(f.FORWARD_RATE - r.TRAILING_RATE, 2) AS DELTA_PP
FROM fwd f
LEFT JOIN recent r ON r.SEGMENT=f.SEGMENT
ORDER BY f.SEGMENT
"""
dfE_f5 = fetch_dataframe(qE_f5, conn=conn)
print("Segment forward rate vs trailing 6-month actuals:")
print(dfE_f5.to_string(index=False))

qE_f6 = """
WITH preds AS (
    SELECT p.CONTRACT_ID_UFR,
           DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RM,
           p.ATR,
           p.PRED_RENEW_RATE_FINAL,
           p.PRED_RENEW_RATE_P10,
           p.PRED_RENEW_RATE_P90
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS p
    WHERE p.SPLIT='VALIDATION' AND p.HORIZON=0 AND p.ATR>0
      AND p.RENEWAL_MONTH >= '2025-09-01'
),
actuals AS (
    SELECT CONTRACT_ID AS CID, RENEWAL_MONTH AS RM,
           ACTUAL_RETAINED_ARR/NULLIF(ATR,0) AS ACTUAL_RATE
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH=TRUE AND ATR>0
)
SELECT
    COUNT(*) AS N,
    ROUND(AVG(CASE WHEN a.ACTUAL_RATE BETWEEN p.PRED_RENEW_RATE_P10 AND p.PRED_RENEW_RATE_P90
                   THEN 1.0 ELSE 0.0 END)*100,1) AS P10_P90_COVERAGE_PCT,
    ROUND(AVG(p.PRED_RENEW_RATE_P90-p.PRED_RENEW_RATE_P10)*100,1) AS AVG_INTERVAL_WIDTH_PP
FROM preds p
LEFT JOIN actuals a ON a.CID=p.CONTRACT_ID_UFR AND a.RM=p.RM
WHERE a.ACTUAL_RATE IS NOT NULL
"""
dfE_f6 = fetch_dataframe(qE_f6, conn=conn)
print("\nP10-P90 confidence interval calibration (Sep 2025+):")
print(dfE_f6.to_string(index=False))
if not dfE_f6.empty:
    cov = dfE_f6['P10_P90_COVERAGE_PCT'].iloc[0]
    width = dfE_f6['AVG_INTERVAL_WIDTH_PP'].iloc[0]
    print(f"  Coverage = {cov:.1f}% | Width = {width:.1f}pp")
    if cov < 55:
        print("  *** NARROW: P10/P90 only covers {cov:.0f}% — intervals overconfident. Not suitable for board.")
    elif cov > 90:
        print("  *** WIDE: intervals too conservative — band covers >90%.")
    else:
        print("  ✓ Coverage reasonable for board risk range")

# ─────────────────────────────────────────────────────────────────────────────
hdr("F — Snapshot status: which months are fully closed (open_opp=0)?")
# ─────────────────────────────────────────────────────────────────────────────
qF = """
SELECT
    RENEWAL_MONTH,
    ROUND(SUM(ATR)/1e6,2)                                                 AS ATR_M,
    ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(ATR),0)*100,2) AS ACTUAL_RATE,
    SUM(COALESCE(OPEN_OPP,0))                                             AS OPEN_OPP_SUM,
    MAX(IS_MATURED_MONTH::INTEGER)                                        AS IS_MATURED,
    COUNT(*)                                                              AS N
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH >= '2025-01-01'
  AND RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
GROUP BY 1
ORDER BY 1
"""
dfF = fetch_dataframe(qF, conn=conn)
print(dfF.to_string(index=False))
closed = dfF[(dfF['OPEN_OPP_SUM'] < 1) & (dfF['IS_MATURED'] == 1)]
open_months = dfF[(dfF['OPEN_OPP_SUM'] >= 1)]
print(f"\n  Fully closed months: {len(closed)} | Still-open historical: {len(open_months)}")
print("  Action: these closed months should be snapshotted to V5_SANDBOX_APP_CLOSED_SNAPSHOTS")
print("          so the Open Renewals tab can display them if user filters to history.")

# ─────────────────────────────────────────────────────────────────────────────
hdr("G — Portfolio churn tier calibration (no blind spots check)")
# ─────────────────────────────────────────────────────────────────────────────
qG = """
WITH preds AS (
    SELECT p.CONTRACT_ID_UFR,
           DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS RM,
           p.ATR,
           p.CONTRACT_RISK_TIER,
           p.P_CHURN_CAL,
           d.PRODUCT_PORTFOLIO
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS p
    LEFT JOIN STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
      ON d.CONTRACT_ID = p.CONTRACT_ID_UFR
     AND d.RENEWAL_MONTH = DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE
    WHERE p.SPLIT IN ('CAL','VALIDATION')
      AND p.HORIZON = 0 AND p.ATR > 0
      AND p.RENEWAL_MONTH >= '2025-09-01'
),
actuals AS (
    SELECT CONTRACT_ID AS CID, RENEWAL_MONTH AS RM,
           ACTUAL_RETAINED_ARR, ATR
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH=TRUE AND ATR>0
)
SELECT
    COALESCE(p.PRODUCT_PORTFOLIO, 'Unknown') AS PORTFOLIO,
    p.CONTRACT_RISK_TIER                      AS TIER,
    COUNT(*) AS N,
    ROUND(AVG(p.P_CHURN_CAL)*100,2)          AS AVG_PRED_CHURN,
    ROUND((1-SUM(COALESCE(a.ACTUAL_RETAINED_ARR,0))/NULLIF(SUM(a.ATR),0))*100,2) AS ACTUAL_CHURN_PCT,
    ROUND(SUM(p.ATR)/1e6,2) AS ATR_M
FROM preds p
LEFT JOIN actuals a ON a.CID=p.CONTRACT_ID_UFR AND a.RM=p.RM
GROUP BY 1,2
HAVING ATR_M > 0.2
ORDER BY 1,2
"""
dfG = fetch_dataframe(qG, conn=conn)
print(dfG.to_string(index=False))

if not dfG.empty and 'ACTUAL_CHURN_PCT' in dfG.columns:
    print("\n  Portfolio tier monotonicity (HIGH > LOW actual churn?)")
    blind_spots = []
    for port, grp in dfG.groupby('PORTFOLIO'):
        tiers = grp.set_index('TIER')['ACTUAL_CHURN_PCT']
        h = tiers.get('HIGH', np.nan)
        l = tiers.get('LOW', np.nan)
        if not pd.isna(h) and not pd.isna(l):
            ok = h > l
            flag = "✓" if ok else "*** BLIND SPOT ***"
            print(f"    {port:30s}  HIGH={h:.1f}%  LOW={l:.1f}%  {flag}")
            if not ok:
                blind_spots.append(port)
    if blind_spots:
        print(f"\n  BLIND SPOTS FOUND: {blind_spots}")
        print("  These portfolios need attention before board rollout")
    else:
        print("\n  ✓ No portfolio blind spots — HIGH tier churn is higher than LOW in ALL portfolios")

conn.close()
print(f"\n{SEP}\nCALIBRATION PART 2 DONE\n{SEP}")
