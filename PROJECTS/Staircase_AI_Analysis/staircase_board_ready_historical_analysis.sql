USE ROLE STREAMLIT_USER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE STREAMLIT_APPS;
USE SCHEMA DBO;

-- ============================================================================
-- Staircase AI Board-Ready Historical Analysis
-- Purpose:
--   1) Verify if the master dataset is sufficient for historical outcome analysis.
--   2) Quantify relationship between Staircase Health and realized churn/renewal outcomes.
--   3) Provide executive-ready outputs to support a clear board narrative.
--   4) Optionally compare Staircase Health to model predictions from V2 output.
--
-- Primary data source:
--   STREAMLIT_APPS.DBO.STAIRCASE_BUSINESS_OUTCOMES_MASTER
--
-- Notes:
--   - This analysis is observational. It shows association, not causal proof.
--   - Run sections in order and export each result set for slide building.
-- ============================================================================

SET MASTER_TABLE = 'STREAMLIT_APPS.DBO.STAIRCASE_BUSINESS_OUTCOMES_MASTER';

-- ============================================================================
-- SECTION 0: Confirm labels and renewal rate fields exist and are populated.
-- Why this matters:
--   This validates whether you truly have historical churn labels and renewal %.
-- ============================================================================
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
)
SELECT
    COUNT(*) AS total_rows,
    COUNT_IF(DERIVED_CHURN_FLAG IS NOT NULL) AS rows_with_historical_churn_label,
    COUNT_IF(GROSS_RETENTION_RATE IS NOT NULL) AS rows_with_historical_renewal_pct,
    COUNT_IF(DERIVED_CHURN_FLAG IS NOT NULL AND GROSS_RETENTION_RATE IS NOT NULL) AS rows_with_both_labels,
    ROUND(100.0 * COUNT_IF(DERIVED_CHURN_FLAG IS NOT NULL) / NULLIF(COUNT(*), 0), 2) AS pct_with_churn_label,
    ROUND(100.0 * COUNT_IF(GROSS_RETENTION_RATE IS NOT NULL) / NULLIF(COUNT(*), 0), 2) AS pct_with_renewal_pct,
    ROUND(100.0 * COUNT_IF(DERIVED_CHURN_FLAG IS NOT NULL AND GROSS_RETENTION_RATE IS NOT NULL) / NULLIF(COUNT(*), 0), 2) AS pct_with_both_labels
FROM base;

-- ============================================================================
-- SECTION 1: Data sufficiency + quality gates for board-safe interpretation.
-- ============================================================================
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
summary AS (
    SELECT
        COUNT(*) AS total_rows,
        COUNT_IF(HAS_ANY_STAIRCASE_SCORE = 1) AS rows_with_any_staircase,
        COUNT_IF(IS_CHURN_ANALYSIS_READY = 1) AS churn_ready_rows,
        COUNT_IF(IS_RENEWAL_VALUE_ANALYSIS_READY = 1) AS renewal_value_ready_rows,
        COUNT_IF(SCORECARD_SNAPSHOT_DATE IS NOT NULL) AS rows_with_snapshot_date,
        COUNT_IF(SCORECARD_SNAPSHOT_DATE > MASTER_DATE) AS leakage_rows,
        COUNT_IF(DATEDIFF('day', SCORECARD_SNAPSHOT_DATE, MASTER_DATE) > 90) AS stale_over_90d_rows,
        COUNT_IF(DERIVED_CHURN_FLAG IS NOT NULL) AS rows_with_churn,
        COUNT_IF(GROSS_RETENTION_RATE IS NOT NULL) AS rows_with_gross_retention,
        COUNT_IF(ADJ_ATR_C_BUDGET_RATE > 0) AS rows_with_positive_atr
    FROM base
)
SELECT
    total_rows,
    rows_with_any_staircase,
    churn_ready_rows,
    renewal_value_ready_rows,
    rows_with_snapshot_date,
    leakage_rows,
    stale_over_90d_rows,
    rows_with_churn,
    rows_with_gross_retention,
    rows_with_positive_atr,
    ROUND(100.0 * rows_with_any_staircase / NULLIF(total_rows, 0), 2) AS pct_with_any_staircase,
    ROUND(100.0 * churn_ready_rows / NULLIF(total_rows, 0), 2) AS pct_churn_ready,
    ROUND(100.0 * renewal_value_ready_rows / NULLIF(total_rows, 0), 2) AS pct_renewal_value_ready,
    ROUND(100.0 * leakage_rows / NULLIF(total_rows, 0), 2) AS leakage_pct,
    ROUND(100.0 * stale_over_90d_rows / NULLIF(total_rows, 0), 2) AS stale_over_90d_pct
