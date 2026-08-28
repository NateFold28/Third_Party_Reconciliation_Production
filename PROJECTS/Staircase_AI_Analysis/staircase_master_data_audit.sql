USE ROLE STREAMLIT_USER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE STREAMLIT_APPS;
USE SCHEMA DBO;

-- Audit target table.
SET MASTER_TABLE = 'STREAMLIT_APPS.DBO.STAIRCASE_BUSINESS_OUTCOMES_MASTER';

-- 1) Core completeness and availability metrics needed for correlation analysis.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
summary AS (
    SELECT
        COUNT(*) AS total_rows,
        COUNT_IF(ACCOUNT_ID IS NULL) AS null_account_id,
        COUNT_IF(CONTRACT_ID_UFR IS NULL) AS null_contract_id,
        COUNT_IF(MASTER_DATE IS NULL) AS null_master_date,
        COUNT_IF(
            COALESCE(STAIRCASE_HEALTH, STAIRCASE_SENTIMENT, STAIRCASE_ENGAGEMENT) IS NULL
        ) AS rows_missing_all_staircase_scores,
        COUNT_IF(STAIRCASE_HEALTH IS NULL) AS null_staircase_health,
        COUNT_IF(STAIRCASE_SENTIMENT IS NULL) AS null_staircase_sentiment,
        COUNT_IF(STAIRCASE_ENGAGEMENT IS NULL) AS null_staircase_engagement,
        COUNT_IF(CHURN_FLAG IS NULL) AS null_churn_flag,
        COUNT_IF(RENEWAL_OUTCOME IS NULL) AS null_renewal_outcome,
        COUNT_IF(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NULL) AS null_renewal_value,
        COUNT_IF(DOWNGRADE_DOLLARS_C_BUDGET_RATE IS NULL) AS null_downsell_value,
        COUNT_IF(LOST_OR_CHURN_DOLLARS_C_BUDGET_RATE IS NULL) AS null_product_loss_value
    FROM base
)
SELECT
    total_rows,
    null_account_id,
    null_contract_id,
    null_master_date,
    rows_missing_all_staircase_scores,
    ROUND(100.0 * rows_missing_all_staircase_scores / NULLIF(total_rows, 0), 2) AS pct_missing_all_staircase_scores,
    null_staircase_health,
    null_staircase_sentiment,
    null_staircase_engagement,
    null_churn_flag,
    null_renewal_outcome,
    null_renewal_value,
    null_downsell_value,
    null_product_loss_value
FROM summary;

-- 2) Duplicate grain check: one record per account, contract, and renewal date expected.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
)
SELECT
    ACCOUNT_ID,
    CONTRACT_ID_UFR,
    MASTER_DATE,
    COUNT(*) AS row_count_at_grain
FROM base
GROUP BY 1, 2, 3
HAVING COUNT(*) > 1
ORDER BY row_count_at_grain DESC, ACCOUNT_ID, CONTRACT_ID_UFR, MASTER_DATE;

-- 3) Temporal alignment check to prevent data leakage.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
)
SELECT
    COUNT(*) AS total_rows,
    COUNT_IF(SCORECARD_SNAPSHOT_DATE > MASTER_DATE) AS leakage_rows,
    ROUND(100.0 * COUNT_IF(SCORECARD_SNAPSHOT_DATE > MASTER_DATE) / NULLIF(COUNT(*), 0), 2) AS leakage_pct,
    COUNT_IF(SCORECARD_SNAPSHOT_DATE IS NULL) AS rows_missing_scorecard_snapshot
FROM base;

-- 4) Score freshness distribution (days between score snapshot and event date).
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
scored AS (
    SELECT
        DATEDIFF('day', SCORECARD_SNAPSHOT_DATE, MASTER_DATE) AS score_age_days
    FROM base
    WHERE SCORECARD_SNAPSHOT_DATE IS NOT NULL
      AND MASTER_DATE IS NOT NULL
)
SELECT
    COUNT(*) AS rows_with_age,
    AVG(score_age_days) AS avg_score_age_days,
    MEDIAN(score_age_days) AS median_score_age_days,
    MIN(score_age_days) AS min_score_age_days,
    MAX(score_age_days) AS max_score_age_days,
    COUNT_IF(score_age_days > 30) AS rows_older_than_30_days,
    COUNT_IF(score_age_days > 90) AS rows_older_than_90_days,
    COUNT_IF(score_age_days > 180) AS rows_older_than_180_days
FROM scored;

-- 5) Correlation-ready sample size by staircase score and business outcome pair.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
pairs AS (
    SELECT
        'STAIRCASE_HEALTH x CHURN_FLAG' AS pair_name,
        COUNT_IF(STAIRCASE_HEALTH IS NOT NULL AND CHURN_FLAG IS NOT NULL) AS pair_non_null_rows
    FROM base
    UNION ALL
    SELECT
        'STAIRCASE_SENTIMENT x CHURN_FLAG',
        COUNT_IF(STAIRCASE_SENTIMENT IS NOT NULL AND CHURN_FLAG IS NOT NULL)
    FROM base
    UNION ALL
    SELECT
        'STAIRCASE_ENGAGEMENT x CHURN_FLAG',
        COUNT_IF(STAIRCASE_ENGAGEMENT IS NOT NULL AND CHURN_FLAG IS NOT NULL)
    FROM base
    UNION ALL
    SELECT
        'STAIRCASE_HEALTH x RENEWAL_VALUE',
        COUNT_IF(STAIRCASE_HEALTH IS NOT NULL AND ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL)
    FROM base
    UNION ALL
    SELECT
        'STAIRCASE_SENTIMENT x RENEWAL_VALUE',
        COUNT_IF(STAIRCASE_SENTIMENT IS NOT NULL AND ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL)
    FROM base
    UNION ALL
    SELECT
        'STAIRCASE_ENGAGEMENT x RENEWAL_VALUE',
        COUNT_IF(STAIRCASE_ENGAGEMENT IS NOT NULL AND ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL)
    FROM base
    UNION ALL
    SELECT
        'STAIRCASE_HEALTH x DOWNSELL_VALUE',
        COUNT_IF(STAIRCASE_HEALTH IS NOT NULL AND DOWNGRADE_DOLLARS_C_BUDGET_RATE IS NOT NULL)
    FROM base
    UNION ALL
    SELECT
        'STAIRCASE_SENTIMENT x DOWNSELL_VALUE',
        COUNT_IF(STAIRCASE_SENTIMENT IS NOT NULL AND DOWNGRADE_DOLLARS_C_BUDGET_RATE IS NOT NULL)
    FROM base
    UNION ALL
    SELECT
        'STAIRCASE_ENGAGEMENT x DOWNSELL_VALUE',
        COUNT_IF(STAIRCASE_ENGAGEMENT IS NOT NULL AND DOWNGRADE_DOLLARS_C_BUDGET_RATE IS NOT NULL)
    FROM base
    UNION ALL
    SELECT
        'STAIRCASE_HEALTH x PRODUCT_LOSS_VALUE',
        COUNT_IF(STAIRCASE_HEALTH IS NOT NULL AND LOST_OR_CHURN_DOLLARS_C_BUDGET_RATE IS NOT NULL)
    FROM base
    UNION ALL
    SELECT
        'STAIRCASE_SENTIMENT x PRODUCT_LOSS_VALUE',
        COUNT_IF(STAIRCASE_SENTIMENT IS NOT NULL AND LOST_OR_CHURN_DOLLARS_C_BUDGET_RATE IS NOT NULL)
    FROM base
    UNION ALL
    SELECT
        'STAIRCASE_ENGAGEMENT x PRODUCT_LOSS_VALUE',
        COUNT_IF(STAIRCASE_ENGAGEMENT IS NOT NULL AND LOST_OR_CHURN_DOLLARS_C_BUDGET_RATE IS NOT NULL)
    FROM base
)
SELECT
    pair_name,
    pair_non_null_rows,
    IFF(pair_non_null_rows >= 200, 'PASS', 'WARN_LOW_SAMPLE') AS readiness_flag
FROM pairs
ORDER BY pair_non_null_rows DESC, pair_name;

-- 6) Coverage trend by month to find periods where data may be too sparse.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
)
SELECT
    DATE_TRUNC('month', MASTER_DATE) AS month_start,
    COUNT(*) AS total_rows,
    COUNT_IF(COALESCE(STAIRCASE_HEALTH, STAIRCASE_SENTIMENT, STAIRCASE_ENGAGEMENT) IS NOT NULL) AS rows_with_any_staircase_score,
    COUNT_IF(CHURN_FLAG IS NOT NULL) AS rows_with_churn,
    COUNT_IF(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL) AS rows_with_renewal_value,
    COUNT_IF(DOWNGRADE_DOLLARS_C_BUDGET_RATE IS NOT NULL) AS rows_with_downsell,
    COUNT_IF(LOST_OR_CHURN_DOLLARS_C_BUDGET_RATE IS NOT NULL) AS rows_with_product_loss,
    ROUND(
        100.0 * COUNT_IF(COALESCE(STAIRCASE_HEALTH, STAIRCASE_SENTIMENT, STAIRCASE_ENGAGEMENT) IS NOT NULL)
        / NULLIF(COUNT(*), 0),
        2
    ) AS pct_with_any_staircase
FROM base
GROUP BY 1
ORDER BY 1;

-- 7) Outcome distribution sanity check for target balance.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
)
SELECT
    RENEWAL_OUTCOME,
    COUNT(*) AS outcome_rows,
    ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 2) AS outcome_pct
FROM base
GROUP BY 1
ORDER BY outcome_rows DESC;

-- 8) Staircase score distribution and variance check (must not be constant).
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
)
SELECT
    'STAIRCASE_HEALTH' AS score_name,
    COUNT_IF(STAIRCASE_HEALTH IS NOT NULL) AS non_null_rows,
    MIN(STAIRCASE_HEALTH) AS min_value,
    MAX(STAIRCASE_HEALTH) AS max_value,
    AVG(STAIRCASE_HEALTH) AS avg_value,
    STDDEV(STAIRCASE_HEALTH) AS stddev_value,
    IFF(STDDEV(STAIRCASE_HEALTH) > 0, 'PASS', 'FAIL_NO_VARIANCE') AS variance_flag
FROM base
UNION ALL
SELECT
    'STAIRCASE_SENTIMENT',
    COUNT_IF(STAIRCASE_SENTIMENT IS NOT NULL),
    MIN(STAIRCASE_SENTIMENT),
    MAX(STAIRCASE_SENTIMENT),
    AVG(STAIRCASE_SENTIMENT),
    STDDEV(STAIRCASE_SENTIMENT),
    IFF(STDDEV(STAIRCASE_SENTIMENT) > 0, 'PASS', 'FAIL_NO_VARIANCE')
FROM base
UNION ALL
SELECT
    'STAIRCASE_ENGAGEMENT',
    COUNT_IF(STAIRCASE_ENGAGEMENT IS NOT NULL),
    MIN(STAIRCASE_ENGAGEMENT),
    MAX(STAIRCASE_ENGAGEMENT),
    AVG(STAIRCASE_ENGAGEMENT),
    STDDEV(STAIRCASE_ENGAGEMENT),
    IFF(STDDEV(STAIRCASE_ENGAGEMENT) > 0, 'PASS', 'FAIL_NO_VARIANCE')
FROM base;

-- 9) Monetary metric sanity checks for key business outcomes.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
)
SELECT
    COUNT(*) AS total_rows,
    COUNT_IF(ADJ_ATR_C_BUDGET_RATE < 0) AS negative_adj_atr_rows,
    COUNT_IF(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE < 0) AS negative_renewal_value_rows,
    COUNT_IF(DOWNGRADE_DOLLARS_C_BUDGET_RATE < 0) AS negative_downsell_rows,
    COUNT_IF(LOST_OR_CHURN_DOLLARS_C_BUDGET_RATE < 0) AS negative_product_loss_rows,
    COUNT_IF(ABS(NET_RETENTION_RATE) > 5) AS extreme_net_retention_rows,
    COUNT_IF(ABS(GROSS_RETENTION_RATE) > 5) AS extreme_gross_retention_rows
FROM base;

-- 10) Account-level continuity check: accounts with repeated renewals and staircase history.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
)
SELECT
    ACCOUNT_ID,
    COUNT(*) AS renewal_rows,
    COUNT_IF(COALESCE(STAIRCASE_HEALTH, STAIRCASE_SENTIMENT, STAIRCASE_ENGAGEMENT) IS NOT NULL) AS rows_with_staircase,
    MIN(MASTER_DATE) AS first_renewal_date,
    MAX(MASTER_DATE) AS last_renewal_date
FROM base
GROUP BY 1
HAVING COUNT(*) >= 2
ORDER BY renewal_rows DESC, ACCOUNT_ID;

-- 11) Duplicate inflation summary at analysis grain.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
grain AS (
    SELECT
        ACCOUNT_ID,
        CONTRACT_ID_UFR,
        MASTER_DATE,
        COUNT(*) AS rows_per_grain
    FROM base
    GROUP BY 1, 2, 3
)
SELECT
    COUNT(*) AS distinct_grain_rows,
    SUM(rows_per_grain) AS physical_rows,
    ROUND(AVG(rows_per_grain), 3) AS avg_rows_per_grain,
    MAX(rows_per_grain) AS max_rows_per_grain,
    COUNT_IF(rows_per_grain > 1) AS duplicated_grain_rows,
    ROUND(100.0 * COUNT_IF(rows_per_grain > 1) / NULLIF(COUNT(*), 0), 2) AS pct_grain_rows_duplicated
FROM grain;

-- 12) Duplicate source diagnostic: multiplicity from ARR product dimensions.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
arr_mult AS (
    SELECT
        ACCOUNT_ID,
        CONTRACT_ID_UFR,
        MASTER_DATE,
        COUNT(
            DISTINCT CONCAT_WS(
                '|',
                COALESCE(ARR_PRODUCT_PORTFOLIO, '__NULL__'),
                COALESCE(ARR_PRODUCT_GROUP, '__NULL__'),
                COALESCE(ARR_PRODUCT_LINE, '__NULL__')
            )
        ) AS arr_product_combos,
        COUNT(*) AS rows_per_grain
    FROM base
    GROUP BY 1, 2, 3
)
SELECT
    COUNT(*) AS grain_rows,
    COUNT_IF(arr_product_combos > 1) AS grain_rows_with_multi_arr_products,
    ROUND(100.0 * COUNT_IF(arr_product_combos > 1) / NULLIF(COUNT(*), 0), 2) AS pct_multi_arr_products,
    AVG(arr_product_combos) AS avg_arr_product_combos,
    MAX(arr_product_combos) AS max_arr_product_combos,
    AVG(rows_per_grain) AS avg_rows_per_grain
FROM arr_mult;

