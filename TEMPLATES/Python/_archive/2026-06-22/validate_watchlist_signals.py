"""
Watchlist signal validation — answers two questions:

  Q1. Are the flagged Jul-Sep accounts being flagged for the SAME reasons
      that historically drove churn? (feature profile comparison)

  Q2. Given the bimodal churn distribution, what do CHURN_PCT buckets
      actually look like in historical outcomes?
      (calibration plot data — answers the magnitude reliability question)

Output: console + watchlist_signal_audit.csv
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

OUT_CSV = Path(__file__).parent / "watchlist_signal_audit.csv"

conn = get_snowflake_connection(); cur = conn.cursor()
for s in ["USE ROLE STREAMLIT_USER","USE WAREHOUSE REPORTING_WH",
          "USE DATABASE STREAMLIT_APPS","USE SCHEMA DBO"]:
    cur.execute(s)
print("Connected ✓\n")


# ─────────────────────────────────────────────────────────────────────────────
# 1. BIMODAL CALIBRATION
#    For closed months, bucket contracts by CHURN_PCT and show the actual
#    outcome distribution (% that fully churned vs fully renewed vs partial).
#    This is the magnitude reliability question.
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("1. BIMODAL CALIBRATION — actual outcome distribution by churn-prob bucket")
print("   (closed months only, IS_MATURE=TRUE)")
print("=" * 70)

cur.execute("""
    SELECT
        CASE
            WHEN CHURN_PCT < 20  THEN '0–20%'
            WHEN CHURN_PCT < 40  THEN '20–40%'
            WHEN CHURN_PCT < 60  THEN '40–60%'
            WHEN CHURN_PCT < 80  THEN '60–80%'
            ELSE                      '80–100%'
        END                                                       AS CHURN_BUCKET,
        COUNT(*)                                                  AS N_CONTRACTS,
        -- Actual outcome distribution within bucket
        ROUND(AVG(COALESCE(ACTUAL_RETAINED_ARR,0) / NULLIF(ATR,0) * 100), 1)
                                                                  AS AVG_ACTUAL_RETENTION_PCT,
        ROUND(PERCENTILE_CONT(0.10) WITHIN GROUP
              (ORDER BY COALESCE(ACTUAL_RETAINED_ARR,0) / NULLIF(ATR,0) * 100), 1)
                                                                  AS P10_ACTUAL_RETENTION,
        ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP
              (ORDER BY COALESCE(ACTUAL_RETAINED_ARR,0) / NULLIF(ATR,0) * 100), 1)
                                                                  AS P50_ACTUAL_RETENTION,
        ROUND(PERCENTILE_CONT(0.90) WITHIN GROUP
              (ORDER BY COALESCE(ACTUAL_RETAINED_ARR,0) / NULLIF(ATR,0) * 100), 1)
                                                                  AS P90_ACTUAL_RETENTION,
        -- How many fully churned (<5%), fully renewed (>95%), partial (5-95%)
        ROUND(COUNT_IF(COALESCE(ACTUAL_RETAINED_ARR,0)/NULLIF(ATR,0) < 0.05) * 100.0 / COUNT(*), 1)
                                                                  AS PCT_FULLY_CHURNED,
        ROUND(COUNT_IF(COALESCE(ACTUAL_RETAINED_ARR,0)/NULLIF(ATR,0) > 0.95) * 100.0 / COUNT(*), 1)
                                                                  AS PCT_FULLY_RENEWED,
        ROUND(COUNT_IF(COALESCE(ACTUAL_RETAINED_ARR,0)/NULLIF(ATR,0) BETWEEN 0.05 AND 0.95)
              * 100.0 / COUNT(*), 1)                              AS PCT_PARTIAL
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURE = TRUE
      AND IS_MATURED_MONTH = TRUE
      AND COALESCE(ATR, 0) > 0
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    GROUP BY 1
    ORDER BY MIN(CHURN_PCT)
""")
bim_df = pd.DataFrame(cur.fetchall(), columns=[
    "BUCKET","N","AVG_RETENTION","P10","P50","P90",
    "PCT_FULLY_CHURNED","PCT_FULLY_RENEWED","PCT_PARTIAL"
])
print(bim_df.to_string(index=False))
print("""
INTERPRETATION:
  P10/P90 spread shows bimodality. If P10~0 and P90~100, the bucket is
  bimodal (model is right directionally but contract-level magnitude is
  unreliable). Mean is only reliable at portfolio grain, not contract grain.
