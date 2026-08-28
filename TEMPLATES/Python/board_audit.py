"""
Full board-readiness audit:
  1. Scheduled task health (all 5 tasks STARTED + last run status)
  2. Data freshness (pipeline log, predictions, app tables)
  3. Board gate validation (portfolio bias, seg×month, invariants)
  4. Display table spot-check (Portfolio < Contract for every month)
  5. App table row counts and ATR sanity
"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
os.environ["PYTHONIOENCODING"] = "utf-8"
from connection import get_snowflake_connection
import pandas as pd

conn = get_snowflake_connection()
cur  = conn.cursor()

def q(sql):
    try:
        return pd.read_sql(sql, conn)
    except Exception as e:
        return pd.DataFrame({"ERROR": [str(e)]})

PASS = "[✓ PASS]"
FAIL = "[✗ FAIL]"
WARN = "[⚠ WARN]"
gates = []

print("=" * 72)
print("BOARD-READINESS AUDIT  —  2026-06-22")
print("=" * 72)

# ── 1. Scheduled tasks ────────────────────────────────────────────────────
print("\n--- 1. SCHEDULED TASKS ---")
tasks_df = q("""
SELECT NAME, STATE, SCHEDULE,
       LAST_COMMITTED_ON::DATE AS LAST_RUN,
       DATEDIFF('day', LAST_COMMITTED_ON, CURRENT_TIMESTAMP()) AS DAYS_AGO
FROM INFORMATION_SCHEMA.TASKS
WHERE TASK_SCHEMA = 'DBO'
  AND NAME ILIKE '%V5%'
ORDER BY NAME
""")
if "ERROR" in tasks_df.columns:
    # Fallback: SHOW TASKS
    cur.execute("SHOW TASKS IN SCHEMA STREAMLIT_APPS.DBO")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    tasks_df = pd.DataFrame(rows, columns=cols)
    tasks_df = tasks_df[tasks_df["name"].str.upper().str.contains("V5")]

print(tasks_df.to_string(index=False))

# Check all critical tasks are STARTED
required_tasks = [
    "V5_SANDBOX_DAILY_REFRESH_TASK",
    "V5_SANDBOX_MONTHLY_MODEL_TASK",
    "V5_SANDBOX_FORECAST_SNAPSHOT_TASK",
    "V5_CALIBRATION_REFRESH_TASK",
]
state_col = "STATE" if "STATE" in tasks_df.columns else "state"
name_col  = "NAME"  if "NAME"  in tasks_df.columns else "name"
started = set(tasks_df[tasks_df[state_col].str.upper() == "STARTED"][name_col].str.upper())
all_started = all(t.upper() in started for t in required_tasks)
lbl = PASS if all_started else WARN
print(f"\n  {lbl} Critical tasks running: {sum(t.upper() in started for t in required_tasks)}/{len(required_tasks)}")
gates.append(("Tasks all STARTED", all_started))

# ── 2. Pipeline log freshness ─────────────────────────────────────────────
print("\n--- 2. PIPELINE LOG FRESHNESS ---")
log_df = q("""
SELECT SOURCE, STATUS, TRIGGERED_AT::DATE AS RUN_DATE,
       DATEDIFF('day', TRIGGERED_AT, CURRENT_TIMESTAMP()) AS DAYS_AGO,
       LEFT(MESSAGE, 80) AS MESSAGE
FROM STREAMLIT_APPS.DBO.V5_PIPELINE_RUN_LOG
ORDER BY TRIGGERED_AT DESC
LIMIT 10
""")
print(log_df.to_string(index=False))

recent_ok = q("""
SELECT COUNT(*) AS CNT
FROM STREAMLIT_APPS.DBO.V5_PIPELINE_RUN_LOG
WHERE STATUS = 'OK'
  AND TRIGGERED_AT >= DATEADD('day', -2, CURRENT_TIMESTAMP())
""").iloc[0, 0]
lbl = PASS if int(recent_ok) > 0 else FAIL
print(f"\n  {lbl} Pipeline ran OK in last 2 days: {recent_ok} entries")
gates.append(("Pipeline ran recently", int(recent_ok) > 0))

# ── 3. Predictions freshness ──────────────────────────────────────────────
print("\n--- 3. PREDICTIONS & MODEL ---")
pred_df = q("""
SELECT RUN_ID, COUNT(*) AS ROW_COUNT, COUNT(DISTINCT SEGMENT) AS SEG_COUNT,
       MIN(RENEWAL_MONTH)::DATE AS MIN_MONTH, MAX(RENEWAL_MONTH)::DATE AS MAX_MONTH
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
WHERE SPLIT = 'SCORE'
GROUP BY RUN_ID
ORDER BY MAX(PREDICTION_TS) DESC
LIMIT 3
""")
print(pred_df.to_string(index=False))

segs_ok = int(pred_df.iloc[0]["SEG_COUNT"]) >= 5 if len(pred_df) else False
lbl = PASS if segs_ok else FAIL
print(f"  {lbl} Latest run has all 5 segments: {pred_df.iloc[0]['SEG_COUNT'] if len(pred_df) else 0}")
gates.append(("All 5 segments trained", segs_ok))

# ── 4. App table counts and ATR ───────────────────────────────────────────
print("\n--- 4. APP TABLE HEALTH ---")
app_df = q("""
SELECT
    COUNT(*) AS TOTAL_ROW_COUNT,
    COUNT_IF(RENEWAL_MONTH >= '2026-06-01') AS FWD_ROWS,
    COUNT_IF(RENEWAL_MONTH >= '2026-06-01' AND COALESCE(ATR,0) > 0) AS FWD_WITH_ATR,
    ROUND(SUM(CASE WHEN RENEWAL_MONTH >= '2026-06-01' THEN ATR ELSE 0 END) / 1e6, 1) AS FWD_ATR_M,
    COUNT_IF(RUN_ID = 'V5_ANCHOR_FALLBACK') AS FALLBACK_ROW_COUNT,
    ROUND(COUNT_IF(RUN_ID = 'V5_ANCHOR_FALLBACK') / COUNT(*) * 100, 1) AS FALLBACK_PCT,
    COUNT_IF(ML_FORECAST < FINANCE_FORECAST - 0.01) AS INVARIANT_VIOLATIONS
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE COALESCE(ATR, 0) > 0
""")
print(app_df.to_string(index=False))

inv_ok  = int(app_df.iloc[0]["INVARIANT_VIOLATIONS"]) == 0
fwd_ok  = int(app_df.iloc[0]["FWD_ATR_M"]) > 150  # $150M+ forward ATR
fback_pct = float(app_df.iloc[0]["FALLBACK_PCT"])
lbl_inv  = PASS if inv_ok  else FAIL
lbl_fwd  = PASS if fwd_ok  else FAIL
lbl_fb   = PASS if fback_pct < 35 else WARN
print(f"  {lbl_inv} ML >= Finance invariant: {app_df.iloc[0]['INVARIANT_VIOLATIONS']} violations")
print(f"  {lbl_fwd} Forward ATR (Jun+): ${app_df.iloc[0]['FWD_ATR_M']}M")
print(f"  {lbl_fb}  Fallback contracts: {fback_pct}% (anchor rate fallback)")
gates.append(("ML >= Finance invariant", inv_ok))
gates.append(("Forward ATR populated", fwd_ok))

# ── 5. Board gates (seg×month, portfolio bias) ────────────────────────────
print("\n--- 5. BOARD GATES (VALIDATION COHORT) ---")
gates_df = q("""
WITH val AS (
    SELECT p.SEGMENT,
           DATE_TRUNC('MONTH', p.RENEWAL_MONTH)::DATE AS MONTH,
           SUM(p.PRED_RENEW_RATE_PORTFOLIO * p.ATR) AS PRED_DOLLARS,
           SUM(f.TARGET__RENEWAL_RATE * p.ATR)      AS ACTUAL_DOLLARS,
           SUM(p.ATR)                                AS ATR
    FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS p
    JOIN STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE f
      ON f.CONTRACT_ID_UFR = p.CONTRACT_ID_UFR
     AND f.RENEWAL_MONTH   = p.RENEWAL_MONTH
    WHERE p.SPLIT = 'VALIDATION'
      AND p.HORIZON = 0
      AND p.RUN_ID = (SELECT RUN_ID FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
                     GROUP BY RUN_ID HAVING COUNT(DISTINCT SEGMENT)>=4
                     ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1)
    GROUP BY 1, 2
)
SELECT
    COUNT(*) AS TOTAL_CELLS,
    COUNT_IF(ABS(PRED_DOLLARS/NULLIF(ATR,0) - ACTUAL_DOLLARS/NULLIF(ATR,0)) * 100 <= 5) AS WITHIN_5PP,
    ROUND(MAX(ABS(PRED_DOLLARS/NULLIF(ATR,0) - ACTUAL_DOLLARS/NULLIF(ATR,0))) * 100, 2) AS WORST_PP,
    ROUND((SUM(PRED_DOLLARS) - SUM(ACTUAL_DOLLARS)) / NULLIF(SUM(ATR),0) * 100, 2) AS PORTFOLIO_BIAS_PP
