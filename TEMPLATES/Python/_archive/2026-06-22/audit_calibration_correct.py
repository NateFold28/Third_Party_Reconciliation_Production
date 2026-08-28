"""
audit_calibration_correct.py
=============================
Correct audit of the V5 calibration pipeline.

ARCHITECTURE REMINDER:
  ML_FORECAST      = E_RENEWAL_RATE * ATR  = RAW uncalibrated LightGBM output
  FINANCE_FORECAST = FINAL_DOLLARS_PORTFOLIO = Two-stage calibrated (Stage 1 per-segment
                     level-shift + Stage 2 global ATR-weighted tie-out)
  RETENTION_PCT    = PRED_RENEW_RATE_FINAL * 100  = contract-level calibrated rate

  The app's "ML Forecast %" line = MODEL_RATE_PCT + netting
                                 = (FINANCE_FORECAST / ATR * 100) + netting

  PRIOR AUDIT BUG: used ML_FORECAST/ATR (raw uncalibrated) → showed fake +8.9pp bias.
  THIS AUDIT: uses FINANCE_FORECAST/ATR (calibrated) → correct comparison.

QUESTIONS ANSWERED:
  A. True calibrated model accuracy (Jan–May 2026): FINANCE_FORECAST/ATR vs actual
  B. What RUN_ID is in the sandbox table vs latest available? (staleness check)
  C. Netting accuracy: what is the blended netting pp and is it valid?
  D. July 2026 segment decomposition (FINANCE_FORECAST based)
  E. Manual overrides for July: are humans adding signal or correcting model?
  F. Per-segment calibrated bias (last 6 months) — actual vs FINANCE_FORECAST

Usage:
    cd c:\\Users\\Nate.Fold\\projects
    .venv\\Scripts\\python.exe TEMPLATES\\Python\\audit_calibration_correct.py
"""
from __future__ import annotations
import sys
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, r'c:\Users\Nate.Fold\projects\TEMPLATES\Python')

import numpy as np
import pandas as pd
from connection import get_snowflake_connection, fetch_dataframe

pd.set_option("display.float_format", "{:,.2f}".format)
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 25)

SEP = "=" * 80
def hdr(t: str) -> None:
    print(f"\n{SEP}\n{t}\n{SEP}")

conn = get_snowflake_connection()

# ═══════════════════════════════════════════════════════════════════════════════
# B — Staleness check: which RUN_ID is in the sandbox table?
#     If this doesn't match the latest run in ML_SANDBOX_V5_PREDICTIONS,
#     SP_V5_BUILD_APP_TABLES_V5_SHADOW needs to be re-run.
# ═══════════════════════════════════════════════════════════════════════════════
hdr("B — Sandbox Table Staleness Check")

SQL_B = """
SELECT
    'In V5_SANDBOX_APP_CONTRACT_DETAIL'  AS source,
    MAX(RUN_ID)                          AS run_id,
    MAX(RUN_TIMESTAMP)                   AS last_built
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
UNION ALL
SELECT
    'Latest in ML_SANDBOX_V5_PREDICTIONS' AS source,
    RUN_ID,
    MAX(PREDICTION_TS)                    AS last_built
FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS
GROUP BY RUN_ID
ORDER BY last_built DESC
LIMIT 1
"""
try:
    df_b = fetch_dataframe(SQL_B, conn=conn)
    print(df_b.to_string(index=False))
    if len(df_b) == 2:
        run_in_table = df_b.iloc[0]["run_id"]
        run_latest   = df_b.iloc[1]["run_id"]
        if run_in_table != run_latest:
            print(f"\n  ⚠️  STALE TABLE — sandbox table was built from {run_in_table}")
            print(f"     Latest model run is {run_latest}")
            print(f"     ACTION: Run SP_V5_BUILD_APP_TABLES_V5_SHADOW() in Snowsight to rebuild.")
        else:
            print(f"\n  ✅ Table is current (run: {run_in_table})")