""")


# ─────────────────────────────────────────────────────────────────────────────
# 2. TOP SHAP DRIVERS — what is actually triggering the risk flags?
#    Global feature importance + top drivers for the watchlist specifically.
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("2. GLOBAL FEATURE IMPORTANCE (what does the model use most?)")
print("=" * 70)

cur.execute("""
    SELECT FEATURE_LABEL, ROUND(MEAN_ABS_SHAP, 4) AS MEAN_ABS_SHAP,
           ROUND(MEAN_SIGNED_SHAP, 4) AS SIGNED,
           N_OBS
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_SHAP_GLOBAL
    WHERE MODEL_TARGET = 'CHURN_PROBABILITY'
    ORDER BY MEAN_ABS_SHAP DESC
    LIMIT 15
""")
global_df = pd.DataFrame(cur.fetchall(), columns=["FEATURE","MEAN_ABS_SHAP","SIGNED","N_OBS"])
# Positive signed = feature pushes toward churn
global_df["DIRECTION"] = global_df["SIGNED"].apply(lambda x: "→ CHURN" if x > 0 else "→ RETAIN")
print(global_df.to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# 3. WATCHLIST SHAP PROFILES
#    For each watchlist contract: top 5 SHAP drivers.
#    Cross-checks whether the flagging reason is a known churn signal.
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("3. WATCHLIST SHAP PROFILES — top drivers per flagged contract")
print("   (Jul–Sep 2026, ranked by loss exposure)")
print("=" * 70)

# Pull top-50 watchlist contract IDs from app table
cur.execute("""
    SELECT CONTRACT_ID, PARTNER, SEGMENT, RENEWAL_MONTH,
           ROUND(AT_RISK_DOLLARS,0) AS LOSS,
           ROUND(CHURN_PCT,1) AS CHURN_PCT,
           CONTRACT_RISK_TIER_RELATIVE AS TIER
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-09-01'
      AND IS_MATURE = FALSE
      AND COALESCE(AT_RISK_DOLLARS,0) > 0
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    ORDER BY AT_RISK_DOLLARS DESC
    LIMIT 50
""")
wl = pd.DataFrame(cur.fetchall(), columns=[
    "CONTRACT_ID","PARTNER","SEGMENT","RENEWAL_MONTH","LOSS","CHURN_PCT","TIER"
])

# Pull SHAP drivers for these contracts (top 5 per contract)
ids_sql = ",".join(f"'{c}'" for c in wl["CONTRACT_ID"].unique())
cur.execute(f"""
    SELECT CONTRACT_ID,
           DRIVER_RANK,
           FEATURE_LABEL,
           TRY_CAST(FEATURE_VALUE AS FLOAT)   AS FEATURE_VALUE,
           ROUND(SHAP_VALUE, 4)            AS SHAP_VALUE,
           DIRECTION
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_SHAP_DRIVERS
    WHERE CONTRACT_ID IN ({ids_sql})
      AND DRIVER_RANK <= 5
      AND MODEL_TARGET = 'CHURN_PROBABILITY'
    ORDER BY CONTRACT_ID, DRIVER_RANK
