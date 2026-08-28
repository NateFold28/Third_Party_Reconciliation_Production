-- All Renewals segment rollup (production-aligned source)

-- All Renewals: monthly rollup — all cohorts (mirrors render_all_renewals)
-- Source: V5_SANDBOX_APP_CONTRACT_DETAIL
-- Returns one row per RENEWAL_MONTH for the trend chart + monthly rollup table

SELECT
  DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE                                          AS renewal_month,
  COUNT(DISTINCT CONTRACT_ID)                                                        AS contracts,
  SUM(COALESCE(ATR, 0))                                                             AS atr,
  SUM(COALESCE(ML_FORECAST, 0))                                                     AS ml_forecast,
  SUM(COALESCE(FINANCE_FORECAST, 0))                                                AS finance_forecast,
  -- Effective forecast: finance > 0 → finance, else ml (matches app logic)
  SUM(CASE
    WHEN FINANCE_FORECAST > 0 AND FINANCE_FORECAST IS NOT NULL THEN FINANCE_FORECAST
    ELSE COALESCE(ML_FORECAST, 0)
  END)                                                                               AS effective_forecast,
  SUM(COALESCE(ACTUAL_RETAINED_ARR, 0))                                             AS actual_retained,
  SUM(COALESCE(OPEN_OPP, 0))                                                        AS open_opp,
  SUM(COALESCE(AT_RISK_DOLLARS, 0))                                                 AS at_risk_dollars,
  DIV0(
    SUM(CASE
      WHEN FINANCE_FORECAST > 0 AND FINANCE_FORECAST IS NOT NULL THEN FINANCE_FORECAST
      ELSE COALESCE(ML_FORECAST, 0)
    END),
    NULLIF(SUM(COALESCE(ATR, 0)), 0)
  ) * 100                                                                            AS forecast_pct,
  DIV0(SUM(COALESCE(ML_FORECAST, 0)), NULLIF(SUM(COALESCE(ATR, 0)), 0)) * 100      AS ml_forecast_pct,
  DIV0(SUM(COALESCE(ACTUAL_RETAINED_ARR, 0)), NULLIF(SUM(COALESCE(ATR, 0)), 0)) * 100 AS actual_pct,
  -- Is this month fully matured (no open opp)?
  IFF(SUM(COALESCE(OPEN_OPP, 0)) <= 0
    AND DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE <= DATE_TRUNC('MONTH', CURRENT_DATE())::DATE,
    TRUE, FALSE)                                                                    AS is_matured_month
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH >= %(start_date)s
  AND RENEWAL_MONTH <= %(end_date)s
GROUP BY 1
ORDER BY 1 ASC
;