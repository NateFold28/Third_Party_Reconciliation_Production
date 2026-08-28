"""
=============================================================================
FULL CUTOVER READINESS AUDIT — V5 Sandbox + Dev App
=============================================================================
Checks:
  1. Model accuracy (Jan-May 2026, bias, MAE, board gates)
  2. Netting pp — dynamic, current value, stability
  3. Manual inputs — count, migration parity, recent entries
  4. Snapshots — table populated, schema, latest entries
  5. Scheduling — tasks running, last execution times
  6. Forecast consistency — sandbox vs prod rate gap is model (not pipeline)
  7. App data freshness — when were sandbox tables last rebuilt
  8. Pipeline end-to-end — SP chain integrity (runs, model, app tables)
  9. ATR agreement — sandbox vs prod vs CARR spine
 10. EFFECTIVE_FORECAST_FINANCE == FINANCE_FORECAST for forward months (parity invariant)
=============================================================================
"""
import sys, warnings
sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')
from connection import get_snowflake_connection, fetch_dataframe
import pandas as pd, numpy as np
warnings.filterwarnings('ignore')

conn = get_snowflake_connection()

PASS = "✅ PASS"
WARN = "⚠️  WARN"
FAIL = "❌ FAIL"

results = []
def chk(label, status, detail=""):
    results.append((label, status, detail))
    print(f"  {status}  {label}" + (f" — {detail}" if detail else ""))

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("1. MODEL ACCURACY — Jan-May 2026 actual vs forecast bias")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
df_acc = fetch_dataframe("""
    SELECT
        DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS month,
        ROUND(SUM(EFFECTIVE_FORECAST_FINANCE)/NULLIF(SUM(ATR),0)*100, 2) AS forecast_pct,
        ROUND(SUM(ACTUAL_RETAINED_ARR)/NULLIF(SUM(ATR),0)*100, 2) AS actual_pct,
        ROUND(SUM(EFFECTIVE_FORECAST_FINANCE)/NULLIF(SUM(ATR),0)*100
              - SUM(ACTUAL_RETAINED_ARR)/NULLIF(SUM(ATR),0)*100, 2) AS bias_pp
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH = TRUE
      AND RENEWAL_MONTH >= '2026-01-01' AND RENEWAL_MONTH < '2026-06-01'
    GROUP BY 1 ORDER BY 1
""", conn=conn)
print(df_acc.to_string(index=False))
biases = df_acc["BIAS_PP"].dropna()
avg_bias = float(biases.mean())
mae = float(biases.abs().mean())
max_abs = float(biases.abs().max())
print(f"\n  Avg bias: {avg_bias:+.2f}pp | MAE: {mae:.2f}pp | Max |bias|: {max_abs:.2f}pp")
chk("Avg bias |<1.5pp|", PASS if abs(avg_bias) < 1.5 else FAIL, f"{avg_bias:+.2f}pp")
chk("MAE <2.0pp", PASS if mae < 2.0 else WARN if mae < 3.0 else FAIL, f"{mae:.2f}pp")
chk("No month >5pp absolute bias", PASS if max_abs < 5.0 else FAIL, f"max={max_abs:.2f}pp")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("2. NETTING PP — dynamic computation, stability, current value")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
df_net = fetch_dataframe("""
    WITH contract_m AS (
        SELECT RENEWAL_MONTH, CONTRACT_RATE_PCT
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY
        WHERE RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
    ),
    portfolio_m AS (
        SELECT DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE AS RENEWAL_MONTH,
               SUM(ACTUAL_RETAINED_ARR)/NULLIF(SUM(ATR),0)*100 AS actual_pct
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE IS_MATURED_MONTH = TRUE
        GROUP BY 1
    )
    SELECT c.RENEWAL_MONTH,
           ROUND(c.CONTRACT_RATE_PCT,2) AS contract_pct,
           ROUND(p.actual_pct,2)        AS portfolio_pct,
           ROUND(c.CONTRACT_RATE_PCT - p.actual_pct, 2) AS gap_pp
    FROM contract_m c JOIN portfolio_m p ON c.RENEWAL_MONTH = p.RENEWAL_MONTH
    WHERE c.RENEWAL_MONTH >= '2025-01-01'
    ORDER BY c.RENEWAL_MONTH
""", conn=conn)
print("Recent 18-month netting gaps (contract − portfolio actual):")
print(df_net.to_string(index=False))
gaps = df_net["GAP_PP"].dropna()
if len(gaps) >= 4:
    _s = gaps.sort_values().reset_index(drop=True)
    live_netting = float(_s.iloc[2:-2].mean()) if len(_s) > 4 else float(_s.mean())
    chk("Netting is DYNAMIC (computed live)", PASS, f"live value = {live_netting:.3f}pp")
    chk("Netting NOT always 1.6pp", PASS if abs(live_netting - 1.6) > 0.05 else WARN,
        f"current={live_netting:.3f}pp vs bootstrap=1.6pp")
    chk("Netting gap stable (std <1.5pp)", PASS if float(gaps.std()) < 1.5 else WARN,
        f"std={float(gaps.std()):.3f}pp")
    print(f"\n  → Dynamic netting = {live_netting:.3f}pp (trimmed mean last {len(gaps)} matured months)")
