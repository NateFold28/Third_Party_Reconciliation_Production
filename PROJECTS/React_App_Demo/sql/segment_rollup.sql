-- Segment (Region) Rollup: ATR for date range

SELECT
    CWS_REGION_C                                                            AS SEGMENT,
    SUM(ADJ_ATR_C_BUDGET_RATE)                                              AS ATR,
    SUM(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE)                           AS ACTUALS,
    DIV0(
        SUM(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE),
        SUM(ADJ_ATR_C_BUDGET_RATE)
    ) * 100                                                                  AS ACTUAL_PCT
FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
WHERE INCLUDE_FLAG_C = 1
  AND MASTER_DATE >= %(start_date)s
  AND MASTER_DATE <= %(end_date)s
GROUP BY 1
ORDER BY ATR DESC
;
-- Segment rollup: aggregated by SEGMENT for the All Renewals > Segment view
-- Mirrors the _render_executive_insights segment section in Development_Forecast_App_V1.py

SELECT
  COALESCE(SEGMENT, 'Unclassified')                                                 AS segment,
  COUNT(DISTINCT CONTRACT_ID)                                                        AS contracts,
  SUM(COALESCE(ATR, 0))                                                             AS atr,
  SUM(CASE
    WHEN FINANCE_FORECAST > 0 AND FINANCE_FORECAST IS NOT NULL THEN FINANCE_FORECAST
    ELSE COALESCE(ML_FORECAST, 0)
  END)                                                                               AS effective_forecast,
  SUM(COALESCE(ACTUAL_RETAINED_ARR, 0))                                             AS actual_retained,
  SUM(COALESCE(AT_RISK_DOLLARS, 0))                                                 AS at_risk_dollars,
  SUM(COALESCE(ML_FORECAST, 0))                                                     AS ml_forecast,
  DIV0(
    SUM(CASE
      WHEN FINANCE_FORECAST > 0 AND FINANCE_FORECAST IS NOT NULL THEN FINANCE_FORECAST
      ELSE COALESCE(ML_FORECAST, 0)
    END),
    NULLIF(SUM(COALESCE(ATR, 0)), 0)
  ) * 100                                                                            AS forecast_pct,
  DIV0(SUM(COALESCE(ACTUAL_RETAINED_ARR, 0)), NULLIF(SUM(COALESCE(ATR, 0)), 0)) * 100 AS actual_pct,
  AVG(COALESCE(CHURN_PCT, 0))                                                       AS avg_churn_pct
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH >= %(start_date)s
  AND RENEWAL_MONTH <= %(end_date)s
GROUP BY 1
ORDER BY atr DESC
;