-- 13) Consistency test: duplicated grain rows should agree on model-critical fields.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
dup_grain AS (
    SELECT
        ACCOUNT_ID,
        CONTRACT_ID_UFR,
        MASTER_DATE,
        COUNT(*) AS rows_per_grain,
        COUNT(DISTINCT STAIRCASE_HEALTH) AS d_staircase_health,
        COUNT(DISTINCT STAIRCASE_SENTIMENT) AS d_staircase_sentiment,
        COUNT(DISTINCT STAIRCASE_ENGAGEMENT) AS d_staircase_engagement,
        COUNT(DISTINCT CHURN_FLAG) AS d_churn_flag,
        COUNT(DISTINCT RENEWAL_OUTCOME) AS d_renewal_outcome,
        COUNT(DISTINCT ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE) AS d_renewal_value,
        COUNT(DISTINCT DOWNGRADE_DOLLARS_C_BUDGET_RATE) AS d_downsell,
        COUNT(DISTINCT LOST_OR_CHURN_DOLLARS_C_BUDGET_RATE) AS d_product_loss
    FROM base
    GROUP BY 1, 2, 3
    HAVING COUNT(*) > 1
)
SELECT
    COUNT(*) AS duplicated_grains,
    COUNT_IF(d_staircase_health > 1) AS duplicated_grains_with_health_conflict,
    COUNT_IF(d_staircase_sentiment > 1) AS duplicated_grains_with_sentiment_conflict,
    COUNT_IF(d_staircase_engagement > 1) AS duplicated_grains_with_engagement_conflict,
    COUNT_IF(d_churn_flag > 1) AS duplicated_grains_with_churn_conflict,
    COUNT_IF(d_renewal_outcome > 1) AS duplicated_grains_with_outcome_conflict,
    COUNT_IF(d_renewal_value > 1) AS duplicated_grains_with_renewal_value_conflict,
    COUNT_IF(d_downsell > 1) AS duplicated_grains_with_downsell_conflict,
    COUNT_IF(d_product_loss > 1) AS duplicated_grains_with_product_loss_conflict
FROM dup_grain;

-- 14) Deterministic one-row-per-event cohort for correlation work.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
dedup AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY ACCOUNT_ID, CONTRACT_ID_UFR, MASTER_DATE
            ORDER BY
                IFF(ARR_PRODUCT_PORTFOLIO IS NOT NULL, 1, 0) DESC,
                ARR_PRODUCT_PORTFOLIO,
                ARR_PRODUCT_GROUP,
                ARR_PRODUCT_LINE
        ) AS rn
    FROM base
)
SELECT
    COUNT(*) AS dedup_rows,
    COUNT_IF(COALESCE(STAIRCASE_HEALTH, STAIRCASE_SENTIMENT, STAIRCASE_ENGAGEMENT) IS NOT NULL) AS dedup_rows_with_staircase,
    COUNT_IF(CHURN_FLAG IS NOT NULL) AS dedup_rows_with_churn,
    COUNT_IF(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL) AS dedup_rows_with_renewal_value,
    COUNT_IF(DOWNGRADE_DOLLARS_C_BUDGET_RATE IS NOT NULL) AS dedup_rows_with_downsell,
    COUNT_IF(LOST_OR_CHURN_DOLLARS_C_BUDGET_RATE IS NOT NULL) AS dedup_rows_with_product_loss
FROM dedup
WHERE rn = 1;

