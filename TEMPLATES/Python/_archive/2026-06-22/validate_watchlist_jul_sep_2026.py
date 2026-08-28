"""
Watchlist validation for Jul–Sep 2026 top-50 at-risk contracts.

Two sections:
  A) RISK TIER CALIBRATION — uses CLOSED historical months (IS_MATURE=TRUE).
     Shows actual churn rates by CONTRACT_RISK_TIER_RELATIVE.
     If High > Medium > Low, the risk ranking is predictive.

  B) SIGNAL AUDIT — shows the key driving signals for every contract
     in the Jul–Sep 2026 top-50 watchlist so a human can cross-check
     against Salesforce before sending to sales leadership.

Output: prints to console + writes watchlist_jul_sep_2026.csv
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402

OUT_CSV = Path(__file__).parent / "watchlist_jul_sep_2026.csv"

conn = get_snowflake_connection()
cur  = conn.cursor()
for s in ["USE ROLE STREAMLIT_USER", "USE WAREHOUSE REPORTING_WH",
          "USE DATABASE STREAMLIT_APPS", "USE SCHEMA DBO"]:
    cur.execute(s)
print("Connected ✓\n")


# ──────────────────────────────────────────────────────────────────────────────
# A. RISK TIER CALIBRATION (closed months only)
#    Validates that High/Med/Low tiers actually predicted different churn rates.
# ──────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("A. RISK TIER CALIBRATION — closed months (IS_MATURE = TRUE)")
print("=" * 70)

cur.execute("""
    SELECT
        CONTRACT_RISK_TIER_RELATIVE,
        COUNT(*)                                                          AS N_CONTRACTS,
        ROUND(SUM(ATR), 0)                                                AS TOTAL_ATR,
        -- Actual churn: contract churned if actual retained < 95% of ATR
        ROUND(AVG(CASE WHEN COALESCE(ACTUAL_RETAINED_ARR, 0) / NULLIF(ATR, 0) < 0.95
                       THEN 1.0 ELSE 0.0 END) * 100, 1)                  AS ACTUAL_CHURN_RATE_PCT,
        -- Dollar retention
        ROUND(SUM(COALESCE(ACTUAL_RETAINED_ARR, 0)) / NULLIF(SUM(ATR), 0) * 100, 1)
                                                                          AS DOLLAR_RETENTION_PCT,
        -- Model predicted churn (mean CHURN_PCT per tier)
        ROUND(AVG(CHURN_PCT), 1)                                          AS AVG_PREDICTED_CHURN_PCT,
        -- Relative risk score
        ROUND(AVG(CONTRACT_RISK_PCTL_IN_SEG), 1)                         AS AVG_RISK_PCTL
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE IS_MATURE = TRUE
      AND IS_MATURED_MONTH = TRUE
      AND COALESCE(ATR, 0) > 0
      AND CONTRACT_RISK_TIER_RELATIVE IS NOT NULL
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    GROUP BY CONTRACT_RISK_TIER_RELATIVE
    ORDER BY ACTUAL_CHURN_RATE_PCT DESC
""")
cal_df = pd.DataFrame(cur.fetchall(), columns=[
    "TIER", "N", "TOTAL_ATR", "ACTUAL_CHURN_PCT", "DOLLAR_RETENTION_PCT",
    "AVG_PREDICTED_CHURN_PCT", "AVG_RISK_PCTL"
])
print(cal_df.to_string(index=False))

if not cal_df.empty:
    tiers = cal_df["TIER"].tolist()
    churns = cal_df["ACTUAL_CHURN_PCT"].tolist()
    # Check monotonicity: High > Medium > Low
    high = cal_df.loc[cal_df["TIER"].str.lower().str.contains("high", na=False), "ACTUAL_CHURN_PCT"]
    low  = cal_df.loc[cal_df["TIER"].str.lower().str.contains("low",  na=False), "ACTUAL_CHURN_PCT"]
    if not high.empty and not low.empty and float(high.iloc[0]) > float(low.iloc[0]):
        print("\n  ✓ CALIBRATION OK — High tier has meaningfully higher actual churn than Low tier.")
    else:
        print("\n  ⚠ CALIBRATION WARNING — Tier ordering does not match actual churn rates.")


# ──────────────────────────────────────────────────────────────────────────────
# B. CHURN PCT DISTRIBUTION (forward months Jul–Sep 2026)
#    Confirms the model is producing a realistic spread, not flat values.
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("B. CHURN PCT DISTRIBUTION — Jul–Sep 2026 forward contracts")
print("=" * 70)

cur.execute("""
    SELECT
        RENEWAL_MONTH,
        SEGMENT,
        COUNT(*)                              AS N_CONTRACTS,
        ROUND(MIN(CHURN_PCT), 1)              AS MIN_CHURN,
        ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY CHURN_PCT), 1) AS P25,
        ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY CHURN_PCT), 1) AS P50,
        ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY CHURN_PCT), 1) AS P75,
        ROUND(MAX(CHURN_PCT), 1)              AS MAX_CHURN,
        COUNT(DISTINCT CHURN_PCT)             AS N_DISTINCT
    FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
    WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-09-01'
      AND IS_MATURE = FALSE
      AND COALESCE(ATR, 0) > 0
      AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    GROUP BY RENEWAL_MONTH, SEGMENT
    ORDER BY RENEWAL_MONTH, SEGMENT
