-- Model Performance summary (production-aligned source)

-- Model Performance: backtest accuracy by month + segment
-- Source: V5_SANDBOX_APP_BACKTEST (mirrors render_model_performance backtest section)
-- Returns one row per (RENEWAL_MONTH, SEGMENT) for the accuracy chart

SELECT
    COALESCE(METHOD, 'CHURN_ADJUSTED')                                                AS method,
    RENEWAL_MONTH::DATE                                                               AS renewal_month,
    COALESCE(SEGMENT, 'All')                                                          AS segment,
    COALESCE(N_CONTRACTS, 0)                                                          AS n_contracts,
    COALESCE(ATR, 0)                                                                  AS atr,
    COALESCE(PREDICTED_RETAINED, 0)                                                   AS predicted_retained,
    COALESCE(ACTUAL_RETAINED, 0)                                                      AS actual_retained,
    COALESCE(PREDICTED_RATE_PCT, 0)                                                   AS predicted_rate_pct,
    COALESCE(ACTUAL_RATE_PCT, 0)                                                      AS actual_rate_pct,
    COALESCE(ERROR_PP, 0)                                                             AS error_pp,
    ABS(COALESCE(ERROR_PP, 0))                                                        AS abs_error_pp
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_BACKTEST
WHERE RENEWAL_MONTH >= %(start_date)s
  AND RENEWAL_MONTH <= %(end_date)s
ORDER BY RENEWAL_MONTH DESC, SEGMENT ASC
;