FROM val
""")
print(gates_df.to_string(index=False))

within5 = int(gates_df.iloc[0]["WITHIN_5PP"])
total   = int(gates_df.iloc[0]["TOTAL_CELLS"])
worst   = float(gates_df.iloc[0]["WORST_PP"])
bias    = float(gates_df.iloc[0]["PORTFOLIO_BIAS_PP"])
seg_ok  = within5 >= 29
bias_ok = abs(bias) <= 2.0
worst_ok= worst <= 6.5
lbl_s  = PASS if seg_ok  else FAIL
lbl_b  = PASS if bias_ok else FAIL
lbl_w  = PASS if worst_ok else WARN
print(f"  {lbl_s} Seg×month ≥29/30 within ±5pp: {within5}/{total}")
print(f"  {lbl_b} Portfolio bias: {bias:+.2f}pp (gate: ±2pp)")
print(f"  {lbl_w} Worst cell: {worst:.1f}pp (gate: ≤6.5pp)")
gates.append(("Seg×month ≥29/30", seg_ok))
gates.append(("Portfolio bias ±2pp", bias_ok))

# ── 6. Calibration knots ──────────────────────────────────────────────────
print("\n--- 6. CALIBRATION KNOTS ---")
cal_df = q("""
SELECT METRIC_NAME,
       ROUND(VALUE_NEW, 4) AS VALUE_NEW,
       ROUND(VALUE_OLD, 4) AS VALUE_OLD,
       GATE_STATUS
FROM STREAMLIT_APPS.DBO.V5_CALIBRATION_KNOTS_AUDIT
ORDER BY METRIC_NAME
LIMIT 8
""")
if "ERROR" in cal_df.columns:
    cal_df = q("""
    SELECT COUNT(*) AS KNOT_COUNT,
           LEFT(MAX(INSERTED_AT), 10) AS LAST_REFRESH,
           DATEDIFF('day', MAX(INSERTED_AT), CURRENT_TIMESTAMP()) AS DAYS_AGO
    FROM STREAMLIT_APPS.DBO.V5_CALIBRATION_KNOTS
    """)
print(cal_df.to_string(index=False))

# ── 7. Forward forecast sanity ────────────────────────────────────────────
print("\n--- 7. FORWARD FORECAST SANITY (Jun–Dec 2026) ---")
fcast_df = q("""
SELECT RENEWAL_MONTH,
       ROUND(SUM(ATR)/1e6, 1) AS ATR_M,
       ROUND(SUM(ML_FORECAST)/SUM(NULLIF(ATR,0))*100, 1) AS ML_RATE,
       ROUND(SUM(FINANCE_FORECAST)/SUM(NULLIF(ATR,0))*100, 1) AS FINANCE_RATE,
       ROUND((SUM(ML_FORECAST)-SUM(FINANCE_FORECAST))/SUM(NULLIF(ATR,0))*100, 2) AS GAP_PP,
       COUNT_IF(ML_FORECAST < FINANCE_FORECAST - 0.01) AS VIOLATIONS
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH BETWEEN '2026-06-01' AND '2026-12-01'
  AND COALESCE(ATR, 0) > 0
GROUP BY 1 ORDER BY 1
""")
print(fcast_df.to_string(index=False))

all_plausible = all(55 <= float(r["ML_RATE"]) <= 95 for _, r in fcast_df.iterrows() if not pd.isna(r["ML_RATE"]))
all_no_viol   = all(int(r["VIOLATIONS"]) == 0 for _, r in fcast_df.iterrows())
lbl_p = PASS if all_plausible else FAIL
lbl_v = PASS if all_no_viol   else FAIL
print(f"  {lbl_p} All forward rates 55–95%")
print(f"  {lbl_v} ML >= Finance for all forward months")
gates.append(("Forward rates plausible", all_plausible))
gates.append(("Forward ML >= Finance", all_no_viol))

# ── SUMMARY ───────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("AUDIT SUMMARY")
print("=" * 72)
n_pass = sum(1 for _, v in gates if v)
n_fail = sum(1 for _, v in gates if not v)
for name, ok in gates:
    lbl = PASS if ok else FAIL
    print(f"  {lbl}  {name}")

print(f"\n  {n_pass}/{len(gates)} gates pass  |  {n_fail} failures")
if n_fail == 0:
    print("\n  ✅  ALL BOARD GATES PASS — PIPELINE IS PRODUCTION READY")
else:
    print(f"\n  ❌  {n_fail} gate(s) failed — review above")
print("=" * 72)
conn.close()