FROM summary;

-- ============================================================================
-- SECTION 2: Core historical association metrics (board headline table).
-- Outputs:
--   - Correlation with historical churn label.
--   - Correlation with historical renewal % (gross retention rate).
--   - Correlation with historical renewal dollars.
-- ============================================================================
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
)
SELECT
    'CHURN_LABEL' AS outcome_name,
    COUNT(*) AS n_rows,
    CORR(STAIRCASE_HEALTH, DERIVED_CHURN_FLAG) AS pearson_corr
FROM base
WHERE IS_CHURN_ANALYSIS_READY = 1
  AND STAIRCASE_HEALTH IS NOT NULL
  AND DERIVED_CHURN_FLAG IS NOT NULL
UNION ALL
SELECT
    'GROSS_RETENTION_RATE',
    COUNT(*),
    CORR(STAIRCASE_HEALTH, GROSS_RETENTION_RATE)
FROM base
WHERE STAIRCASE_HEALTH IS NOT NULL
  AND GROSS_RETENTION_RATE IS NOT NULL
UNION ALL
SELECT
    'RENEWAL_DOLLARS',
    COUNT(*),
    CORR(STAIRCASE_HEALTH, ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE)
FROM base
WHERE STAIRCASE_HEALTH IS NOT NULL
  AND ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL
ORDER BY outcome_name;

-- Spearman (rank-based) for robustness to outliers/ties.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
health_churn AS (
    SELECT
        RANK() OVER (ORDER BY STAIRCASE_HEALTH) AS rx,
        RANK() OVER (ORDER BY DERIVED_CHURN_FLAG) AS ry
    FROM base
    WHERE IS_CHURN_ANALYSIS_READY = 1
      AND STAIRCASE_HEALTH IS NOT NULL
      AND DERIVED_CHURN_FLAG IS NOT NULL
),
health_grr AS (
    SELECT
        RANK() OVER (ORDER BY STAIRCASE_HEALTH) AS rx,
        RANK() OVER (ORDER BY GROSS_RETENTION_RATE) AS ry
    FROM base
    WHERE STAIRCASE_HEALTH IS NOT NULL
      AND GROSS_RETENTION_RATE IS NOT NULL
),
health_renewal AS (
    SELECT
        RANK() OVER (ORDER BY STAIRCASE_HEALTH) AS rx,
        RANK() OVER (ORDER BY ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE) AS ry
    FROM base
    WHERE STAIRCASE_HEALTH IS NOT NULL
      AND ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL
)
SELECT
    'CHURN_LABEL' AS outcome_name,
    (SELECT COUNT(*) FROM health_churn) AS n_rows,
    (SELECT CORR(rx, ry) FROM health_churn) AS spearman_corr
UNION ALL
SELECT
    'GROSS_RETENTION_RATE',
    (SELECT COUNT(*) FROM health_grr),
    (SELECT CORR(rx, ry) FROM health_grr)
UNION ALL
SELECT
    'RENEWAL_DOLLARS',
    (SELECT COUNT(*) FROM health_renewal),
    (SELECT CORR(rx, ry) FROM health_renewal)
ORDER BY outcome_name;

