"""
audit_july_forecast.py
======================
Answers five specific questions about the July 74% vs 70% (manual override) gap:

  A. Recent actuals accuracy (Jan-May 2026) — is the model drifting?
  B. Calibration policy: what offsets are CURRENTLY applied per segment?
  C. Netting calculation: what is the live netting pp and is it double-counting?
  D. July forward decomposition: model raw → calibration → netting → final, by segment
  E. Manual overrides for July: what are humans changing, and in which segment?
     If humans are systematically lowering, it's a calibration signal, not judgment.

Usage:
    cd c:\\Users\\Nate.Fold\\projects
    .venv\\Scripts\\python.exe TEMPLATES\\Python\\audit_july_forecast.py
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
pd.set_option("display.max_rows", 60)

SEP = "=" * 80
def hdr(t: str) -> None:
    print(f"\n{SEP}\n{t}\n{SEP}")

conn = get_snowflake_connection()

# ═══════════════════════════════════════════════════════════════════════════════
# A — Recent portfolio accuracy (Jan–May 2026)
#     Source: V5_SANDBOX_APP_CONTRACT_DETAIL (same as app)
#     Shows portfolio-level model rate vs Finance actual rate by month.
# ═══════════════════════════════════════════════════════════════════════════════
hdr("A — Recent Portfolio Accuracy (Jan–May 2026, matured months)")

SQL_A = """
SELECT
    DATE_TRUNC('MONTH', d.RENEWAL_DATE)::DATE                   AS RENEWAL_MONTH,
    SUM(d.ATR)                                                   AS ATR,
    SUM(d.ML_FORECAST)                                           AS ML_FORECAST_SUM,
    SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0))                      AS ACTUAL_SUM,
    SUM(d.ML_FORECAST)         / NULLIF(SUM(d.ATR), 0) * 100    AS MODEL_RATE_PCT,
    SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100  AS ACTUAL_RATE_PCT,
    SUM(d.ML_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100
        - SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100
                                                                 AS MODEL_BIAS_PP
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
print(df_a[["RENEWAL_MONTH","ATR","MODEL_RATE_PCT","ACTUAL_RATE_PCT","MODEL_BIAS_PP"]].to_string(index=False))
print(f"\n  Avg MODEL bias (Jan-May 2026): {df_a['MODEL_BIAS_PP'].mean():.2f}pp")

# ═══════════════════════════════════════════════════════════════════════════════
# B — Current calibration policy (what offsets are active today?)
# ═══════════════════════════════════════════════════════════════════════════════
hdr("B — Active Calibration Policy (V5_CALIBRATION_POLICY)")

SQL_B = """
SELECT POLICY_ID, SEGMENT, OFFSET_PP, EFFECTIVE_DATE, EXPIRY_DATE, NOTES
FROM STREAMLIT_APPS.DBO.V5_CALIBRATION_POLICY
WHERE EFFECTIVE_DATE <= CURRENT_DATE()
  AND (EXPIRY_DATE IS NULL OR EXPIRY_DATE > CURRENT_DATE())
ORDER BY SEGMENT
"""
try:
    df_b = fetch_dataframe(SQL_B, conn=conn)
    print(df_b.to_string(index=False))
    if df_b.empty:
        print("  *** No active calibration policy found — model is running on RAW output ***")
except Exception as e:
    print(f"  Could not query V5_CALIBRATION_POLICY: {e}")
    print("  (Table may not exist or policy was never seeded)")

# ═══════════════════════════════════════════════════════════════════════════════
# C — Netting calculation: what is the live netting, and does it double-count?
#     Netting = CONTRACT_ACTUAL_PCT − ACTUAL_PCT  (contract grain − portfolio grain)
#     Expected: 1-3pp structural gap from contract vs portfolio allocation math.
#     If netting is large (>3pp), and the model is ALREADY calibrated, it
#     means netting is double-counting on top of a calibrated model.
# ═══════════════════════════════════════════════════════════════════════════════
hdr("C — Live Netting Calculation (last 12 matured months)")

SQL_C = """
SELECT
    DATE_TRUNC('MONTH', d.RENEWAL_DATE)::DATE  AS RENEWAL_MONTH,
    -- Portfolio-grain rate (sum/sum)
    SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100  AS PORTFOLIO_ACTUAL_PCT,
    -- Contract-grain rate: avg of per-contract rates (Finance board definition)
    AVG(
        CASE WHEN d.CONTRACT_ATR > 0
             THEN COALESCE(d.ACTUAL_RETAINED_ARR, 0) / d.CONTRACT_ATR * 100
             ELSE NULL END
    )                                                                         AS CONTRACT_ACTUAL_PCT_AVG,
    -- Gap = netting
    AVG(
        CASE WHEN d.CONTRACT_ATR > 0
             THEN COALESCE(d.ACTUAL_RETAINED_ARR, 0) / d.CONTRACT_ATR * 100
             ELSE NULL END
    ) - SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100
                                                                              AS NETTING_PP_RAW,
    -- From snapshot table (what the app is actually using)
    MAX(s.NETTING_PP)  AS SNAPSHOT_NETTING_PP,
    COUNT(DISTINCT d.CONTRACT_ID)  AS N_CONTRACTS
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
LEFT JOIN STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS s
    ON DATE_TRUNC('MONTH', d.RENEWAL_DATE)::DATE = s.RENEWAL_MONTH
    AND s.SNAPSHOT_DATE = (
        SELECT MAX(s2.SNAPSHOT_DATE)
        FROM STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS s2
        WHERE s2.RENEWAL_MONTH = DATE_TRUNC('MONTH', d.RENEWAL_DATE)::DATE
    )
WHERE d.IS_MATURED_MONTH = TRUE
  AND d.ATR > 0
  AND d.ACTUAL_RETAINED_ARR IS NOT NULL
  AND d.CONTRACT_ATR > 0
  AND DATE_TRUNC('MONTH', d.RENEWAL_DATE) >= ADD_MONTHS(CURRENT_DATE(), -12)
GROUP BY 1
ORDER BY 1
"""
try:
    df_c = fetch_dataframe(SQL_C, conn=conn)
    df_c["RENEWAL_MONTH"] = pd.to_datetime(df_c["RENEWAL_MONTH"]).dt.strftime("%Y-%m")
    print(df_c.to_string(index=False))

    netting_vals = pd.to_numeric(df_c["NETTING_PP_RAW"], errors="coerce").dropna()
    trimmed = netting_vals.sort_values().iloc[2:-2] if len(netting_vals) > 4 else netting_vals
    live_netting = float(trimmed.mean()) if len(trimmed) > 0 else 1.6
    print(f"\n  Live trimmed-mean netting (app value) : {live_netting:.2f}pp")
    print(f"  If model EFF_BIAS_PP (from A) > 0 AND netting > 0 → netting inflates an already-over-predicting model")
except Exception as e:
    print(f"  Could not compute netting: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# D — July 2026 forward decomposition by segment
#     Shows EXACTLY where the 74% comes from, layer by layer.
# ═══════════════════════════════════════════════════════════════════════════════
hdr("D — July 2026 Forward Decomposition by Segment")

SQL_D = """
SELECT
    d.SEGMENT,
    COUNT(DISTINCT d.CONTRACT_ID)                                              AS N_CONTRACTS,
    SUM(d.ATR)                                                                 AS ATR,
    SUM(d.CHURN_PCT * d.ATR) / NULLIF(SUM(d.ATR), 0)                          AS AVG_CHURN_PCT,
    SUM(d.ML_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100                          AS MODEL_RATE_PCT,
    SUM(d.OPEN_OPP_CARR)                                                       AS OPEN_OPP
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
WHERE DATE_TRUNC('MONTH', d.RENEWAL_DATE) = '2026-07-01'
  AND d.ATR > 0
GROUP BY 1
ORDER BY ATR DESC
"""
try:
    df_d = fetch_dataframe(SQL_D, conn=conn)
    print(df_d.to_string(index=False))
    atr_tot    = df_d["ATR"].sum()
    model_rate = (df_d["MODEL_RATE_PCT"] * df_d["ATR"]).sum() / atr_tot
    print(f"\n  Portfolio MODEL_RATE : {model_rate:.1f}%  (before netting)")
    try:
        print(f"  + netting ({live_netting:.1f}pp)  → displayed 'ML Forecast %' ≈ {model_rate + live_netting:.1f}%")
    except NameError:
        pass
except Exception as e:
    print(f"  Could not decompose July by segment: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# E — Manual overrides for July: what are humans adjusting?
#     Key question: are overrides clustered in one segment (systematic)?
#     Or spread across specific large contracts (judgment)?
# ═══════════════════════════════════════════════════════════════════════════════
hdr("E — July 2026 Manual Overrides (RENEWAL_FORECAST_V5_USER_INPUTS)")

SQL_E = """
SELECT
    u.SEGMENT,
    COUNT(*) AS N_OVERRIDES,
    SUM(u.ATR)                                                AS OVERRIDE_ATR,
    AVG(u.ML_FORECAST / NULLIF(u.ATR, 0) * 100)              AS AVG_MODEL_RATE_AT_OVERRIDE,
    AVG(u.MANUAL_FORECAST / NULLIF(u.ATR, 0) * 100)          AS AVG_MANUAL_RATE,
    AVG(
        (u.MANUAL_FORECAST - u.ML_FORECAST) / NULLIF(u.ATR, 0) * 100
    )                                                         AS AVG_ADJUSTMENT_PP,
    SUM(u.MANUAL_FORECAST - u.ML_FORECAST)                   AS TOTAL_OVERRIDE_DOLLARS
FROM STREAMLIT_APPS.DBO.RENEWAL_FORECAST_V5_USER_INPUTS u
WHERE DATE_TRUNC('MONTH', u.RENEWAL_DATE) = '2026-07-01'
  AND u.ATR > 0
GROUP BY 1
ORDER BY ABS(SUM(u.MANUAL_FORECAST - u.ML_FORECAST)) DESC
"""
try:
    df_e = fetch_dataframe(SQL_E, conn=conn)
    if df_e.empty:
        print("  No manual overrides found in RENEWAL_FORECAST_V5_USER_INPUTS for July 2026.")
        print("  → The 74%→70% gap must be coming from somewhere else (calibration or netting).")
    else:
        print(df_e.to_string(index=False))
        total_adj = df_e["TOTAL_OVERRIDE_DOLLARS"].sum()
        print(f"\n  Total July override dollars: ${total_adj:,.0f}")
        print(f"  Avg adjustment direction: {'DOWN (corroborates calibration over-prediction)' if total_adj < 0 else 'UP'}")
except Exception as e:
    print(f"  Could not query user inputs: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# F — Calibration proposal (what SP_V5_PROPOSE_QUARTERLY_CALIBRATION would say)
#     Compute per-segment bias on last 6 matured months. If bias > 2pp, need refresh.
# ═══════════════════════════════════════════════════════════════════════════════
hdr("F — Per-Segment Bias (last 6 matured months) — Calibration Proposal")

SQL_F = """
SELECT
    d.SEGMENT,
    COUNT(DISTINCT DATE_TRUNC('MONTH', d.RENEWAL_DATE))  AS N_MONTHS,
    COUNT(DISTINCT d.CONTRACT_ID)                        AS N_CONTRACTS,
    SUM(d.ATR)                                           AS ATR,
    SUM(d.ML_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100    AS MODEL_RATE,
    SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100
                                                         AS ACTUAL_RATE,
    SUM(d.ML_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100
        - SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100
                                                         AS BIAS_PP,
    -- What offset would fix this bias?
    -(SUM(d.ML_FORECAST) / NULLIF(SUM(d.ATR), 0) * 100
       - SUM(COALESCE(d.ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(d.ATR), 0) * 100)
                                                         AS PROPOSED_OFFSET_PP
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL d
WHERE d.IS_MATURED_MONTH = TRUE
  AND d.ATR > 0
  AND d.ACTUAL_RETAINED_ARR IS NOT NULL
  AND DATE_TRUNC('MONTH', d.RENEWAL_DATE) >= ADD_MONTHS(CURRENT_DATE(), -6)
GROUP BY 1
ORDER BY ABS(BIAS_PP) DESC
"""
df_f = fetch_dataframe(SQL_F, conn=conn)
print(df_f.to_string(index=False))

# Portfolio summary
atr_f = df_f["ATR"].sum()
wtd_bias = (df_f["BIAS_PP"] * df_f["ATR"]).sum() / atr_f if atr_f > 0 else 0
print(f"\n  ATR-weighted portfolio bias (last 6 months): {wtd_bias:.2f}pp")
needs_refresh = df_f[df_f["BIAS_PP"].abs() >= 2.0]
if needs_refresh.empty:
    print("  ✅ All segments within ±2pp — calibration is current.")
else:
    print(f"  ⚠️  Segments needing calibration refresh (|bias| ≥ 2pp):")
    for _, r in needs_refresh.iterrows():
        direction = "over-predicting" if r["BIAS_PP"] > 0 else "under-predicting"
        print(f"     {r['SEGMENT']:<22} bias={r['BIAS_PP']:+.1f}pp  ({direction})  → proposed offset: {r['PROPOSED_OFFSET_PP']:+.1f}pp")

# ═══════════════════════════════════════════════════════════════════════════════
# G — Summary verdict
# ═══════════════════════════════════════════════════════════════════════════════
hdr("G — SUMMARY VERDICT")
print("""
Root cause of July 74% vs 70% manual override gap — ranked by likelihood:

  [Check A] If MODEL_BIAS_PP on recent matured months > 2pp:
    → Calibration is stale. SP_V5_PROPOSE_QUARTERLY_CALIBRATION needed.
    → Human overrides are correcting a systematic model error, not adding judgment.

  [Check C] If live netting pp > 2pp AND model is already over-predicting:
    → Netting is DOUBLE-COUNTING. The model over-predicts, and netting adds MORE on top.
    → Fix: either reduce netting window, or fix calibration first, then re-measure netting.

  [Check E] If overrides are clustered in one segment (e.g. Strategic):
    → Human insiders know something structural (e.g. specific large account at risk).
    → This is legitimate judgment — do NOT try to calibrate it away.

  [Check F] PROPOSED_OFFSET_PP per segment = what the calibration refresh would apply.
    → If proposed offsets are > 2pp, run SP_V5_PROPOSE_QUARTERLY_CALIBRATION() in Snowsight.
""")