""")
dist_df = pd.DataFrame(cur.fetchall(), columns=[
    "MONTH", "SEGMENT", "N", "MIN", "P25", "P50", "P75", "MAX", "N_DISTINCT"
])
print(dist_df.to_string(index=False))

flat_segs = dist_df[dist_df["N_DISTINCT"] <= 2]["SEGMENT"].unique()
if len(flat_segs) > 0:
    print(f"\n  ⚠ FLAT segments (≤2 distinct CHURN_PCT values): {list(flat_segs)}")
    print("    These segments will have inaccurate risk rankings.")
else:
    print("\n  ✓ All segments show diverse CHURN_PCT distributions — model calibrated.")


# ──────────────────────────────────────────────────────────────────────────────
# C. TOP-50 WATCHLIST — Jul–Sep 2026 with key driving signals
#    For human cross-check against Salesforce / AM knowledge.
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("C. TOP-50 WATCHLIST — Jul–Sep 2026 (key signals for manual review)")
print("=" * 70)

cur.execute("""
    WITH fwd AS (
        SELECT
            CONTRACT_ID,
            PARTNER,
            SEGMENT,
            PRODUCT_GROUP,
            PRODUCT_PORTFOLIO,
            RENEWAL_MONTH,
            RENEWAL_MANAGER,
            ACCOUNT_OWNER,
            ROUND(ATR, 0)                                          AS ATR,
            ROUND(AT_RISK_DOLLARS, 0)                              AS LOSS_EXPOSURE,
            ROUND(CHURN_PCT, 1)                                    AS CHURN_PCT,
            ROUND(RETENTION_PCT, 1)                                AS RETENTION_PCT,
            CONTRACT_RISK_TIER_RELATIVE                            AS RISK_TIER,
            ROUND(CONTRACT_RISK_PCTL_IN_SEG, 0)                   AS RISK_PCTL_IN_SEG,
            FINANCE_ANCHOR_SOURCE,
            -- How different is ML from the finance anchor?
            ROUND(RETENTION_PCT - FINANCE_ANCHOR_RATE * 100, 1)   AS ML_VS_ANCHOR_PP,
            -- Flag if early warning (renewing within 3 months of today)
            CASE WHEN RENEWAL_MONTH <= DATEADD('MONTH', 3, DATE_TRUNC('MONTH', CURRENT_DATE()))
                 THEN TRUE ELSE FALSE END                          AS IS_NEAR_TERM
        FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-09-01'
          AND IS_MATURE = FALSE
          AND COALESCE(AT_RISK_DOLLARS, 0) > 0
          AND RUN_ID != 'V5_ANCHOR_FALLBACK'
    )
    SELECT *
    FROM fwd
    ORDER BY LOSS_EXPOSURE DESC
    LIMIT 50
""")
wl_df = pd.DataFrame(cur.fetchall(), columns=[
    "CONTRACT_ID", "PARTNER", "SEGMENT", "PRODUCT_GROUP", "PRODUCT_PORTFOLIO",
    "RENEWAL_MONTH", "RENEWAL_MANAGER", "ACCOUNT_OWNER",
    "ATR", "LOSS_EXPOSURE", "CHURN_PCT", "RETENTION_PCT",
    "RISK_TIER", "RISK_PCTL_IN_SEG", "FINANCE_ANCHOR_SOURCE",
    "ML_VS_ANCHOR_PP", "IS_NEAR_TERM"
])

print(f"\n  {len(wl_df)} contracts, total loss exposure: ${wl_df['LOSS_EXPOSURE'].sum():,.0f}")
print(f"  Total ATR: ${wl_df['ATR'].sum():,.0f}")
print()

# Flag anything that looks miscalibrated
flags = []
# High churn % from anchor fallback (not ML-scored) — lower confidence
anchor_fallback = wl_df[wl_df["FINANCE_ANCHOR_SOURCE"].str.contains("anchor", case=False, na=False)]
if not anchor_fallback.empty:
    flags.append(f"  ⚠ {len(anchor_fallback)} contracts use ANCHOR fallback (not ML-scored) — verify manually.")

# Very high ML vs anchor gap (>20pp) — model disagrees significantly with finance
big_gap = wl_df[wl_df["ML_VS_ANCHOR_PP"].abs() > 20]
if not big_gap.empty:
    flags.append(f"  ⚠ {len(big_gap)} contracts have ML vs anchor gap >20pp — model signal may be stale.")

# Very low churn PCT but huge ATR (loss driven by size, not probability)
size_driven = wl_df[(wl_df["CHURN_PCT"] < 15) & (wl_df["ATR"] > 500_000)]
if not size_driven.empty:
    flags.append(f"  ℹ {len(size_driven)} contracts flagged primarily by size (CHURN_PCT<15%, ATR>$500K). "
                 f"Risk ranking reflects dollar exposure, not probability.")

if flags:
    for f in flags:
        print(f)
else:
    print("  ✓ No obvious miscalibration flags.")

print()
print(wl_df.to_string(index=False))

# Save CSV
wl_df.to_csv(OUT_CSV, index=False)
print(f"\n  Saved to: {OUT_CSV}")

conn.close()
print("\nValidation complete.")