-- ============================================================================
-- SECTION 3: Staircase Health decile lift (most board-friendly view).
-- ============================================================================
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
    WHERE STAIRCASE_HEALTH IS NOT NULL
),
scored AS (
    SELECT
        ACCOUNT_ID,
        CONTRACT_ID_UFR,
        MASTER_DATE,
        STAIRCASE_HEALTH,
        DERIVED_CHURN_FLAG,
        GROSS_RETENTION_RATE,
        ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE AS RENEWAL_DOLLARS,
        ADJ_ATR_C_BUDGET_RATE AS ATR,
        NTILE(10) OVER (ORDER BY STAIRCASE_HEALTH) AS health_decile
    FROM base
)
SELECT
    health_decile,
    COUNT(*) AS n_rows,
    MIN(STAIRCASE_HEALTH) AS min_health,
    MAX(STAIRCASE_HEALTH) AS max_health,
    AVG(DERIVED_CHURN_FLAG) AS churn_rate,
    AVG(GROSS_RETENTION_RATE) AS avg_gross_retention_rate,
    MEDIAN(GROSS_RETENTION_RATE) AS median_gross_retention_rate,
    AVG(RENEWAL_DOLLARS) AS avg_renewal_dollars,
    MEDIAN(RENEWAL_DOLLARS) AS median_renewal_dollars,
    SUM(ATR) AS total_atr,
    SUM(RENEWAL_DOLLARS) AS total_renewal_dollars,
    CASE WHEN SUM(ATR) > 0 THEN SUM(RENEWAL_DOLLARS) / SUM(ATR) END AS portfolio_gross_retention
FROM scored
GROUP BY 1
ORDER BY 1;

-- Top-vs-bottom executive lift table.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
    WHERE STAIRCASE_HEALTH IS NOT NULL
),
scored AS (
    SELECT
        STAIRCASE_HEALTH,
        DERIVED_CHURN_FLAG,
        GROSS_RETENTION_RATE,
        ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE AS RENEWAL_DOLLARS,
        ADJ_ATR_C_BUDGET_RATE AS ATR,
        NTILE(10) OVER (ORDER BY STAIRCASE_HEALTH) AS health_decile
    FROM base
),
bands AS (
    SELECT
        CASE
            WHEN health_decile IN (1, 2) THEN 'BOTTOM_20'
            WHEN health_decile IN (9, 10) THEN 'TOP_20'
            ELSE 'MIDDLE_60'
        END AS health_band,
        DERIVED_CHURN_FLAG,
        GROSS_RETENTION_RATE,
        RENEWAL_DOLLARS,
        ATR
    FROM scored
)
SELECT
    health_band,
    COUNT(*) AS n_rows,
    AVG(DERIVED_CHURN_FLAG) AS churn_rate,
    AVG(GROSS_RETENTION_RATE) AS avg_gross_retention_rate,
    AVG(RENEWAL_DOLLARS) AS avg_renewal_dollars,
    SUM(ATR) AS total_atr,
    SUM(RENEWAL_DOLLARS) AS total_renewal_dollars,
    CASE WHEN SUM(ATR) > 0 THEN SUM(RENEWAL_DOLLARS) / SUM(ATR) END AS portfolio_gross_retention
FROM bands
GROUP BY 1
ORDER BY CASE health_band WHEN 'BOTTOM_20' THEN 1 WHEN 'MIDDLE_60' THEN 2 ELSE 3 END;

-- ============================================================================
-- SECTION 4: Time stability check (if relationship changes over time, board should know).
-- ============================================================================
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
    WHERE STAIRCASE_HEALTH IS NOT NULL
      AND MASTER_DATE IS NOT NULL
),
monthly AS (
    SELECT
        DATE_TRUNC('month', MASTER_DATE) AS month_start,
        COUNT(*) AS n_rows,
        CORR(STAIRCASE_HEALTH, DERIVED_CHURN_FLAG) AS corr_health_churn,
        CORR(STAIRCASE_HEALTH, GROSS_RETENTION_RATE) AS corr_health_gross_retention,
        CORR(STAIRCASE_HEALTH, ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE) AS corr_health_renewal_dollars,
        AVG(DERIVED_CHURN_FLAG) AS churn_rate,
        AVG(GROSS_RETENTION_RATE) AS avg_gross_retention_rate
    FROM base
    WHERE DERIVED_CHURN_FLAG IS NOT NULL
      AND GROSS_RETENTION_RATE IS NOT NULL
    GROUP BY 1
)
SELECT
    month_start,
    n_rows,
    corr_health_churn,
    corr_health_gross_retention,
    corr_health_renewal_dollars,
    churn_rate,
    avg_gross_retention_rate
