"""
may_forecast_accuracy.py
========================
Answers: "How accurate is our renewal forecasting for May 2026?"

Output:
  1. Portfolio-level accuracy (current dev pipeline model run vs actuals)
  2. Frozen point-in-time snapshot accuracy (model vs manual-adjusted vs actuals)
  3. Manual input impact summary (how much humans moved the needle for May)
  4. Per-segment breakout
  5. Contract-level detail (optional, top over/under by dollar error)

Data sources (dev pipeline — same tables as Development_Forecast_App_V1.py):
  - STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL  ← current model run
  - STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS        ← frozen snapshot (shared)
  - STREAMLIT_APPS.DBO.RENEWAL_FORECAST_V5_USER_INPUTS  ← manual overrides

Usage:
    cd c:\\Users\\Nate.Fold\\projects
    .venv\\Scripts\\python.exe TEMPLATES\\Python\\may_forecast_accuracy.py
"""
from __future__ import annotations
import sys
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, r"c:\Users\Nate.Fold\projects\TEMPLATES\Python")

import pandas as pd
import numpy as np
from connection import get_snowflake_connection, fetch_dataframe

pd.set_option("display.float_format", "{:,.2f}".format)
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_rows", 60)

SEP  = "=" * 80
SEP2 = "-" * 80

def hdr(t: str) -> None:
    print(f"\n{SEP}\n{t}\n{SEP}")

def sub(t: str) -> None:
    print(f"\n{SEP2}\n{t}\n{SEP2}")

RENEWAL_MONTH   = "2026-05-01"
T_SANDBOX       = "STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL"
T_SNAPSHOTS     = "STREAMLIT_APPS.DBO.V5_APP_FORECAST_SNAPSHOTS"
T_INPUTS        = "STREAMLIT_APPS.DBO.RENEWAL_FORECAST_V5_USER_INPUTS"

conn = get_snowflake_connection()

# ═══════════════════════════════════════════════════════════════════════════════
# 1. PORTFOLIO-LEVEL ACCURACY — current dev pipeline model run
#    Source: V5_SANDBOX_APP_CONTRACT_DETAIL (most recent model run for May)
#    IS_MATURED_MONTH = TRUE ensures May is fully settled / actuals available
# ═══════════════════════════════════════════════════════════════════════════════
hdr("1  PORTFOLIO ACCURACY — Current Dev Pipeline (May 2026)")

df_portfolio = fetch_dataframe(f"""
    SELECT
        COUNT(DISTINCT CONTRACT_ID)                                     AS N_CONTRACTS,
        ROUND(SUM(ATR) / 1e6, 3)                                        AS ATR_M,
        ROUND(SUM(ML_FORECAST) / 1e6, 3)                                AS ML_FORECAST_M,
        ROUND(SUM(ACTUAL_RETAINED_ARR) / 1e6, 3)                        AS ACTUAL_M,
        -- Model vs Actual
        ROUND(SUM(ML_FORECAST)         / NULLIF(SUM(ATR), 0) * 100, 2) AS MODEL_RATE_PCT,
        ROUND(SUM(ACTUAL_RETAINED_ARR) / NULLIF(SUM(ATR), 0) * 100, 2) AS ACTUAL_RATE_PCT,
        ROUND(
            SUM(ML_FORECAST) / NULLIF(SUM(ATR), 0) * 100
          - SUM(ACTUAL_RETAINED_ARR) / NULLIF(SUM(ATR), 0) * 100,
        2)                                                               AS MODEL_ERROR_PP,
        -- Model as % of actuals (how close is $1 forecast to $1 actual)
        ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ACTUAL_RETAINED_ARR), 0) * 100, 2) AS MODEL_PCT_OF_ACTUAL
    FROM {T_SANDBOX}
    WHERE RENEWAL_MONTH = '{RENEWAL_MONTH}'
      AND IS_MATURED_MONTH = TRUE
      AND ATR > 0
      AND ACTUAL_RETAINED_ARR IS NOT NULL
""", conn=conn)

print(df_portfolio.T.to_string(header=False))

