"""
Production V5 — Risk Calibration & Segment/Portfolio Trend Audit

Diagnoses:
  1. CHURN_PCT distribution by segment (is it calibrated or uniform?)
  2. CONTRACT_RISK_PCTL_IN_SEG distribution (is it actually a percentile 0-100?)
  3. CONTRACT_RISK_TIER_RELATIVE distribution (flat vs varied?)
  4. PRODUCT_PORTFOLIO presence and segment cardinality
  5. Segment vs Portfolio rate trend by month (are they flat or diverging?)
  6. EFFECTIVE_FORECAST/RENEWAL_FORECAST relationship check
  7. IS_MATURE distribution (forward vs historical mix)
  8. Raw model score distributions (CONTRACT_RISK_SCORE)
"""

import sys
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection
import json

def audit_calibration():
    try:
        conn = get_snowflake_connection()
        cur = conn.cursor()
        cur.execute("USE ROLE STREAMLIT_USER")
        cur.execute("USE WAREHOUSE REPORTING_WH")
        cur.execute("USE DATABASE STREAMLIT_APPS")
        cur.execute("USE SCHEMA DBO")
        print("✓ Connected to STREAMLIT_APPS.DBO\n")
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return

    TABLE = "V5_SANDBOX_APP_CONTRACT_DETAIL"

    # =========================================================================
    # CHECK 1: Column existence — does the table have all expected score columns?
    # =========================================================================
    print("=" * 80)
    print("CHECK 1: SCORE COLUMN EXISTENCE")
    print("=" * 80)

    required_cols = [
        "CHURN_PCT", "RETENTION_PCT", "CONTRACT_RISK_SCORE",
        "CONTRACT_RISK_PCTL_IN_SEG", "CONTRACT_RISK_TIER",
        "CONTRACT_RISK_TIER_RELATIVE", "ML_RISK_SCORE",
        "AT_RISK_DOLLARS", "EARLY_WARNING_FLAG",
        "SEGMENT", "PRODUCT_PORTFOLIO", "COHORT",
        "RENEWAL_FORECAST", "ML_FORECAST", "FINANCE_FORECAST",
        "ATR", "ACTUAL_RETAINED_ARR", "IS_MATURE",
    ]

    try:
        cur.execute(f"""
            SELECT COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'DBO' AND TABLE_NAME = '{TABLE}'
            ORDER BY ORDINAL_POSITION
        """)
        existing_cols = {row[0] for row in cur.fetchall()}
        
        present, missing = [], []
        for c in required_cols:
            (present if c in existing_cols else missing).append(c)
        
        print(f"\nPresent ({len(present)}): {', '.join(present)}")
        if missing:
            print(f"\n⚠ MISSING ({len(missing)}): {', '.join(missing)}")
            print("  → These missing columns will cause NULL/fallback behavior in the app")
        else:
            print("\n✓ All expected score columns are present")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # CHECK 2: CHURN_PCT distribution by segment
    # =========================================================================
    print("\n" + "=" * 80)
    print("CHECK 2: CHURN_PCT DISTRIBUTION BY SEGMENT")
    print("=" * 80)
    print("Healthy: each segment should have DISTINCT min/mean/max values")
    print("Broken:  all segments show same value OR most rows are NULL/0\n")

    try:
        cur.execute(f"""
            SELECT
                SEGMENT,
                COUNT(*) AS n,
                ROUND(MIN(CHURN_PCT), 2) AS min_churn,
                ROUND(AVG(CHURN_PCT), 2) AS avg_churn,
                ROUND(MAX(CHURN_PCT), 2) AS max_churn,
                ROUND(STDDEV(CHURN_PCT), 4) AS stddev_churn,
                SUM(CASE WHEN CHURN_PCT IS NULL THEN 1 ELSE 0 END) AS null_count,
                SUM(CASE WHEN CHURN_PCT = 0 THEN 1 ELSE 0 END) AS zero_count,
                COUNT(DISTINCT ROUND(CHURN_PCT, 4)) AS distinct_values
            FROM {TABLE}
            WHERE ATR > 0
            GROUP BY SEGMENT
            ORDER BY SEGMENT
        """)

        rows = cur.fetchall()
        print(f"{'Segment':<20} {'N':>6} {'Min':>7} {'Avg':>7} {'Max':>7} {'StdDev':>8} {'Nulls':>6} {'Zeros':>6} {'Distinct':>9}")
        print("-" * 85)
        for seg, n, mn, av, mx, sd, nulls, zeros, distinct in rows:
            flag = ""
            if nulls > n * 0.1:
                flag += " ⚠NULL"
            if zeros > n * 0.3:
                flag += " ⚠ZERO"
            if sd is not None and sd < 0.001:
                flag += " ⚠FLAT"
            if distinct is not None and distinct < 10:
                flag += " ⚠FEW_DISTINCT"
            print(f"{str(seg):<20} {n:>6,} {mn:>7.2f} {av:>7.2f} {mx:>7.2f} {sd:>8.4f} {nulls:>6,} {zeros:>6,} {distinct:>9,} {flag}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # CHECK 3: CONTRACT_RISK_PCTL_IN_SEG distribution
    # =========================================================================
    print("\n" + "=" * 80)
    print("CHECK 3: CONTRACT_RISK_PCTL_IN_SEG DISTRIBUTION")
    print("=" * 80)
    print("Healthy: values spread 0-100 with roughly uniform distribution per segment")
    print("Broken:  all 50 (NULL fallback), all same value, or <10 distinct values\n")

    try:
        cur.execute(f"""
            SELECT
                SEGMENT,
                COUNT(*) AS n,
                ROUND(MIN(CONTRACT_RISK_PCTL_IN_SEG), 1) AS min_pctl,
                ROUND(AVG(CONTRACT_RISK_PCTL_IN_SEG), 1) AS avg_pctl,
                ROUND(MAX(CONTRACT_RISK_PCTL_IN_SEG), 1) AS max_pctl,
                SUM(CASE WHEN CONTRACT_RISK_PCTL_IN_SEG IS NULL THEN 1 ELSE 0 END) AS nulls,
                SUM(CASE WHEN CONTRACT_RISK_PCTL_IN_SEG = 50 THEN 1 ELSE 0 END) AS at_50,
                COUNT(DISTINCT ROUND(CONTRACT_RISK_PCTL_IN_SEG, 0)) AS distinct_pctls
            FROM {TABLE}
            WHERE ATR > 0
            GROUP BY SEGMENT
            ORDER BY SEGMENT
        """)

        rows = cur.fetchall()
        print(f"{'Segment':<20} {'N':>6} {'Min':>6} {'Avg':>6} {'Max':>6} {'Nulls':>6} {'At_50':>6} {'Distinct':>9}")
        print("-" * 75)
        for seg, n, mn, av, mx, nulls, at_50, distinct in rows:
            flag = ""
            if nulls > n * 0.5:
                flag += " ⚠MOSTLY_NULL"
            if at_50 > n * 0.5:
                flag += " ⚠MOSTLY_50 (NULL fallback being used?)"
            if distinct is not None and distinct < 20:
                flag += f" ⚠FEW_DISTINCT({distinct})"
            print(f"{str(seg):<20} {n:>6,} {mn:>6.1f} {av:>6.1f} {mx:>6.1f} {nulls:>6,} {at_50:>6,} {distinct:>9,} {flag}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # CHECK 4: CONTRACT_RISK_TIER_RELATIVE distribution
    # =========================================================================
    print("\n" + "=" * 80)
    print("CHECK 4: CONTRACT_RISK_TIER_RELATIVE DISTRIBUTION")
    print("=" * 80)
    print("Healthy: multiple tiers populated (Low, Medium-Low, Medium, Medium-High, High)\n")

    try:
        cur.execute(f"""
            SELECT
                CONTRACT_RISK_TIER_RELATIVE,
                COUNT(*) AS n,
                ROUND(AVG(CHURN_PCT), 2) AS avg_churn,
                ROUND(AVG(ATR), 0) AS avg_atr
            FROM {TABLE}
            WHERE ATR > 0
            GROUP BY CONTRACT_RISK_TIER_RELATIVE
            ORDER BY n DESC
        """)

        rows = cur.fetchall()
        total = sum(r[1] for r in rows)
        print(f"{'Tier':<20} {'N':>8} {'%Total':>8} {'AvgChurn':>10} {'AvgATR':>10}")
        print("-" * 65)
        for tier, n, avg_churn, avg_atr in rows:
            pct = n / total * 100 if total > 0 else 0
            print(f"{str(tier):<20} {n:>8,} {pct:>8.1f}% {avg_churn:>10.2f} {avg_atr:>10,.0f}")
        
        if len(rows) < 3:
            print("\n⚠ FEWER THAN 3 TIERS — relative risk bucketing is broken")
        elif any(r[0] is None for r in rows):
            null_count = sum(r[1] for r in rows if r[0] is None)
            print(f"\n⚠ {null_count:,} NULL tier rows — column not populated by pipeline")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # CHECK 5: SEGMENT vs PRODUCT_PORTFOLIO cardinality
    # =========================================================================
    print("\n" + "=" * 80)
    print("CHECK 5: PRODUCT_PORTFOLIO PRESENCE & CARDINALITY")
    print("=" * 80)
    print("Healthy: multiple distinct portfolios across segments")
    print("Broken:  all NULL, all 'Unknown', or only 1 distinct value\n")

    try:
        cur.execute(f"""
            SELECT
                SEGMENT,
                COUNT(DISTINCT PRODUCT_PORTFOLIO) AS distinct_portfolios,
                COUNT(DISTINCT CASE WHEN PRODUCT_PORTFOLIO IS NULL OR PRODUCT_PORTFOLIO = '' OR PRODUCT_PORTFOLIO = 'Unknown' THEN 1 END) AS has_unknown,
                SUM(CASE WHEN PRODUCT_PORTFOLIO IS NULL THEN 1 ELSE 0 END) AS null_portfolio,
                SUM(CASE WHEN PRODUCT_PORTFOLIO = 'Unknown' THEN 1 ELSE 0 END) AS unknown_portfolio
            FROM {TABLE}
            WHERE ATR > 0
            GROUP BY SEGMENT
            ORDER BY SEGMENT
        """)

        rows = cur.fetchall()
        print(f"{'Segment':<20} {'Distinct Portfolios':>20} {'NULL':>8} {'Unknown':>10}")
        print("-" * 65)
        for seg, distinct, has_unk, nulls, unknowns in rows:
            flag = ""
            if distinct <= 1:
                flag = " ⚠ONLY 1 PORTFOLIO — trend chart will be flat"
            elif nulls > 0 or unknowns > 0:
                flag = f" ⚠ {nulls+unknowns:,} null/unknown rows"
            print(f"{str(seg):<20} {distinct:>20,} {nulls:>8,} {unknowns:>10,} {flag}")

        # Full portfolio list
        cur.execute(f"""
            SELECT PRODUCT_PORTFOLIO, COUNT(*) AS n
            FROM {TABLE}
            WHERE ATR > 0
            GROUP BY PRODUCT_PORTFOLIO
            ORDER BY n DESC
            LIMIT 15
        """)
        print("\nAll portfolios in data:")
        for portfolio, n in cur.fetchall():
            print(f"  {str(portfolio):<35} {n:>8,} rows")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # CHECK 6: SEGMENT + PRODUCT_PORTFOLIO rate trend — are they flat?
    # =========================================================================
    print("\n" + "=" * 80)
    print("CHECK 6: SEGMENT RATE TREND BY MONTH (last 6 closed + 3 forward)")
    print("=" * 80)
    print("Healthy: rates vary by 5-30pp across months and segments")
    print("Broken:  all segments show same rate, or rates flat at one value\n")

    try:
        cur.execute(f"""
            SELECT
                RENEWAL_MONTH,
                SEGMENT,
                COUNT(*) AS n_contracts,
                ROUND(SUM(ATR) / 1e6, 2) AS atr_m,
                ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 1) AS ml_rate_pct,
                ROUND(SUM(RENEWAL_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 1) AS renewal_rate_pct,
                ROUND(SUM(ACTUAL_RETAINED_ARR) / NULLIF(SUM(ATR), 0) * 100, 1) AS actual_rate_pct
            FROM {TABLE}
            WHERE ATR > 0
              AND RENEWAL_MONTH BETWEEN DATE_TRUNC('MONTH', CURRENT_DATE() - INTERVAL '6 months')
                                    AND DATE_TRUNC('MONTH', CURRENT_DATE() + INTERVAL '3 months')
            GROUP BY RENEWAL_MONTH, SEGMENT
            ORDER BY RENEWAL_MONTH, SEGMENT
        """)

        rows = cur.fetchall()
        print(f"{'Month':<12} {'Segment':<15} {'N':>5} {'ATR $M':>8} {'ML%':>7} {'RenewFcst%':>12} {'Actual%':>9}")
        print("-" * 75)
        prev_month = None
        for month, seg, n, atr_m, ml_rate, ren_rate, actual_rate in rows:
            if prev_month and prev_month != str(month):
                print("")
            prev_month = str(month)
            actual_str = f"{actual_rate:>9.1f}" if actual_rate else "      N/A"
            print(f"{str(month):<12} {str(seg):<15} {n:>5,} {atr_m:>8.2f} {ml_rate:>7.1f} {ren_rate:>12.1f} {actual_str}")

    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # CHECK 7: PORTFOLIO rate trend
    # =========================================================================
    print("\n" + "=" * 80)
    print("CHECK 7: PORTFOLIO RATE TREND BY MONTH")
    print("=" * 80)
    print("Healthy: portfolios diverge by 10-40pp from each other")
    print("Broken:  all portfolios show same rate (flat line)\n")

    try:
        cur.execute(f"""
            SELECT
                RENEWAL_MONTH,
                PRODUCT_PORTFOLIO,
                COUNT(*) AS n,
                ROUND(SUM(ATR) / 1e6, 2) AS atr_m,
                ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 1) AS ml_rate,
                ROUND(SUM(RENEWAL_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 1) AS renewal_rate
            FROM {TABLE}
            WHERE ATR > 0
              AND RENEWAL_MONTH BETWEEN DATE_TRUNC('MONTH', CURRENT_DATE() - INTERVAL '3 months')
                                    AND DATE_TRUNC('MONTH', CURRENT_DATE() + INTERVAL '3 months')
            GROUP BY RENEWAL_MONTH, PRODUCT_PORTFOLIO
            ORDER BY RENEWAL_MONTH, PRODUCT_PORTFOLIO
        """)

        rows = cur.fetchall()
        print(f"{'Month':<12} {'Portfolio':<30} {'N':>5} {'ATR $M':>8} {'ML%':>7} {'Renew%':>8}")
        print("-" * 80)
        prev_month = None
        for month, portfolio, n, atr_m, ml_rate, ren_rate in rows:
            if prev_month and prev_month != str(month):
                print("")
            prev_month = str(month)
            print(f"{str(month):<12} {str(portfolio):<30} {n:>5,} {atr_m:>8.2f} {ml_rate:>7.1f} {ren_rate:>8.1f}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # CHECK 8: RENEWAL_FORECAST vs ML_FORECAST divergence
    # =========================================================================
    print("\n" + "=" * 80)
    print("CHECK 8: RENEWAL_FORECAST vs ML_FORECAST DIVERGENCE")
    print("=" * 80)
    print("Healthy: RENEWAL_FORECAST ≠ ML_FORECAST (calibration was applied)")
    print("Broken:  RENEWAL_FORECAST = ML_FORECAST (calibration lost or bypassed)\n")

    try:
        cur.execute(f"""
            SELECT
                SEGMENT,
                COUNT(*) AS n,
                SUM(CASE WHEN ABS(RENEWAL_FORECAST - ML_FORECAST) < 0.01 THEN 1 ELSE 0 END) AS same_count,
                ROUND(SUM(CASE WHEN ABS(RENEWAL_FORECAST - ML_FORECAST) < 0.01 THEN 1 ELSE 0 END) / COUNT(*) * 100, 1) AS pct_same,
                ROUND(AVG(RENEWAL_FORECAST - ML_FORECAST), 2) AS avg_delta,
                ROUND(MAX(ABS(RENEWAL_FORECAST - ML_FORECAST)), 2) AS max_delta
            FROM {TABLE}
            WHERE ATR > 0
            GROUP BY SEGMENT
            ORDER BY pct_same DESC
        """)

        rows = cur.fetchall()
        print(f"{'Segment':<20} {'N':>6} {'Same':>6} {'%Same':>7} {'AvgDelta':>10} {'MaxDelta':>10}")
        print("-" * 70)
        for seg, n, same, pct_same, avg_delta, max_delta in rows:
            flag = " ⚠ IDENTICAL — calibration overwrite missing!" if pct_same > 95 else ""
            print(f"{str(seg):<20} {n:>6,} {same:>6,} {pct_same:>7.1f}% {avg_delta:>10.2f} {max_delta:>10.2f}{flag}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # CHECK 9: IS_MATURE distribution (are we using actuals where we should?)
    # =========================================================================
    print("\n" + "=" * 80)
    print("CHECK 9: IS_MATURE / IS_MATURED_DISPLAY_CAL DISTRIBUTION")
    print("=" * 80)

    try:
        cur.execute(f"""
            SELECT
                IS_MATURE,
                IS_MATURED_DISPLAY_CAL,
                RENEWAL_MONTH,
                COUNT(*) AS n
            FROM {TABLE}
            WHERE ATR > 0
              AND RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE() - INTERVAL '4 months')
            GROUP BY IS_MATURE, IS_MATURED_DISPLAY_CAL, RENEWAL_MONTH
            ORDER BY RENEWAL_MONTH, IS_MATURE
        """)

        rows = cur.fetchall()
        print(f"{'IS_MATURE':<12} {'IS_MAT_DISP':<14} {'MONTH':<12} {'N':>6}")
        print("-" * 50)
        for is_mat, is_mat_cal, month, n in rows:
            print(f"{str(is_mat):<12} {str(is_mat_cal):<14} {str(month):<12} {n:>6,}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # CHECK 10: Verify CONTRACT_RISK_SCORE scale (0-1 decimal vs 0-100 pct)
    # =========================================================================
    print("\n" + "=" * 80)
    print("CHECK 10: CONTRACT_RISK_SCORE SCALE DETECTION")
    print("=" * 80)
    print("The app has a scale-detection fallback: if median < 1.0, it multiplies by 100")
    print("This can distort risk scores if the scale shifted post-retrain\n")

    try:
        cur.execute(f"""
            SELECT
                SEGMENT,
                ROUND(MIN(CONTRACT_RISK_SCORE), 4) AS min_score,
                ROUND(PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY CONTRACT_RISK_SCORE), 4) AS p10,
                ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY CONTRACT_RISK_SCORE), 4) AS median_score,
                ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY CONTRACT_RISK_SCORE), 4) AS p90,
                ROUND(MAX(CONTRACT_RISK_SCORE), 4) AS max_score,
                SUM(CASE WHEN CONTRACT_RISK_SCORE > 1.0 THEN 1 ELSE 0 END) AS above_1,
                SUM(CASE WHEN CONTRACT_RISK_SCORE <= 1.0 THEN 1 ELSE 0 END) AS at_or_below_1
            FROM {TABLE}
            WHERE ATR > 0 AND CONTRACT_RISK_SCORE IS NOT NULL
            GROUP BY SEGMENT
            ORDER BY SEGMENT
        """)

        rows = cur.fetchall()
        print(f"{'Segment':<15} {'Min':>8} {'P10':>8} {'Median':>8} {'P90':>8} {'Max':>8} {'Above1':>8} {'AtOrBelow1':>12}")
        print("-" * 90)
        for seg, mn, p10, med, p90, mx, above1, below1 in rows:
            scale_warning = ""
            if med is not None and med <= 1.0:
                scale_warning = " ⚠ DECIMAL SCALE — app will multiply by 100"
            elif med is not None and med > 1.0:
                scale_warning = " ✓ PCT scale (0-100)"
            print(f"{str(seg):<15} {mn:>8.4f} {p10:>8.4f} {med:>8.4f} {p90:>8.4f} {mx:>8.4f} {above1:>8,} {below1:>12,}{scale_warning}")
    except Exception as e:
        print(f"⚠ Error: {e}")

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print("\n" + "=" * 80)
    print("AUDIT COMPLETE — Review ⚠ flags above for root causes")
    print("=" * 80)
    print("""
Common causes of flat segment/portfolio trend lines:
  A) PRODUCT_PORTFOLIO is all NULL or 'Unknown' (lost during retrain join)
  B) RENEWAL_FORECAST = ML_FORECAST for all rows (calibration step skipped)
  C) CHURN_PCT uniform across segments (model scoring in wrong context)
  D) CONTRACT_RISK_PCTL_IN_SEG all NULL (percentile not recomputed post-retrain)
  E) IS_MATURE all FALSE for historical months (actuals not being used)

Common causes of skewed relative risk scoring:
  A) CONTRACT_RISK_SCORE scale changed (decimal → pct) without app-side detection
  B) CONTRACT_RISK_PCTL_IN_SEG NULL → app uses fallback 50 for all contracts
  C) CHURN_PCT compressed to narrow range (0.1-0.15 for all) after calibration
  D) ATR distribution changed dramatically (dominates 25% weight in composite)
""")

    conn.close()

if __name__ == '__main__':
    audit_calibration()
