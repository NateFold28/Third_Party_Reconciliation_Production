-- Portfolio Rollup: ATR for date range
-- Portfolio Rollup: aggregated by PRODUCT_PORTFOLIO
-- Source: V5_SANDBOX_APP_CONTRACT_DETAIL (mirrors dev app portfolio breakdown)

SELECT
  COALESCE(PRODUCT_PORTFOLIO, 'Unclassified')                               AS portfolio,
  COUNT(DISTINCT CONTRACT_ID)                                                AS contracts,
  SUM(COALESCE(ATR, 0))                                                     AS atr,
  SUM(CASE
    WHEN FINANCE_FORECAST > 0 AND FINANCE_FORECAST IS NOT NULL THEN FINANCE_FORECAST
    ELSE COALESCE(ML_FORECAST, 0)
  END)                                                                       AS effective_forecast,
  SUM(COALESCE(ACTUAL_RETAINED_ARR, 0))                                     AS actual_retained,
  SUM(COALESCE(AT_RISK_DOLLARS, 0))                                         AS at_risk_dollars,
  DIV0(
    SUM(CASE
      WHEN FINANCE_FORECAST > 0 AND FINANCE_FORECAST IS NOT NULL THEN FINANCE_FORECAST
      ELSE COALESCE(ML_FORECAST, 0)
    END),
    NULLIF(SUM(COALESCE(ATR, 0)), 0)
  ) * 100                                                                    AS forecast_pct,
  DIV0(SUM(COALESCE(ACTUAL_RETAINED_ARR, 0)), NULLIF(SUM(COALESCE(ATR, 0)), 0)) * 100 AS actual_pct
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH >= %(start_date)s
  AND RENEWAL_MONTH <= %(end_date)s
GROUP BY 1
ORDER BY atr DESC
;