if not df_portfolio.empty:
    r = df_portfolio.iloc[0]
    err_pp   = float(r.get("MODEL_ERROR_PP", 0) or 0)
    act_pct  = float(r.get("ACTUAL_RATE_PCT", 0) or 0)
    mod_pct  = float(r.get("MODEL_RATE_PCT", 0) or 0)
    pct_of   = float(r.get("MODEL_PCT_OF_ACTUAL", 0) or 0)
    direction = "OVER-forecasted" if err_pp > 0 else "UNDER-forecasted"
    print(f"""
  SUMMARY ANSWER:
    May 2026 actual renewal rate  : {act_pct:.1f}%
    Model forecast rate (ML)      : {mod_pct:.1f}%
    Error                         : {err_pp:+.2f} pp  ({direction})
    Model forecast as % of actual : {pct_of:.1f}%   (100% = perfect)
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. FROZEN SNAPSHOT ACCURACY — what was forecast at month-close vs actuals
#    Source: V5_APP_FORECAST_SNAPSHOTS (closest snapshot on/before 2026-05-31)
# ═══════════════════════════════════════════════════════════════════════════════
hdr("2  FROZEN SNAPSHOT ACCURACY (Point-in-Time May 2026)")

df_snap = fetch_dataframe(f"""
    SELECT
        SNAPSHOT_DATE,
        RENEWAL_MONTH,
        CONTRACTS,
        ROUND(ATR / 1e6, 3)                           AS ATR_M,
        ROUND(MODEL_FORECAST / 1e6, 3)                AS MODEL_FORECAST_M,
        ROUND(MANUAL_ADJUSTED / 1e6, 3)               AS MANUAL_ADJ_M,
        ROUND(ACTUAL / 1e6, 3)                         AS ACTUAL_M,
        ROUND(MODEL_RATE_PCT, 2)                       AS MODEL_RATE_PCT,
        ROUND(MANUAL_ADJUSTED_PCT, 2)                  AS MANUAL_ADJ_PCT,
        -- derive actual rate from stored dollars
        ROUND(ACTUAL / NULLIF(ATR, 0) * 100, 2)        AS ACTUAL_RATE_PCT,
        -- errors
        ROUND(MODEL_RATE_PCT    - ACTUAL / NULLIF(ATR, 0) * 100, 2)   AS MODEL_ERROR_PP,
        ROUND(MANUAL_ADJUSTED_PCT - ACTUAL / NULLIF(ATR, 0) * 100, 2) AS MANUAL_ERROR_PP,
        N_MANUAL_INPUTS,
        RUN_ID
    FROM {T_SNAPSHOTS}
    WHERE RENEWAL_MONTH = '{RENEWAL_MONTH}'
    ORDER BY SNAPSHOT_DATE DESC
    LIMIT 5
""", conn=conn)

if df_snap.empty:
    print("  No snapshots found for May 2026 — snapshot proc may not have run yet.")
else:
    print(df_snap.T.to_string(header=False))
    latest = df_snap.iloc[0]
    mod_err  = float(latest.get("MODEL_ERROR_PP", 0) or 0)
    man_err  = float(latest.get("MANUAL_ERROR_PP", 0) or 0)
    n_manual = int(latest.get("N_MANUAL_INPUTS", 0) or 0)
    print(f"""
  SNAPSHOT SUMMARY (latest snapshot):
    Snapshot taken               : {latest.get('SNAPSHOT_DATE')}
    Model error (pp)             : {mod_err:+.2f} pp
    Manual-adjusted error (pp)   : {man_err:+.2f} pp
    Manual improvement           : {mod_err - man_err:+.2f} pp  (positive = manual helped)
    # manual overrides           : {n_manual:,}
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. MANUAL INPUT IMPACT — what did humans change for May
#    Source: RENEWAL_FORECAST_V5_USER_INPUTS
# ═══════════════════════════════════════════════════════════════════════════════
hdr("3  MANUAL INPUT IMPACT — May 2026 Overrides")

df_inputs = fetch_dataframe(f"""
    SELECT
        i.CONTRACT_ID,
        i.PRODUCT_GROUP,
        i.RENEWAL_FORECAST       AS MANUAL_FORECAST,
        i.STATUS,
        i.EXEC_HELP,
        i.REASON_CODE,
        i.NOTES,
        i.UPDATED_AT,
        d.ATR,
        d.ML_FORECAST,
        d.ACTUAL_RETAINED_ARR    AS ACTUAL,
        -- deltas
        (i.RENEWAL_FORECAST - d.ML_FORECAST)        AS MANUAL_DELTA,
        (i.RENEWAL_FORECAST - d.ACTUAL_RETAINED_ARR) AS MANUAL_VS_ACTUAL_DELTA
    FROM {T_INPUTS} i
    LEFT JOIN {T_SANDBOX} d
      ON  d.CONTRACT_ID    = i.CONTRACT_ID
      AND d.PRODUCT_GROUP  = i.PRODUCT_GROUP
      AND d.RENEWAL_MONTH  = '{RENEWAL_MONTH}'
    WHERE COALESCE(i.RENEWAL_MONTH, '{RENEWAL_MONTH}') = '{RENEWAL_MONTH}'
""", conn=conn)

if df_inputs.empty:
    print("  No manual overrides found for May 2026.")
else:
    n_total      = len(df_inputs)
    n_up         = (df_inputs["MANUAL_DELTA"] > 0).sum()
    n_down       = (df_inputs["MANUAL_DELTA"] < 0).sum()
    total_delta  = df_inputs["MANUAL_DELTA"].sum()
    total_atr    = df_inputs["ATR"].sum()
    avg_delta_pp = total_delta / total_atr * 100 if total_atr > 0 else 0

    # Was manual adjustment closer to actual than model?
    covered = df_inputs.dropna(subset=["ACTUAL", "ML_FORECAST", "MANUAL_FORECAST"])
    if not covered.empty:
        model_mse  = ((covered["ML_FORECAST"]     - covered["ACTUAL"]) ** 2).mean()
        manual_mse = ((covered["MANUAL_FORECAST"]  - covered["ACTUAL"]) ** 2).mean()
        manual_mae = (covered["MANUAL_FORECAST"] - covered["ACTUAL"]).abs().mean()
        model_mae  = (covered["ML_FORECAST"]     - covered["ACTUAL"]).abs().mean()

    print(f"""
  Contracts with manual overrides    : {n_total:,}
  Moved UP   (vs model)              : {n_up:,}
  Moved DOWN (vs model)              : {n_down:,}
  Net manual adjustment ($M)         : {total_delta/1e6:+.3f}M
  Net manual adjustment (pp of ATR)  : {avg_delta_pp:+.2f} pp
""")
    if not covered.empty:
        print(f"  On overridden contracts only:")
        print(f"    Model MAE vs actual            : ${model_mae:,.0f}")
        print(f"    Manual MAE vs actual           : ${manual_mae:,.0f}")
        improvement = model_mae - manual_mae
        print(f"    Manual improvement over model  : ${improvement:,.0f} avg/contract ({'helped' if improvement>0 else 'hurt'})")

    sub("Override detail (all May overrides)")
    display_cols = ["CONTRACT_ID","PRODUCT_GROUP","STATUS","MANUAL_FORECAST",
                    "ML_FORECAST","ACTUAL","MANUAL_DELTA","MANUAL_VS_ACTUAL_DELTA"]
    available = [c for c in display_cols if c in df_inputs.columns]
    print(df_inputs[available].sort_values("MANUAL_DELTA", ascending=False).to_string(index=False))


# ═══════════════════════════════════════════════════════════════════════════════
# 4. PER-SEGMENT BREAKOUT — accuracy by segment for May
# ═══════════════════════════════════════════════════════════════════════════════
hdr("4  PER-SEGMENT ACCURACY — May 2026")

df_seg = fetch_dataframe(f"""
    SELECT
        SEGMENT,
        COUNT(DISTINCT CONTRACT_ID)                                     AS N_CONTRACTS,
        ROUND(SUM(ATR) / 1e3, 1)                                        AS ATR_K,
        ROUND(SUM(ML_FORECAST)         / NULLIF(SUM(ATR), 0) * 100, 2) AS MODEL_RATE_PCT,
        ROUND(SUM(ACTUAL_RETAINED_ARR) / NULLIF(SUM(ATR), 0) * 100, 2) AS ACTUAL_RATE_PCT,
        ROUND(
            SUM(ML_FORECAST) / NULLIF(SUM(ATR), 0) * 100
          - SUM(ACTUAL_RETAINED_ARR) / NULLIF(SUM(ATR), 0) * 100,
        2)                                                               AS ERROR_PP,
        ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ACTUAL_RETAINED_ARR), 0) * 100, 2) AS MODEL_PCT_OF_ACTUAL
    FROM {T_SANDBOX}
    WHERE RENEWAL_MONTH = '{RENEWAL_MONTH}'
      AND IS_MATURED_MONTH = TRUE
      AND ATR > 0
      AND ACTUAL_RETAINED_ARR IS NOT NULL
    GROUP BY 1
    ORDER BY ATR_K DESC
""", conn=conn)

if df_seg.empty:
    print("  No segment data found.")
else:
    print(df_seg.to_string(index=False))


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CONTRACT-LEVEL DETAIL — top 20 over/under by absolute dollar error
#    Useful if they want a contract-level Excel breakout
# ═══════════════════════════════════════════════════════════════════════════════
hdr("5  CONTRACT-LEVEL DETAIL — Top 20 Largest Errors (May 2026)")

df_contracts = fetch_dataframe(f"""
    SELECT
        CONTRACT_ID,
        PRODUCT_GROUP,
        SEGMENT,
        ROUND(ATR / 1e3, 1)                                               AS ATR_K,
        ROUND(ML_FORECAST / 1e3, 1)                                       AS MODEL_K,
        ROUND(ACTUAL_RETAINED_ARR / 1e3, 1)                               AS ACTUAL_K,
        ROUND((ML_FORECAST - ACTUAL_RETAINED_ARR) / 1e3, 1)              AS ERROR_K,
        ROUND(ML_FORECAST / NULLIF(ATR, 0) * 100, 1)                      AS MODEL_RATE_PCT,
        ROUND(ACTUAL_RETAINED_ARR / NULLIF(ATR, 0) * 100, 1)              AS ACTUAL_RATE_PCT
    FROM {T_SANDBOX}
    WHERE RENEWAL_MONTH = '{RENEWAL_MONTH}'
      AND IS_MATURED_MONTH = TRUE
      AND ATR > 0
      AND ACTUAL_RETAINED_ARR IS NOT NULL
    ORDER BY ABS(ML_FORECAST - ACTUAL_RETAINED_ARR) DESC
    LIMIT 20
""", conn=conn)

if df_contracts.empty:
    print("  No contract data found.")
else:
    print(df_contracts.to_string(index=False))

    sub("HEADLINE NUMBERS (copy-paste ready)")
    # Re-fetch clean headline for easy sharing
    if not df_portfolio.empty:
        r = df_portfolio.iloc[0]
        n   = int(r.get("N_CONTRACTS", 0) or 0)
        atr = float(r.get("ATR_M", 0) or 0)
        act = float(r.get("ACTUAL_RATE_PCT", 0) or 0)
        mod = float(r.get("MODEL_RATE_PCT", 0) or 0)
        err = float(r.get("MODEL_ERROR_PP", 0) or 0)
        pof = float(r.get("MODEL_PCT_OF_ACTUAL", 0) or 0)
        print(f"""
  May 2026 Renewal Forecast Accuracy
  -----------------------------------
  Contracts evaluated : {n:,}
  Total ATR           : ${atr:.1f}M
  Actual renewal rate : {act:.1f}%
  Model forecast rate : {mod:.1f}%
  Error               : {err:+.1f} pp  (model {'over' if err>0 else 'under'}-forecast actuals)
  Forecast accuracy   : {pof:.1f}% of actual dollars (100% = perfect)
""")

conn.close()
print(f"\n{SEP}")
print("Done.")