""")
shap_df = pd.DataFrame(cur.fetchall(), columns=[
    "CONTRACT_ID","RANK","FEATURE","VALUE","SHAP","DIRECTION"
])

# Pivot to wide: top-3 risk drivers per contract as columns
top3 = (
    shap_df[shap_df["DIRECTION"] == "RISK"]
    .sort_values(["CONTRACT_ID","SHAP"], ascending=[True, False])
    .groupby("CONTRACT_ID")
    .head(3)
    .assign(driver_num=lambda df: df.groupby("CONTRACT_ID").cumcount() + 1)
)
top3_wide = top3.pivot(index="CONTRACT_ID", columns="driver_num", values="FEATURE").add_prefix("TOP_RISK_")
top3_wide.columns = [f"RISK_DRIVER_{i}" for i in top3_wide.columns.str.extract(r"(\d+)")[0]]

# Merge back into watchlist
result = wl.merge(top3_wide, on="CONTRACT_ID", how="left")
print(result.to_string(index=False))

# Show the top features driving the HIGH-tier watchlist contracts overall
print("\n--- Most common risk drivers across the watchlist ---")
risk_counts = (
    shap_df[shap_df["DIRECTION"] == "RISK"]
    .merge(wl[["CONTRACT_ID","TIER"]], on="CONTRACT_ID")
    .groupby(["FEATURE","TIER"])["CONTRACT_ID"]
    .count()
    .reset_index()
    .rename(columns={"CONTRACT_ID":"COUNT"})
    .sort_values("COUNT", ascending=False)
    .head(20)
)
print(risk_counts.to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# 4. HISTORICAL VALIDATION
#    For the same top risk features, do they actually predict churn
#    in closed months? Confirms the model is flagging for real reasons.
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("4. HISTORICAL DRIVER VALIDATION")
print("   Do watchlist risk features actually predict churn in closed months?")
print("=" * 70)

# Get top 5 most common risk drivers from watchlist
top_features = risk_counts["FEATURE"].head(5).tolist()
feat_sql = ",".join(f"'{f}'" for f in top_features)

# ─────────────────────────────────────────────────────────────────────────────
# 4. HISTORICAL DRIVER VALIDATION
#    The SHAP table only contains the current forward run.
#    Instead: for each historical run in ML_SANDBOX_V5_PREDICTIONS that has
#    matured actuals, show whether predicted CHURN_PCT accurately separated
#    churners from renewers — bucketed by the same churn-prob ranges.
#    This proves the model was flagging correctly in the past.
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("4. HISTORICAL PREDICTION ACCURACY — did high CHURN_PCT = actual churn?")
print("   (all runs × all mature months in V5_SANDBOX_APP_CONTRACT_DETAIL)")
print("=" * 70)

cur.execute("""
    SELECT
        SEGMENT,
        CASE
            WHEN CHURN_PCT < 20  THEN '0–20%'
            WHEN CHURN_PCT < 40  THEN '20–40%'
            WHEN CHURN_PCT < 60  THEN '40–60%'
            WHEN CHURN_PCT < 80  THEN '60–80%'
            ELSE                      '80–100%'
        END                                                       AS CHURN_BUCKET,
        COUNT(*)                                                  AS N,
        -- Did contract actually churn (lose >5% of ATR)?
        ROUND(COUNT_IF(COALESCE(ACTUAL_RETAINED_ARR,0)/NULLIF(ATR,0) < 0.95)
              * 100.0 / COUNT(*), 1)                              AS PCT_ACTUALLY_CHURNED,
        ROUND(AVG(COALESCE(ACTUAL_RETAINED_ARR,0)/NULLIF(ATR,0)*100), 1)
                                                                  AS AVG_ACTUAL_RETENTION
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURE = TRUE
      AND IS_MATURED_MONTH = TRUE
      AND COALESCE(ATR, 0) > 0
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
      AND CHURN_PCT IS NOT NULL
    GROUP BY SEGMENT, CHURN_BUCKET
    ORDER BY SEGMENT,
             CASE CHURN_BUCKET
                 WHEN '0–20%'   THEN 1 WHEN '20–40%' THEN 2 WHEN '40–60%' THEN 3
                 WHEN '60–80%'  THEN 4 ELSE 5
             END
""")
hist2_df = pd.DataFrame(cur.fetchall(), columns=[
    "SEGMENT","BUCKET","N","PCT_ACTUALLY_CHURNED","AVG_ACTUAL_RETENTION"
])
print(hist2_df.to_string(index=False))
print("""
  A monotonically increasing PCT_ACTUALLY_CHURNED across buckets per segment
  = the model correctly ranks risk. Compare the watchlist contracts' CHURN_PCT
  to these buckets to assess how reliable their flag is.
""")

# Summary: pivot to show each segment's churn rate at 40-60% vs 60-80% vs 80-100%
print("--- Key buckets for the watchlist range (40–100%) ---")
pivot = hist2_df[hist2_df["BUCKET"].isin(["40–60%","60–80%","80–100%"])].pivot(
    index="SEGMENT", columns="BUCKET", values="PCT_ACTUALLY_CHURNED"
)
print(pivot.to_string())

result.to_csv(OUT_CSV, index=False)
print(f"Saved signal audit to: {OUT_CSV}")
conn.close()
print("\nDone.")