-- 15) Correlation pair sample sizes on deduped cohort (avoids duplicate inflation).
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
dedup AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY ACCOUNT_ID, CONTRACT_ID_UFR, MASTER_DATE
            ORDER BY
                IFF(ARR_PRODUCT_PORTFOLIO IS NOT NULL, 1, 0) DESC,
                ARR_PRODUCT_PORTFOLIO,
                ARR_PRODUCT_GROUP,
                ARR_PRODUCT_LINE
        ) AS rn
    FROM base
),
cohort AS (
    SELECT *
    FROM dedup
    WHERE rn = 1
),
pairs AS (
    SELECT 'STAIRCASE_HEALTH x CHURN_FLAG' AS pair_name, COUNT_IF(STAIRCASE_HEALTH IS NOT NULL AND CHURN_FLAG IS NOT NULL) AS pair_non_null_rows FROM cohort
    UNION ALL
    SELECT 'STAIRCASE_SENTIMENT x CHURN_FLAG', COUNT_IF(STAIRCASE_SENTIMENT IS NOT NULL AND CHURN_FLAG IS NOT NULL) FROM cohort
    UNION ALL
    SELECT 'STAIRCASE_ENGAGEMENT x CHURN_FLAG', COUNT_IF(STAIRCASE_ENGAGEMENT IS NOT NULL AND CHURN_FLAG IS NOT NULL) FROM cohort
    UNION ALL
    SELECT 'STAIRCASE_HEALTH x RENEWAL_VALUE', COUNT_IF(STAIRCASE_HEALTH IS NOT NULL AND ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL) FROM cohort
    UNION ALL
    SELECT 'STAIRCASE_SENTIMENT x RENEWAL_VALUE', COUNT_IF(STAIRCASE_SENTIMENT IS NOT NULL AND ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL) FROM cohort
    UNION ALL
    SELECT 'STAIRCASE_ENGAGEMENT x RENEWAL_VALUE', COUNT_IF(STAIRCASE_ENGAGEMENT IS NOT NULL AND ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE IS NOT NULL) FROM cohort
    UNION ALL
    SELECT 'STAIRCASE_HEALTH x DOWNSELL_VALUE', COUNT_IF(STAIRCASE_HEALTH IS NOT NULL AND DOWNGRADE_DOLLARS_C_BUDGET_RATE IS NOT NULL) FROM cohort
    UNION ALL
    SELECT 'STAIRCASE_SENTIMENT x DOWNSELL_VALUE', COUNT_IF(STAIRCASE_SENTIMENT IS NOT NULL AND DOWNGRADE_DOLLARS_C_BUDGET_RATE IS NOT NULL) FROM cohort
    UNION ALL
    SELECT 'STAIRCASE_ENGAGEMENT x DOWNSELL_VALUE', COUNT_IF(STAIRCASE_ENGAGEMENT IS NOT NULL AND DOWNGRADE_DOLLARS_C_BUDGET_RATE IS NOT NULL) FROM cohort
    UNION ALL
    SELECT 'STAIRCASE_HEALTH x PRODUCT_LOSS_VALUE', COUNT_IF(STAIRCASE_HEALTH IS NOT NULL AND LOST_OR_CHURN_DOLLARS_C_BUDGET_RATE IS NOT NULL) FROM cohort
    UNION ALL
    SELECT 'STAIRCASE_SENTIMENT x PRODUCT_LOSS_VALUE', COUNT_IF(STAIRCASE_SENTIMENT IS NOT NULL AND LOST_OR_CHURN_DOLLARS_C_BUDGET_RATE IS NOT NULL) FROM cohort
    UNION ALL
    SELECT 'STAIRCASE_ENGAGEMENT x PRODUCT_LOSS_VALUE', COUNT_IF(STAIRCASE_ENGAGEMENT IS NOT NULL AND LOST_OR_CHURN_DOLLARS_C_BUDGET_RATE IS NOT NULL) FROM cohort
)
SELECT
    pair_name,
    pair_non_null_rows,
    IFF(pair_non_null_rows >= 200, 'PASS', 'WARN_LOW_SAMPLE') AS readiness_flag
FROM pairs
ORDER BY pair_non_null_rows DESC, pair_name;

-- 16) Closed-outcome cohort stats (exclude pending churn labels for churn modeling).
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
dedup AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY ACCOUNT_ID, CONTRACT_ID_UFR, MASTER_DATE
            ORDER BY
                IFF(ARR_PRODUCT_PORTFOLIO IS NOT NULL, 1, 0) DESC,
                ARR_PRODUCT_PORTFOLIO,
                ARR_PRODUCT_GROUP,
                ARR_PRODUCT_LINE
        ) AS rn
    FROM base
),
cohort AS (
    SELECT *
    FROM dedup
    WHERE rn = 1
)
SELECT
    COUNT(*) AS dedup_rows_total,
    COUNT_IF(CHURN_FLAG IS NOT NULL) AS dedup_rows_closed_for_churn,
    ROUND(100.0 * COUNT_IF(CHURN_FLAG IS NOT NULL) / NULLIF(COUNT(*), 0), 2) AS pct_closed_for_churn,
    COUNT_IF(CHURN_FLAG = 1) AS churn_positive_rows,
    COUNT_IF(CHURN_FLAG = 0) AS churn_negative_rows
FROM cohort;