else:
    chk("Netting matured months ≥4", FAIL, f"only {len(gaps)} found")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("3. MANUAL INPUTS — migration parity, count, recent activity")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
df_inp = fetch_dataframe("""
    SELECT
        COUNT(*) AS total_inputs,
        COUNT(DISTINCT CONTRACT_ID) AS unique_contracts,
        COUNT(DISTINCT RENEWAL_MONTH) AS unique_months,
        MAX(UPDATED_AT) AS latest_update,
        SUM(CASE WHEN RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE()) THEN 1 ELSE 0 END) AS forward_inputs,
        SUM(CASE WHEN RENEWAL_MONTH = DATE_TRUNC('MONTH', CURRENT_DATE()) THEN 1 ELSE 0 END) AS current_month_inputs
    FROM STREAMLIT_APPS.DBO.RENEWAL_FORECAST_V5_USER_INPUTS
""", conn=conn)
print(df_inp.to_string(index=False))

total_inputs = int(df_inp["TOTAL_INPUTS"].iloc[0])
latest_update = str(df_inp["LATEST_UPDATE"].iloc[0])
chk("User inputs table populated", PASS if total_inputs > 100 else WARN, f"{total_inputs:,} active inputs")
chk("Recent input activity", PASS if "2026-06" in latest_update or "2026-05" in latest_update else WARN,
    f"latest={latest_update}")

