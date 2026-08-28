-- Monthly Summary: ATR, Actuals, Actual Percent
-- Parameterized by :start_date / :end_date (FastAPI will bind these)

SELECT
    DATE_TRUNC('MONTH', MASTER_DATE)::DATE AS RENEWAL_MONTH,
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
ORDER BY 1 ASC
;