FROM monthly
ORDER BY month_start;

-- ============================================================================
-- SECTION 5: Cohort consistency check (to prevent Simpson's paradox).
-- ============================================================================
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
    WHERE STAIRCASE_HEALTH IS NOT NULL
      AND DERIVED_CHURN_FLAG IS NOT NULL
),
seg AS (
    SELECT
        COALESCE(TOUCH_TIER, 'UNKNOWN') AS touch_tier,
        COUNT(*) AS n_rows,
        CORR(STAIRCASE_HEALTH, DERIVED_CHURN_FLAG) AS corr_health_churn,
        AVG(DERIVED_CHURN_FLAG) AS churn_rate,
        AVG(STAIRCASE_HEALTH) AS avg_health
    FROM base
    GROUP BY 1
)
SELECT
    touch_tier,
    n_rows,
    corr_health_churn,
    churn_rate,
    avg_health,
    CASE WHEN n_rows >= 200 THEN 'OK_FOR_EXECUTIVE_READ' ELSE 'LOW_SAMPLE' END AS sample_flag
FROM seg
ORDER BY n_rows DESC, touch_tier;

-- Product portfolio consistency.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
    WHERE STAIRCASE_HEALTH IS NOT NULL
      AND DERIVED_CHURN_FLAG IS NOT NULL
),
port AS (
    SELECT
        COALESCE(PRODUCT_PORTFOLIO_UFR, 'UNKNOWN') AS product_portfolio,
        COUNT(*) AS n_rows,
        CORR(STAIRCASE_HEALTH, DERIVED_CHURN_FLAG) AS corr_health_churn,
        AVG(DERIVED_CHURN_FLAG) AS churn_rate
    FROM base
    GROUP BY 1
)
SELECT
    product_portfolio,
    n_rows,
    corr_health_churn,
    churn_rate,
    CASE WHEN n_rows >= 200 THEN 'OK_FOR_EXECUTIVE_READ' ELSE 'LOW_SAMPLE' END AS sample_flag
FROM port
ORDER BY n_rows DESC, product_portfolio;

-- ============================================================================
-- SECTION 6: ATR-controlled view (size bias check).
-- ============================================================================
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
    WHERE STAIRCASE_HEALTH IS NOT NULL
      AND DERIVED_CHURN_FLAG IS NOT NULL
      AND ADJ_ATR_C_BUDGET_RATE IS NOT NULL
      AND ADJ_ATR_C_BUDGET_RATE > 0
),
scored AS (
    SELECT
        STAIRCASE_HEALTH,
        DERIVED_CHURN_FLAG,
        ADJ_ATR_C_BUDGET_RATE,
        NTILE(5) OVER (ORDER BY ADJ_ATR_C_BUDGET_RATE) AS atr_quintile
    FROM base
)
SELECT
    atr_quintile,
    COUNT(*) AS n_rows,
    MIN(ADJ_ATR_C_BUDGET_RATE) AS min_atr,
    MAX(ADJ_ATR_C_BUDGET_RATE) AS max_atr,
    CORR(STAIRCASE_HEALTH, DERIVED_CHURN_FLAG) AS corr_health_churn_within_atr_bucket,
    AVG(DERIVED_CHURN_FLAG) AS churn_rate
FROM scored
GROUP BY 1
ORDER BY 1;