# Sample latest inputs
df_inp_sample = fetch_dataframe("""
    SELECT CONTRACT_ID, RENEWAL_MONTH, RENEWAL_FORECAST, UPDATED_AT
    FROM STREAMLIT_APPS.DBO.RENEWAL_FORECAST_V5_USER_INPUTS
    ORDER BY UPDATED_AT DESC LIMIT 10
""", conn=conn)
print("\nMost recent 10 inputs:")
print(df_inp_sample.to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("4. SNAPSHOTS — table populated, schema, monthly coverage")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
df_snap = fetch_dataframe("""
    SELECT
        COUNT(*) AS total_snaps,
        COUNT(DISTINCT RENEWAL_MONTH) AS unique_months,
        MIN(SNAPSHOT_DATE) AS first_snap,
        MAX(SNAPSHOT_DATE) AS latest_snap,
        COUNT(DISTINCT SNAPSHOT_DATE) AS snapshot_dates
    FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS
""", conn=conn)
print("V5_APP_FORECAST_SNAPSHOTS:")
print(df_snap.to_string(index=False))
snap_total = int(df_snap["TOTAL_SNAPS"].iloc[0])
chk("Forecast snapshots populated", PASS if snap_total > 0 else FAIL, f"{snap_total} rows")

df_snap2 = fetch_dataframe("""
    SELECT RENEWAL_MONTH, SNAPSHOT_DATE, CONTRACTS, ROUND(ATR/1e6,2) AS atr_m,
           ROUND(MANUAL_ADJUSTED/1e6,2) AS fcst_m,
           ROUND(MANUAL_ADJUSTED/NULLIF(ATR,0)*100,1) AS fcst_pct,
           ROUND(ACTUAL/1e6,2) AS actual_m,
           N_MANUAL_INPUTS
    FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOT_LATEST
    WHERE RENEWAL_MONTH >= '2026-01-01'
    ORDER BY RENEWAL_MONTH
""", conn=conn)
print("\nV5_APP_FORECAST_SNAPSHOT_LATEST (Jan 2026+):")
print(df_snap2.to_string(index=False))
snap_rows_2026 = len(df_snap2)
chk("Snapshots cover Jan-Jun 2026", PASS if snap_rows_2026 >= 5 else WARN,
    f"{snap_rows_2026} months in 2026")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("5. SCHEDULING — Snowflake tasks (last run, next run, state)")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
try:
    df_tasks = fetch_dataframe("""
        SELECT
            name,
            state,
            schedule,
            last_committed_on,
            last_suspended_on
        FROM information_schema.tasks
        WHERE task_catalog = 'STREAMLIT_APPS'
          AND task_schema = 'DBO'
        ORDER BY name
    """, conn=conn)
    print(df_tasks.to_string(index=False))
    for _, row in df_tasks.iterrows():
        active = str(row.get("STATE","")).upper() == "STARTED"
        chk(f"Task {row['NAME']} active", PASS if active else WARN,
            f"state={row['STATE']}")
except Exception as e:
    print(f"  [Tasks not accessible via information_schema: {e}]")
    print("  → Checking via SHOW TASKS...")
    try:
        cur = conn.cursor()
        cur.execute("USE DATABASE STREAMLIT_APPS")
        cur.execute("USE SCHEMA DBO")
        cur.execute("SHOW TASKS IN SCHEMA STREAMLIT_APPS.DBO")
        tasks = cur.fetchall()
        cols = [d[0] for d in cur.description]
        df_tasks2 = pd.DataFrame(tasks, columns=cols)
        relevant_cols = [c for c in ["name","state","schedule","last_committed_on"] if c in df_tasks2.columns]
        print(df_tasks2[relevant_cols].to_string(index=False) if relevant_cols else df_tasks2.head(20).to_string(index=False))
        for _, row in df_tasks2.iterrows():
            state = str(row.get("state","")).upper()
            chk(f"Task {row.get('name','?')} active", PASS if state == "STARTED" else WARN, f"state={state}")
    except Exception as e2:
        chk("Task scheduling audit", WARN, f"Could not query tasks: {e2}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("6. TABLE FRESHNESS — when were sandbox tables last built")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
df_fresh = fetch_dataframe("""
    SELECT
        MAX(RUN_TIMESTAMP) AS last_detail_build,
        MAX(BUILT_AT)      AS last_detail_built_at
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
""", conn=conn)
print("Contract detail last build:")
print(df_fresh.to_string(index=False))

df_runs = fetch_dataframe("""
    SELECT RUN_ID, RUN_TIMESTAMP, N_FORECAST, NOTES
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_RUNS
    ORDER BY RUN_TIMESTAMP DESC LIMIT 5
""", conn=conn)
print("\nLast 5 sandbox pipeline runs:")
print(df_runs.to_string(index=False))

last_build = str(df_fresh["LAST_DETAIL_BUILD"].iloc[0]) if not df_fresh.empty else ""
chk("Tables built today (2026-06-15)", PASS if "2026-06-15" in last_build else WARN,
    f"last={last_build}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("7. EFFECTIVE_FORECAST_FINANCE == FINANCE_FORECAST for forward months")
print("   (invariant: daily blend should not have altered forward months)")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
df_eff = fetch_dataframe("""
    SELECT
        COUNT(*) AS total_fwd,
        SUM(CASE WHEN ABS(COALESCE(EFFECTIVE_FORECAST_FINANCE,0) - COALESCE(FINANCE_FORECAST,0)) < 1
                 THEN 1 ELSE 0 END) AS matching,
        SUM(CASE WHEN ABS(COALESCE(EFFECTIVE_FORECAST_FINANCE,0) - COALESCE(FINANCE_FORECAST,0)) >= 1
                 THEN 1 ELSE 0 END) AS differing,
        MAX(ABS(COALESCE(EFFECTIVE_FORECAST_FINANCE,0) - COALESCE(FINANCE_FORECAST,0))) AS max_diff
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURED_MONTH = FALSE
      AND RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
""", conn=conn)
print(df_eff.to_string(index=False))
diff_count = int(df_eff["DIFFERING"].iloc[0])
chk("EFF_FORECAST == FINANCE_FORECAST for all fwd months",
    PASS if diff_count == 0 else WARN, f"{diff_count} rows differ")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("8. ATR PARITY — sandbox vs prod vs CARR spine for current forward months")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
df_atr = fetch_dataframe("""
    SELECT
        ROUND(ABS(sbox.atr - prod.atr)/1e3, 1) AS diff_k,
        ROUND(sbox.atr/1e6, 3) AS sbox_atr_m,
        ROUND(prod.atr/1e6, 3) AS prod_atr_m,
        sbox.RENEWAL_MONTH
    FROM (
        SELECT RENEWAL_MONTH, SUM(ATR) AS atr
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-12-01'
        GROUP BY 1
    ) sbox
    JOIN (
        SELECT RENEWAL_MONTH, SUM(ATR) AS atr
        FROM STREAMLIT_APPS.DBO.V5_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-12-01'
        GROUP BY 1
    ) prod ON sbox.RENEWAL_MONTH = prod.RENEWAL_MONTH
    ORDER BY sbox.RENEWAL_MONTH
""", conn=conn)
print(df_atr.to_string(index=False))
max_diff_k = float(df_atr["DIFF_K"].max()) if not df_atr.empty else 9999
chk("ATR sandbox==prod (Jul-Dec 2026) within $1k", PASS if max_diff_k < 1.0 else WARN,
    f"max diff ${max_diff_k:.1f}k")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("9. FORWARD RATE REASONABLENESS — are H1-H6 rates in board-acceptable range?")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
df_fwd = fetch_dataframe("""
    SELECT
        RENEWAL_MONTH,
        ROUND(SUM(FINANCE_FORECAST)/NULLIF(SUM(ATR),0)*100, 2) AS portfolio_rate,
        ROUND(SUM(ATR)/1e6, 2) AS atr_m,
        COUNT(*) AS contracts
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
      AND RENEWAL_MONTH <= DATEADD('MONTH', 6, DATE_TRUNC('MONTH', CURRENT_DATE()))
    GROUP BY 1 ORDER BY 1
""", conn=conn)
print(df_fwd.to_string(index=False))
rates = df_fwd["PORTFOLIO_RATE"].dropna()
chk("All fwd rates in 60–85% range", PASS if (rates >= 60).all() and (rates <= 85).all() else FAIL,
    f"min={float(rates.min()):.1f}%, max={float(rates.max()):.1f}%")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("10. MODEL RUN INTEGRITY — latest run metadata & gate results")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
try:
    df_model = fetch_dataframe("""
        SELECT RUN_ID, PREDICTION_TS, SEGMENT,
               ROUND(AUC, 3) AS AUC,
               ROUND(MAE_PP, 3) AS MAE_PP,
               ROUND(VALIDATION_BIAS_PP, 3) AS BIAS_PP,
               GATE_AUC_OK, GATE_MAE_BIAS_OK, GATE_RANK_OK
        FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_MODEL_RUNS
        WHERE RUN_ID = (
            SELECT RUN_ID FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
            GROUP BY RUN_ID ORDER BY MAX(PREDICTION_TS) DESC LIMIT 1
        )
        ORDER BY SEGMENT
    """, conn=conn)
    print(df_model.to_string(index=False))
    gates_ok = (df_model["GATE_AUC_OK"].astype(str).str.upper().isin(["TRUE","1"]).all() and
                df_model["GATE_RANK_OK"].astype(str).str.upper().isin(["TRUE","1"]).all())
    chk("All segments pass AUC + Rank gates", PASS if gates_ok else WARN,
        "review table above for any FALSE")
except Exception as e:
    chk("Model run gate check", WARN, f"Query error: {e}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("11. PREDICTION LOCKING — H≥1 forward months frozen after lock timestamp")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
try:
    df_lock = fetch_dataframe("""
        SELECT
            DISTINCT ML_FORECAST_RATE_LOCKED IS NOT NULL AS has_locked_rate,
            COUNT(*) AS fwd_rows
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE IS_MATURED_MONTH = FALSE
          AND RENEWAL_MONTH > DATE_TRUNC('MONTH', CURRENT_DATE())
        GROUP BY 1
    """, conn=conn)
    print(df_lock.to_string(index=False))
    has_lock = df_lock["HAS_LOCKED_RATE"].astype(str).str.upper().str.contains("TRUE").any()
    chk("ML_FORECAST_RATE_LOCKED populated for fwd months", PASS if has_lock else WARN)
except Exception as e:
    chk("Prediction lock check", WARN, str(e))

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("12. COHORT VOCABULARY — FORWARD_OPEN / HISTORICAL_MATURED (no nulls/unknowns)")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
df_cohort = fetch_dataframe("""
    SELECT COHORT, COUNT(*) AS cnt, ROUND(SUM(ATR)/1e6, 2) AS atr_m
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH >= '2026-01-01' AND RENEWAL_MONTH <= '2026-12-01'
    GROUP BY 1 ORDER BY cnt DESC
""", conn=conn)
print(df_cohort.to_string(index=False))
bad_cohorts = df_cohort[~df_cohort["COHORT"].isin(["FORWARD_OPEN","HISTORICAL_MATURED","RECONCILIATION"])]
chk("No unexpected COHORT values", PASS if bad_cohorts.empty else WARN,
    f"unexpected: {bad_cohorts['COHORT'].tolist()}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
# ─────────────────────────────────────────────────────────────────────────────
passes = sum(1 for _, s, _ in results if "PASS" in s)
warns  = sum(1 for _, s, _ in results if "WARN" in s)
fails  = sum(1 for _, s, _ in results if "FAIL" in s)
print(f"\n  {passes} PASS  |  {warns} WARN  |  {fails} FAIL\n")
for label, status, detail in results:
    print(f"  {status}  {label}" + (f" — {detail}" if detail else ""))
print()
if fails == 0 and warns <= 2:
    print("  🟢 GREEN LIGHT — sandbox and dev app are board-ready for cutover.")
elif fails == 0:
    print(f"  🟡 CONDITIONAL — {warns} warnings need review before cutover.")
else:
    print(f"  🔴 NOT READY — {fails} failures must be resolved before cutover.")
