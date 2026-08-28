# Analysis Contract — React_App_Demo

**Locked:** [x] yes
**Date:** 2026-06-05
**Owner:** Nate

## Raw Ask
Build a React proof-of-concept app deployed on Snowflake SPCS. App displays renewal ATR/Actuals data with date filters. Goal: evaluate React vs Streamlit for team-facing internal apps, demonstrate SPCS build/deploy process.

## Decision Statement
> After seeing this work, [Nate / team] will decide whether to adopt React on SPCS as the preferred internal app platform over Streamlit.

## Primary KPI
- **Is React on SPCS feasible?** Measured by: app deployed, data loads, date filter works, build process documented.
- Grain: one proof-of-concept service
- Directionality: higher feasibility = proceed
- Tolerance for error: PoC standard — data accuracy secondary to plumbing demo

## Secondary Diagnostics
1. Time from code to live URL (build + push + deploy)
2. Dev experience vs Streamlit (subjective, document in notes after build)
3. Data freshness path (live query vs precomputed table trade-off)

## Time Logic
- Observation window: 2026 fiscal year (Jan–Dec 2026)
- Forecast horizon: none — reporting only
- Label maturity rule: n/a

## Delivery Target
- Format: [x] app (React on SPCS)
- Audience: internal team evaluation

## Execution Constraints
- Production execution path: Snowflake SPCS (Docker containers)
- Local dev/SSO blocked for Python — backend uses service account creds injected via Snowflake secrets in SPCS
- Docker + Snowflake CLI (snow) required for image build and push

## Known Non-Goals
- Not a production dashboard — PoC only
- No auth layer in v1 (SPCS ingress handles authentication)
- No precomputed tables — queries run live against ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL