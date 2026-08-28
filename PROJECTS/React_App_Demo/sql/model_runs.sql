-- Model Runs: champion + recent run history
-- Source: V5_SANDBOX_APP_RUNS (mirrors render_model_performance runs table)

SELECT
    RUN_ID                                                                            AS run_id,
    RUN_TIMESTAMP::DATE                                                               AS run_timestamp,
    COALESCE(METHOD, 'CHURN_ADJUSTED')                                                AS method,
    COALESCE(N_CONTRACTS, 0)                                                          AS n_contracts,
    COALESCE(FORECAST_RATE_PCT, 0)                                                    AS forecast_rate_pct,
    COALESCE(IS_CHAMPION, FALSE)                                                       AS is_champion,
    COALESCE(CHAMPION_GATE_PASSED, FALSE)                                              AS champion_gate_passed,
    NOTES
FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_RUNS
ORDER BY RUN_TIMESTAMP DESC
LIMIT 20
;