-- 17) Derived churn target from renewal outcome (recommended when CHURN_FLAG lacks negatives).
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
dedup AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY ACCOUNT_ID, CONTRACT_ID_UFR, MASTER_DATE
            ORDER BY
                IFF(ARR_PRODUCT_PORTFOLIO IS NOT NULL, 1, 0) DESC,
                ARR_PRODUCT_PORTFOLIO,
                ARR_PRODUCT_GROUP,
                ARR_PRODUCT_LINE
        ) AS rn
    FROM base
),
cohort AS (
    SELECT
        dedup.*,
        COALESCE(
            dedup.DERIVED_CHURN_FLAG,
            CASE
                WHEN dedup.RENEWAL_OUTCOME = 'CHURNED' THEN 1
                WHEN dedup.RENEWAL_OUTCOME = 'RENEWED' THEN 0
                ELSE NULL
            END
        ) AS DERIVED_CHURN_FLAG_FINAL
    FROM dedup
    WHERE rn = 1
)
SELECT
    COUNT(*) AS dedup_rows_total,
    COUNT_IF(DERIVED_CHURN_FLAG_FINAL IS NOT NULL) AS rows_closed_for_derived_churn,
    ROUND(100.0 * COUNT_IF(DERIVED_CHURN_FLAG_FINAL IS NOT NULL) / NULLIF(COUNT(*), 0), 2) AS pct_closed_for_derived_churn,
    COUNT_IF(DERIVED_CHURN_FLAG_FINAL = 1) AS derived_churn_positive_rows,
    COUNT_IF(DERIVED_CHURN_FLAG_FINAL = 0) AS derived_churn_negative_rows,
    ROUND(
        100.0 * COUNT_IF(DERIVED_CHURN_FLAG_FINAL = 1)
        / NULLIF(COUNT_IF(DERIVED_CHURN_FLAG_FINAL IS NOT NULL), 0),
        2
    ) AS derived_churn_positive_rate_pct
FROM cohort;

-- 18) Staircase sample sizes vs derived churn target on deduped closed outcomes.
WITH base AS (
    SELECT *
    FROM IDENTIFIER($MASTER_TABLE)
),
dedup AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY ACCOUNT_ID, CONTRACT_ID_UFR, MASTER_DATE
            ORDER BY
                IFF(ARR_PRODUCT_PORTFOLIO IS NOT NULL, 1, 0) DESC,
                ARR_PRODUCT_PORTFOLIO,
                ARR_PRODUCT_GROUP,
                ARR_PRODUCT_LINE
        ) AS rn
    FROM base
),
cohort AS (
    SELECT
        dedup.*,
        COALESCE(
            dedup.DERIVED_CHURN_FLAG,
            CASE
                WHEN dedup.RENEWAL_OUTCOME = 'CHURNED' THEN 1
                WHEN dedup.RENEWAL_OUTCOME = 'RENEWED' THEN 0
                ELSE NULL
            END
        ) AS DERIVED_CHURN_FLAG_FINAL
    FROM dedup
    WHERE rn = 1
),
pairs AS (
    SELECT
        'STAIRCASE_HEALTH x DERIVED_CHURN_FLAG' AS pair_name,
        COUNT_IF(STAIRCASE_HEALTH IS NOT NULL AND DERIVED_CHURN_FLAG_FINAL IS NOT NULL) AS pair_non_null_rows
    FROM cohort
    UNION ALL
    SELECT
        'STAIRCASE_SENTIMENT x DERIVED_CHURN_FLAG',
        COUNT_IF(STAIRCASE_SENTIMENT IS NOT NULL AND DERIVED_CHURN_FLAG_FINAL IS NOT NULL)
    FROM cohort
    UNION ALL
    SELECT
        'STAIRCASE_ENGAGEMENT x DERIVED_CHURN_FLAG',
        COUNT_IF(STAIRCASE_ENGAGEMENT IS NOT NULL AND DERIVED_CHURN_FLAG_FINAL IS NOT NULL)
    FROM cohort
)
SELECT
    pair_name,
    pair_non_null_rows,
    IFF(pair_non_null_rows >= 200, 'PASS', 'WARN_LOW_SAMPLE') AS readiness_flag
FROM pairs
ORDER BY pair_non_null_rows DESC, pair_name;
