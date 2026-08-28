# Current State — React_App_Demo

**Last Updated:** 2026-07-09

## ⚠️ The blockers below are STALE — SPCS deployment SUCCEEDED
As of 2026-07-09 the full SPCS stack is **live and verified in Snowflake**:
- Compute pool `REACT_APP_POOL` — **ACTIVE**
- Image repository `REACT_APP_REPO` — exists (`vb54386-connectwise-ent.registry.snowflakecomputing.com/react_app_db/app_data/react_app_repo`)
- Service `REACT_APP_DB.APP_DATA.REACT_APP_DEMO` — **RUNNING**
- Owner role `REACT_APP_ROLE` has `CREATE SERVICE` / `BIND SERVICE ENDPOINT` / compute-pool USAGE

So Docker + SYSADMIN were resolved and the PoC deployed. The "React vs Streamlit"
evaluation concluded **React is viable on SPCS** and is the chosen path for the
**Renewal Risk** surveillance app, whose own React frontend + FastAPI backend now live at
`PROJECTS/Renewal_Risk_System/app/` (reusing this repo's proven pattern, pool, and repo).
The sections below are kept only as the original build log.

## Stage
12 — Local app validated; Snowflake deployment SUCCEEDED (see note above)

## Working Hypothesis
React + FastAPI on SPCS is viable for internal dashboards. The two-container topology
(frontend nginx + backend FastAPI) maps cleanly to the SPCS spec format.

## Latest Gate Decision
- Stage 0 contract: locked
- Stage 11 scaffold: complete
- Stage 12 local runtime + Snowflake CLI path: complete

## Verified in This Session
1. Local frontend reachable on port 5173.
2. Local backend reachable on port 8000 (`/health` returned `{"status":"ok"}`).
3. Snowflake CLI available via project venv path:
   - `c:/Users/Nate.Fold/projects/.venv/Scripts/snow.exe`
4. SSO SQL execution works from laptop with `externalbrowser` authenticator.

## Confirmed Blockers
1. Docker Desktop install requires admin elevation on this corporate laptop.
2. `SYSADMIN` role not granted to current user, so compute pool/image repository cannot be created from this identity.
3. No existing SPCS objects were found:
   - `REACT_APP_DEMO_POOL` compute pool: not found
   - `REACT_APP_DEMO_REPO` image repository: not found
   - `REACT_APP_DEMO` service: not found

## Known Blockers
1. Docker Desktop admin install approval (or CI runner with Docker).
2. Snowflake platform admin role (`SYSADMIN` or delegated equivalent) required to create compute pool + repository + service.
3. Backend container auth decision required for production:
   - preferred: key-pair/JWT service principal
   - acceptable demo fallback: service-account password secret

## Next Recommended Action
1. Platform admin (or CI runner) builds and pushes images to Snowflake registry.
2. Platform admin runs `deployment/spcs_setup.sql` with real values and non-placeholder image URL.
3. Grant endpoint usage to corporate consumer role(s).
4. Validate app URL with:
   - `SHOW ENDPOINTS IN SERVICE REACT_APP_DEMO;`
5. Share ingress URL internally.