-- Use per-outcome eligible populations to avoid sample-intersection bias.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
churn_signal AS (
    SELECT
        'CHURN_LABEL' AS outcome_name,
        COUNT(*) AS n_rows,
        CORR(STAIRCASE_HEALTH, DERIVED_CHURN_FLAG) AS corr_value
    FROM base
    WHERE IS_CHURN_ANALYSIS_READY = 1
      AND STAIRCASE_HEALTH IS NOT NULL
      AND DERIVED_CHURN_FLAG IS NOT NULL
),
grr_signal AS (
    SELECT
        'GROSS_RETENTION_RATE' AS outcome_name,
        COUNT(*) AS n_rows,
        CORR(STAIRCASE_HEALTH, GROSS_RETENTION_RATE) AS corr_value
    FROM base
    WHERE STAIRCASE_HEALTH IS NOT NULL
      AND GROSS_RETENTION_RATE IS NOT NULL
),
renewal_signal AS (
    SELECT
        'RENEWAL_DOLLARS' AS outcome_name,
        COUNT(*) AS n_rows,
        CORR(STAIRCASE_HEALTH, ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE) AS corr_value
    FROM base
    WHERE STAIRCASE_HEALTH IS NOT NULL
      AND ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL
),
all_signals AS (
    SELECT * FROM churn_signal
    UNION ALL
    SELECT * FROM grr_signal
    UNION ALL
    SELECT * FROM renewal_signal
)
SELECT
    outcome_name,
    n_rows,
    corr_value,
    CASE
        WHEN ABS(corr_value) >= 0.20 THEN 'MODERATE_SIGNAL'
        WHEN ABS(corr_value) >= 0.10 THEN 'WEAK_TO_MODERATE_SIGNAL'
        ELSE 'LITTLE_SIGNAL'
    END AS signal_assessment
FROM all_signals
ORDER BY outcome_name;

-- 8A) Alignment inside prediction output only (no historical join required).
WITH pred AS (
    SELECT
        ACCOUNT_ID,
        CONTRACT_ID,
        PRODUCT_GROUP,
        RENEWAL_DATE,
        CHURN_PROBABILITY,
        RENEWAL_FORECAST,
        STAIRCASE_HEALTH_SCORE
    FROM STREAMLIT_APPS.DBO.RENEWAL_FORECAST_V2_OUTPUT
)
SELECT
    COUNT(*) AS prediction_rows,
    CORR(STAIRCASE_HEALTH_SCORE, CHURN_PROBABILITY) AS corr_healthscore_vs_model_churn_prob,
    CORR(STAIRCASE_HEALTH_SCORE, RENEWAL_FORECAST) AS corr_healthscore_vs_model_renewal_forecast
FROM pred;

-- 8B) Model-vs-actual on settled historical contracts (app-consistent grain).
WITH pred AS (
    SELECT
        CONTRACT_ID,
        PRODUCT_GROUP,
        DATE_TRUNC('month', RENEWAL_DATE) AS renewal_month,
        CHURN_PROBABILITY,
        RENEWAL_FORECAST,
        STAIRCASE_HEALTH_SCORE
    FROM STREAMLIT_APPS.DBO.RENEWAL_FORECAST_V2_OUTPUT
),
actuals AS (
    SELECT
        CONTRACT_ID_UFR,
        PRODUCT_GROUP,
        DATE_TRUNC('month', RENEWAL_DATE) AS renewal_month,
        ATR,
        RENEWED,
        OPEN,
        CASE
            WHEN COALESCE(OPEN, 0) <= 0 AND COALESCE(RENEWED, 0) <= 0 THEN 1
            WHEN COALESCE(OPEN, 0) <= 0 AND COALESCE(RENEWED, 0) > 0 THEN 0
            ELSE NULL
        END AS settled_churn_flag
    FROM STREAMLIT_APPS.DBO.RENEWAL_BASE_DATA
    WHERE ATR > 0
      AND COALESCE(OPEN, 0) <= 0
      AND RENEWED IS NOT NULL
),
joined AS (
    SELECT
        p.CONTRACT_ID,
        p.PRODUCT_GROUP,
        p.renewal_month,
        p.CHURN_PROBABILITY,
        p.RENEWAL_FORECAST,
        p.STAIRCASE_HEALTH_SCORE,
        a.ATR,
        a.RENEWED,
        a.settled_churn_flag
    FROM pred p
    INNER JOIN actuals a
        ON p.CONTRACT_ID = a.CONTRACT_ID_UFR
       AND p.PRODUCT_GROUP = a.PRODUCT_GROUP
       AND p.renewal_month = a.renewal_month
)
SELECT
    COUNT(*) AS settled_join_rows,
    CORR(CHURN_PROBABILITY, settled_churn_flag) AS corr_model_churn_prob_vs_settled_churn,
    CORR(RENEWAL_FORECAST, RENEWED) AS corr_model_forecast_vs_settled_renewed,
    CORR(STAIRCASE_HEALTH_SCORE, settled_churn_flag) AS corr_healthscore_vs_settled_churn,
    CORR(STAIRCASE_HEALTH_SCORE, RENEWED) AS corr_healthscore_vs_settled_renewed