except Exception as e:
    print(f"  Could not run staleness check: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# A — TRUE calibrated accuracy (Jan–May 2026)
#     FINANCE_FORECAST = FINAL_DOLLARS_PORTFOLIO = two-stage calibrated output
#     This is what MODEL_RATE_PCT / PORTFOLIO_NETTED_PCT in the app is built from.
# ═══════════════════════════════════════════════════════════════════════════════
hdr("A — True Calibrated Model Accuracy (FINANCE_FORECAST vs Actual, Jan–May 2026)")

SQL_A = """
SELECT
    DATE_TRUNC('MONTH', d.RENEWAL_DATE)::DATE              AS RENEWAL_MONTH,
    SUM(d.ATR)                                             AS ATR,
    -- Raw ML (E_RENEWAL_RATE * ATR) — intentionally uncalibrated, shown for reference
    SUM(d.ML_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100      AS ML_RAW_RATE_PCT,
    -- Calibrated output (FINAL_DOLLARS_PORTFOLIO = two-stage calibrated)
    SUM(d.FINANCE_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100 AS FINANCE_RATE_PCT,
    -- Actual
    SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100  AS ACTUAL_RATE_PCT,
    -- Bias: calibrated vs actual
    SUM(d.FINANCE_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100
        - SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100
                                                           AS CALIBRATED_BIAS_PP,
    -- Bias: raw vs actual
    SUM(d.ML_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100
        - SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100
                                                           AS RAW_BIAS_PP
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
WHERE d.IS_MATURED_MONTH = TRUE
  AND d.ATR > 0
  AND d.ACTUAL_RETAINED_ARR IS NOT NULL
  AND DATE_TRUNC('MONTH', d.RENEWAL_DATE) >= '2026-01-01'
GROUP BY 1
ORDER BY 1
"""
df_a = fetch_dataframe(SQL_A, conn=conn)
df_a["RENEWAL_MONTH"] = pd.to_datetime(df_a["RENEWAL_MONTH"]).dt.strftime("%Y-%m")
print(df_a.to_string(index=False))
cal_bias_avg = df_a["CALIBRATED_BIAS_PP"].mean()
raw_bias_avg = df_a["RAW_BIAS_PP"].mean()
print(f"\n  Avg calibrated bias (FINANCE_FORECAST): {cal_bias_avg:+.2f}pp")
print(f"  Avg raw bias        (ML_FORECAST raw) : {raw_bias_avg:+.2f}pp")
print(f"\n  INTERPRETATION:")
if abs(cal_bias_avg) <= 2.0:
    print(f"  ✅ Calibrated model is within ±2pp — pipeline is working correctly.")
    print(f"     The raw bias ({raw_bias_avg:+.1f}pp) is expected; that's what calibration corrects.")
elif abs(cal_bias_avg) <= 4.0:
    print(f"  ⚠️  Calibrated model is {cal_bias_avg:+.1f}pp — marginal. Quarterly recalibration may help.")
else:
    print(f"  ❌ Calibrated model is {cal_bias_avg:+.1f}pp — calibration needs refresh.")

# ═══════════════════════════════════════════════════════════════════════════════
# C — Netting: what is the actual netting value and is it double-counting?
#     Netting = gap between Finance contract-level rate and portfolio-level rate.
#     The model outputs FINANCE_FORECAST at PORTFOLIO grain.
#     Finance board uses CONTRACT-level actuals (allocated at contract grain).
#     Netting bridges the grain gap.
# ═══════════════════════════════════════════════════════════════════════════════
hdr("C — Netting Calculation (last 12 matured months)")

SQL_C = """
SELECT
    DATE_TRUNC('MONTH', d.RENEWAL_DATE)::DATE          AS RENEWAL_MONTH,
    -- Portfolio-grain: sum(actual) / sum(ATR)
    SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100
                                                       AS PORTFOLIO_ACTUAL_PCT,
    -- Contract-grain: avg of per-contract actuals (Finance board = ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE)
    AVG(d.CONTRACT_ACTUAL_PCT)                         AS CONTRACT_ACTUAL_PCT_AVG,
    -- Gap = netting (contract consistently above portfolio due to allocation math)
    AVG(d.CONTRACT_ACTUAL_PCT)
        - SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100
                                                       AS NETTING_GAP_PP,
    -- Calibrated model portfolio rate
    SUM(d.FINANCE_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100
                                                       AS CALIBRATED_MODEL_PCT,
    -- Expected contract-level (calibrated + netting)
    SUM(d.FINANCE_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100
        + (AVG(d.CONTRACT_ACTUAL_PCT)
           - SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100)
                                                       AS MODEL_PLUS_NETTING,
    -- Contract actual (what we're trying to match)
    AVG(d.CONTRACT_ACTUAL_PCT)                         AS CONTRACT_ACTUAL_TO_MATCH,
    COUNT(DISTINCT d.CONTRACT_ID)                      AS N_CONTRACTS
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
WHERE d.IS_MATURED_MONTH = TRUE
  AND d.ATR > 0
  AND d.ACTUAL_RETAINED_ARR IS NOT NULL
  AND d.CONTRACT_ACTUAL_PCT IS NOT NULL
  AND DATE_TRUNC('MONTH', d.RENEWAL_DATE) >= ADD_MONTHS(CURRENT_DATE(), -12)
GROUP BY 1
ORDER BY 1
"""
try:
    df_c = fetch_dataframe(SQL_C, conn=conn)
    df_c["RENEWAL_MONTH"] = pd.to_datetime(df_c["RENEWAL_MONTH"]).dt.strftime("%Y-%m")
    print(df_c[["RENEWAL_MONTH","CALIBRATED_MODEL_PCT","NETTING_GAP_PP",
                "MODEL_PLUS_NETTING","CONTRACT_ACTUAL_TO_MATCH"]].to_string(index=False))

    netting_vals = pd.to_numeric(df_c["NETTING_GAP_PP"], errors="coerce").dropna()
    trimmed = netting_vals.sort_values().iloc[2:-2] if len(netting_vals) > 4 else netting_vals
    live_netting = float(trimmed.mean()) if len(trimmed) > 0 else 1.6

    # Check if model+netting matches contract actual
    final_bias = (df_c["MODEL_PLUS_NETTING"] - df_c["CONTRACT_ACTUAL_TO_MATCH"]).mean()
    print(f"\n  Live trimmed-mean netting            : {live_netting:.2f}pp")
    print(f"  Calibrated model + netting vs actual : {final_bias:+.2f}pp avg bias")
    if abs(final_bias) <= 2.0:
        print(f"  ✅ The pipeline (calibration + netting) is well-calibrated end-to-end.")
    else:
        print(f"  ⚠️  Pipeline bias: {final_bias:+.1f}pp — likely a calibration or netting issue.")
except Exception as e:
    live_netting = 1.6
    print(f"  Could not compute netting (missing CONTRACT_ACTUAL_PCT?): {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# D — July 2026 decomposition (calibrated)
#     Shows July portfolio prediction layer by layer
# ═══════════════════════════════════════════════════════════════════════════════
hdr("D — July 2026 Segment Decomposition (Calibrated)")

SQL_D = """
SELECT
    d.SEGMENT,
    COUNT(DISTINCT d.CONTRACT_ID)                                   AS N_CONTRACTS,
    SUM(d.ATR)                                                      AS ATR,
    SUM(d.ML_FORECAST)   / NULLIF(SUM(d.ATR), 0) * 100             AS RAW_ML_RATE,
    SUM(d.FINANCE_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100          AS CALIBRATED_RATE,
    AVG(d.RETENTION_PCT)                                            AS AVG_RETENTION_PCT,
    AVG(d.CHURN_PCT)                                                AS AVG_CHURN_PCT,
    AVG(d.CHURN_PROBABILITY * 100)                                  AS AVG_P_CHURN_CAL_PCT
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
WHERE DATE_TRUNC('MONTH', d.RENEWAL_DATE) = '2026-07-01'
  AND d.ATR > 0
GROUP BY 1
ORDER BY ATR DESC
"""
df_d = fetch_dataframe(SQL_D, conn=conn)
print(df_d.to_string(index=False))

atr_tot = df_d["ATR"].sum()
cal_rate_july = (df_d["CALIBRATED_RATE"] * df_d["ATR"]).sum() / atr_tot
raw_rate_july = (df_d["RAW_ML_RATE"] * df_d["ATR"]).sum() / atr_tot
print(f"\n  Portfolio CALIBRATED rate (July) : {cal_rate_july:.1f}%  ← what 'ML Forecast %' is based on")
print(f"  Portfolio RAW ML rate    (July) : {raw_rate_july:.1f}%  ← intentionally uncalibrated, not displayed")
try:
    print(f"  + netting ({live_netting:.1f}pp)              → app 'ML Forecast %' ≈ {cal_rate_july + live_netting:.1f}%")
except NameError:
    pass

# ═══════════════════════════════════════════════════════════════════════════════
# E — Manual overrides for July
#     Look at RENEWAL_FORECAST_V5_USER_INPUTS — these are the human inputs
# ═══════════════════════════════════════════════════════════════════════════════
hdr("E — July 2026 Manual Overrides")

SQL_E_COLS = """
SELECT COLUMN_NAME
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'DBO'
  AND TABLE_NAME = 'RENEWAL_FORECAST_V5_USER_INPUTS'
ORDER BY ORDINAL_POSITION
"""
try:
    df_cols = fetch_dataframe(SQL_E_COLS, conn=conn)
    print(f"  RENEWAL_FORECAST_V5_USER_INPUTS columns: {df_cols['COLUMN_NAME'].tolist()}")

    SQL_E = """
    SELECT *
    FROM STREAMLIT_APPS.DBO.RENEWAL_FORECAST_V5_USER_INPUTS
    WHERE DATE_TRUNC('MONTH', RENEWAL_DATE) = '2026-07-01'
    LIMIT 20
    """
    df_e = fetch_dataframe(SQL_E, conn=conn)
    if df_e.empty:
        print("\n  No manual overrides found for July 2026.")
        print("  → The 74%→70% gap is NOT from manual overrides in the inputs table.")
        print("     Check: is the user entering values through a different UI path?")
    else:
        print(f"\n  {len(df_e)} override rows found for July 2026:")
        print(df_e.head(20).to_string(index=False))
except Exception as e:
    print(f"  Could not inspect user inputs table: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# F — Per-segment calibrated bias (last 6 months)
#     Using FINANCE_FORECAST (correct column), not ML_FORECAST
# ═══════════════════════════════════════════════════════════════════════════════
hdr("F — Per-Segment Calibrated Bias (last 6 matured months)")

SQL_F = """
SELECT
    d.SEGMENT,
    COUNT(DISTINCT DATE_TRUNC('MONTH', d.RENEWAL_DATE)) AS N_MONTHS,
    COUNT(DISTINCT d.CONTRACT_ID)                       AS N_CONTRACTS,
    SUM(d.ATR)                                          AS ATR,
    SUM(d.FINANCE_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100   AS CALIBRATED_RATE,
    SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100
                                                        AS ACTUAL_RATE,
    SUM(d.FINANCE_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100
        - SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100
                                                        AS CALIBRATED_BIAS_PP
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
WHERE d.IS_MATURED_MONTH = TRUE
  AND d.ATR > 0
  AND d.ACTUAL_RETAINED_ARR IS NOT NULL
  AND DATE_TRUNC('MONTH', d.RENEWAL_DATE) >= ADD_MONTHS(CURRENT_DATE(), -6)
GROUP BY 1
ORDER BY ABS(CALIBRATED_BIAS_PP) DESC
"""
df_f = fetch_dataframe(SQL_F, conn=conn)
print(df_f.to_string(index=False))

atr_f = df_f["ATR"].sum()
wtd_bias = (df_f["CALIBRATED_BIAS_PP"] * df_f["ATR"]).sum() / atr_f if atr_f > 0 else 0
print(f"\n  ATR-weighted portfolio calibrated bias (last 6 months): {wtd_bias:+.2f}pp")

needs_refresh = df_f[df_f["CALIBRATED_BIAS_PP"].abs() >= 2.0]
if needs_refresh.empty:
    print("  ✅ All segments within ±2pp — calibration is working correctly.")
else:
    print(f"  ⚠️  Segments with |calibrated bias| ≥ 2pp:")
    for _, r in needs_refresh.iterrows():
        direction = "over-predicting" if r["CALIBRATED_BIAS_PP"] > 0 else "under-predicting"
        print(f"     {r['SEGMENT']:<22} calibrated={r['CALIBRATED_RATE']:.1f}%  actual={r['ACTUAL_RATE']:.1f}%  bias={r['CALIBRATED_BIAS_PP']:+.1f}pp  ({direction})")

# ═══════════════════════════════════════════════════════════════════════════════
# G — End-to-end pipeline check for July
#     Model → netting → vs manual override
# ═══════════════════════════════════════════════════════════════════════════════
hdr("G — July Pipeline End-to-End Summary")
try:
    print(f"""
  Layer                              Value
  ─────────────────────────────────────────────────────────────
  1. Raw LightGBM (ML_FORECAST/ATR)  {raw_rate_july:.1f}%   ← uncalibrated, NOT displayed
  2. After Stage 1+2 calibration     {cal_rate_july:.1f}%   ← FINANCE_FORECAST/ATR
  3. After netting (~{live_netting:.1f}pp)          {cal_rate_july + live_netting:.1f}%   ← app 'ML Forecast %'
  4. Manual override (user input)    70.0%  ← user says 70%

  Gap (calibrated model vs override): {cal_rate_july + live_netting - 70.0:+.1f}pp

  If calibrated bias (Step F) ≈ 0pp → model is correct; gap is human judgment.
  If calibrated bias >> 0pp         → model still over-predicts; calibration refresh needed.
""")
except:
    print("  Could not compute end-to-end summary.")
