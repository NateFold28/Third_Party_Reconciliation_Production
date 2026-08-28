"""
Full audit of V5_20260611_175205:
- Forward forecast continuity (blends into historicals)
- Contract-level vs portfolio-level separation
- Walk-forward gates (full + near-horizon)
- Segment monotonicity
- Hardcoded constants audit
"""
import sys, json
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection

conn = get_snowflake_connection()
cur = conn.cursor()
run_id = 'V5_20260611_175205'

def q(sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    print(f"  {'  '.join(str(c)[:18].ljust(18) for c in cols)}")
    print(f"  {'  '.join('-'*18 for _ in cols)}")
    for r in rows:
        print(f"  {'  '.join(str(v)[:18].ljust(18) for v in r)}")
    print()

# ── 1. Historical → forward blend at PREDICTION level (H=0 only) ─────────────────────────────
print("=== 1. HISTORICAL → FORWARD BLEND (contract-level predictions, H=0) ===")
q(f"""
SELECT
  DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RM,
  CASE WHEN SPLIT IN ('TRAIN','CAL','VALIDATION') THEN 'HISTORY' ELSE 'FORWARD' END AS PERIOD,
  ROUND(SUM(PRED_RENEW_RATE_FINAL * ATR) / NULLIF(SUM(ATR),0) * 100, 2) AS PRED_RATE,
  COUNT(DISTINCT CONTRACT_ID_UFR) AS N_CONTRACTS
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
WHERE RUN_ID = '{run_id}' AND HORIZON = 0
  AND RENEWAL_MONTH >= DATEADD('MONTH', -18, CURRENT_DATE)
GROUP BY 1,2
ORDER BY 1
""")

# ── 2. Portfolio-level from app tables ────────────────────────────────────────
print("=== 2. PORTFOLIO-LEVEL APP TABLE (V5_SANDBOX_APP_CONTRACT_DETAIL) ===")
q(f"""
SELECT
  RENEWAL_MONTH::DATE AS RM,
  ROUND(SUM(RENEWAL_FORECAST)/NULLIF(SUM(ATR),0)*100,2) AS PORTFOLIO_MODEL_RATE,
  ROUND(SUM(ACTUAL_RETAINED_ARR)/NULLIF(SUM(CASE WHEN ACTUAL_RETAINED_ARR IS NOT NULL THEN ATR END),0)*100,2) AS ACTUAL_RATE,
  COUNT(DISTINCT CONTRACT_ID) AS N_CONTRACTS
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RUN_ID != 'V5_ANCHOR_FALLBACK'
  AND RENEWAL_MONTH >= DATEADD('MONTH', -12, CURRENT_DATE)
GROUP BY 1 ORDER BY 1
""")

# ── 3. Contract-level vs portfolio separation ─────────────────────────────────
print("=== 3. CONTRACT vs PORTFOLIO RATE (should: contract > portfolio by ~1-2pp) ===")
q(f"""
WITH contract_rates AS (
  SELECT
    RENEWAL_MONTH::DATE AS RM,
    ROUND(SUM(PRED_RENEW_RATE_FINAL * ATR)/NULLIF(SUM(ATR),0)*100,2) AS CONTRACT_LVL_RATE,
    COUNT(DISTINCT CONTRACT_ID_UFR) AS N
  FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
  WHERE RUN_ID = '{run_id}' AND SPLIT = 'SCORE' AND HORIZON = 0
  GROUP BY 1
),
portfolio_rate AS (
  SELECT
    DATE_TRUNC('MONTH', MASTER_DATE)::DATE AS RM,
    ROUND(SUM(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE)/NULLIF(SUM(ADJ_ATR_C_BUDGET_RATE),0)*100,2) AS PORT_RATE
  FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
  WHERE INCLUDE_FLAG_C = 1
    AND MASTER_DATE >= DATEADD('MONTH', -6, CURRENT_DATE)
    AND MASTER_DATE < DATE_TRUNC('MONTH', CURRENT_DATE)
  GROUP BY 1
)
SELECT c.RM, c.CONTRACT_LVL_RATE, p.PORT_RATE,
  ROUND(c.CONTRACT_LVL_RATE - COALESCE(p.PORT_RATE,0),2) AS SPREAD_PP,
  c.N
FROM contract_rates c
LEFT JOIN portfolio_rate p ON c.RM = p.RM
ORDER BY 1
""")

# ── 4. Walk-forward gates ─────────────────────────────────────────────────────
print("=== 4. WALK-FORWARD GATES (latest run) ===")
q(f"""
SELECT
  SEGMENT,
  HORIZON_BUCKET,
  ROUND(MAE_PP,3) AS MAE_PP,
  ROUND(BIAS_PP,3) AS BIAS_PP,
  ROUND(RANK_SPEARMAN,3) AS SPEARMAN,
  BEATS_NAIVE,
  N_CONTRACTS
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_WALK_FORWARD
WHERE RUN_ID = '{run_id}'
ORDER BY SEGMENT, HORIZON_BUCKET
""")

# ── 5. Segment monotonicity (H0 should be closest to recent actual) ────────────
print("=== 5. SEGMENT FORWARD MONOTONICITY (SCORE) ===")
q(f"""
SELECT SEGMENT,
  ROUND(AVG(CASE WHEN HORIZON=0 THEN PRED_RENEW_RATE_FINAL END)*100,2) AS H0,
  ROUND(AVG(CASE WHEN HORIZON=1 THEN PRED_RENEW_RATE_FINAL END)*100,2) AS H1,
  ROUND(AVG(CASE WHEN HORIZON=2 THEN PRED_RENEW_RATE_FINAL END)*100,2) AS H2,
  ROUND(AVG(CASE WHEN HORIZON=3 THEN PRED_RENEW_RATE_FINAL END)*100,2) AS H3,
  ROUND(AVG(CASE WHEN HORIZON=4 THEN PRED_RENEW_RATE_FINAL END)*100,2) AS H4,
  ROUND(AVG(CASE WHEN HORIZON=5 THEN PRED_RENEW_RATE_FINAL END)*100,2) AS H5
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
WHERE RUN_ID = '{run_id}' AND SPLIT = 'SCORE'
GROUP BY 1 ORDER BY 1
""")

# ── 6. Recent backtest accuracy (last 6 matured months, H=0) ─────────────────
print("=== 6. RECENT BACKTEST (app table, last 6 settled months) ===")
q(f"""
SELECT
  RENEWAL_MONTH::DATE AS RM,
  SEGMENT,
  ROUND(SUM(ML_FORECAST)/NULLIF(SUM(ATR),0)*100,2) AS PRED,
  ROUND(SUM(FINANCE_RENEWED_GROSS)/NULLIF(SUM(FINANCE_ATR),0)*100,2) AS ACTUAL_FINANCE,
  ROUND((SUM(ML_FORECAST)/NULLIF(SUM(ATR),0) - SUM(FINANCE_RENEWED_GROSS)/NULLIF(SUM(FINANCE_ATR),0))*100,2) AS BIAS_PP
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE IS_MATURED_MONTH = TRUE
  AND RENEWAL_MONTH >= DATEADD('MONTH', -7, CURRENT_DATE)
  AND RUN_ID != 'V5_ANCHOR_FALLBACK'
GROUP BY 1,2 ORDER BY 2,1
""")

# ── 7. PSI: how many features survived, any severe accepted ──────────────────
print("=== 7. PSI GATE SUMMARY ===")
q(f"""
SELECT
  PSI_BUCKET,
  COUNT(*) AS N_FEATURES,
  SUM(IS_ACCEPTED) AS N_ACCEPTED,
  COUNT(*) - SUM(IS_ACCEPTED) AS N_DROPPED
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PSI_AUDIT
WHERE RUN_ID = '{run_id}'
GROUP BY 1 ORDER BY 1
""")

cur.close()
conn.close()
print("=== AUDIT COMPLETE ===")