FROM joined;

-- ============================================================================
-- END
-- After running, share outputs from Sections 2, 3, 5, 7, and 8 for interpretation.
-- ============================================================================

-- ============================================================================
-- SECTION 9 (OPTIONAL): Dedicated V2 backtest comparison vs Staircase Health.
-- Purpose:
--   Replace statistical join-based backtest checks with executive governance
--   guidance that avoids circular validation logic.
-- ============================================================================
/*
This is a very good question, and it shows you are now thinking at the
model-governance / executive-risk level rather than "what else can I analyze."

I will answer this in three parts, plainly and directly:
1) What not to do (and why)
2) What does paint a clearer picture without circular logic
3) What an exec-grade "next layer" actually looks like for black-box AI scores

1) First, kill the wrong idea cleanly

"Would backtesting my churn and renewal model on historical data that has
Staircase scores just show the model is better since it was trained on these
scores?"

Yes - exactly. And that is why it is the wrong test.

If:
- Your churn / renewal model was trained using Staircase scores (or derived
  features), and
- You backtest that same model on historical data with those same scores,

then any improvement is tautological.

You would be proving:
"A model performs better when you give it the same signal it was trained on."

That does not answer:
- Whether Staircase is independently useful
- Whether Staircase adds incremental signal
- Whether leadership should trust Staircase as a concept

Executive translation:
"That backtest would confirm the model learned Staircase - not that Staircase
is valid."

Do not present that as validation.

2) What actually paints a clearer picture (without circular logic)

The goal now is not more correlation.
The goal is to answer:
"What is Staircase uniquely good for, and what should it not be used for?"

Four analyses do this without circularity:

A) Incremental signal test (the right way)
- Fit a baseline churn model WITHOUT Staircase features.
- Add Staircase Health / Engagement as additional features.
- Compare AUC / PR, calibration, and lift in top risk deciles.

This tells you whether Staircase adds signal beyond what is already known.

Executive framing:
"Even after controlling for everything we already use, Staircase still improves
 churn risk separation by X."

B) Decision-boundary analysis (highly persuasive)
- Find accounts where Staircase says high risk but renewal model says low risk,
  and vice versa.
- Compare actual outcomes.

This answers whether Staircase catches different risk (behavioral/early) versus
commercial/late signals.

C) Actionability test (not accuracy)
- If CS acted on the bottom Staircase Health decile earlier:
  - How many churns were in that group?
  - How much ARR was at risk?
  - Were those accounts not yet flagged by existing commercial signals?

Executive framing:
"Staircase flags risk earlier than our commercial signals in X% of churn cases."

D) Stability and drift (governance lens)
- Test separation across months, segments, and ATR bands.
- Check score distribution drift quarter to quarter.
- Check degradation under changing business mix.

For black-box AI, trust comes from stability, not explainability.

3) How to talk about black-box AI responsibly

What Staircase is:
- A risk stratification signal
- Behavioral and relationship-oriented
- Directionally aligned with churn
- Useful for separating safer vs riskier populations

What Staircase is not:
- A causal model
- A dollar forecasting engine
- A substitute for renewal forecasting
- A metric to optimize directly

Leadership mental model:
"Staircase is like a credit score - not a revenue forecast."

Credit scores are black-box, imperfect, useful for screening, and dangerous if
over-interpreted.

4) Do we need more analysis before closing this?

No - not to answer the original question.
You already answered:
- Is this metric tied to reality? Yes.
- Is it misleading? Only if misused.
- Should we keep reporting it? Yes, with guardrails.

Yes - if leadership wants to change usage (forecast weighting, compensation,
or optimization).
Then run incremental and actionability analyses.

Bottom line:
- Do not backtest a model trained on Staircase to "prove" Staircase works.
- Do test incremental, distinct, and actionable signal.
- Treat Staircase as a risk screen, not a forecast.
- Current evidence is enough for a credible board update.
- Further work is refinement and governance, not basic validation.
*/
