-- Open Renewals monthly rollup (production-aligned source)
-- Source table mirrors the production app contract detail surface.

-- Open Renewals: FORWARD_OPEN cohort monthly rollup
-- Source: V5_SANDBOX_APP_CONTRACT_DETAIL (dev app sandbox tables)
-- Mirrors Development_Forecast_App_V1.py render_open_renewals() KPIs + chart

SELECT
  DATE_TRUNC('MONTH', RENEWAL_MONTH)::DATE                                          AS renewal_month,
  COUNT(DISTINCT CONTRACT_ID)                                                        AS contracts,
  SUM(COALESCE(ATR, 0))                                                             AS atr,
  SUM(COALESCE(ML_FORECAST, 0))                                                     AS ml_forecast,
  SUM(COALESCE(FINANCE_FORECAST, 0))                                                AS finance_forecast,
  -- Effective: finance if > 0, else ml (mirrors assemble_frame() logic)
  SUM(CASE
    WHEN FINANCE_FORECAST > 0 AND FINANCE_FORECAST IS NOT NULL THEN FINANCE_FORECAST
    ELSE COALESCE(ML_FORECAST, 0)
  END)                                                                               AS effective_forecast,
  SUM(COALESCE(OPEN_OPP, 0))                                                        AS open_opp,
  SUM(COALESCE(ACTUAL_RETAINED_ARR, 0))                                             AS actual_retained,
  DIV0(
    SUM(CASE
      WHEN FINANCE_FORECAST > 0 AND FINANCE_FORECAST IS NOT NULL THEN FINANCE_FORECAST
      ELSE COALESCE(ML_FORECAST, 0)
    END),
    NULLIF(SUM(COALESCE(ATR, 0)), 0)
  ) * 100                                                                            AS forecast_pct,
  DIV0(SUM(COALESCE(ML_FORECAST, 0)), NULLIF(SUM(COALESCE(ATR, 0)), 0)) * 100      AS ml_forecast_pct,
  -- At-risk dollars for the period
  SUM(COALESCE(AT_RISK_DOLLARS, 0))                                                 AS at_risk_dollars,
  -- Manual input count
  COUNT_IF(SEGMENT IS NOT NULL)                                                      AS n_rows
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
WHERE RENEWAL_MONTH >= %(start_date)s
  AND RENEWAL_MONTH <= %(end_date)s
  AND COALESCE(COHORT, '') = 'FORWARD_OPEN'
GROUP BY 1
ORDER BY 1 ASC
;