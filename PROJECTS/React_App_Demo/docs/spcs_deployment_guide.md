# SPCS Deployment Guide - React_App_Demo

**Date:** 2026-06-05

## Objective
Deploy the React + FastAPI demo as a Snowflake Container Services (SPCS) service that can be shared broadly across the company.

## Architecture
```
Browser -> SPCS Public Endpoint (ui)
              -> Frontend container (nginx + React)
              -> Backend container (FastAPI)
              -> Snowflake SQL
```

## Current Status (Verified)
1. Local app runtime is working (`localhost:5173` + backend health on `localhost:8000`).
2. Snowflake SQL over SSO is working from laptop via Snowflake CLI in project venv.
3. Deployment is blocked locally by:
    - Docker Desktop admin install restriction
    - Missing `SYSADMIN` grant to current user

## Corporate-Friendly Rollout Model
Use a split-responsibility workflow:

1. Developer workflow (no admin rights required)
    - Build app code.
    - Validate local frontend/backend behavior.
    - Commit deployment specs and SQL.

2. Platform workflow (admin-capable identity)
    - Build and push images to Snowflake image repository.
    - Create compute pool, image repository, secrets, and service.
    - Grant endpoint usage to corporate consumer roles.

This avoids requiring Docker admin rights on every developer laptop.

## Required Tooling
1. Snowflake CLI path currently validated:
    - `c:/Users/Nate.Fold/projects/.venv/Scripts/snow.exe`
2. Docker Desktop on build runner or admin-enabled machine.

## Build and Push Images (platform-runner)
```powershell
cd PROJECTS\React_App_Demo
.\deployment\build_and_push.ps1 -ImageRepoUrl "<org-account>.registry.snowflakecomputing.com/analytics_dev/dbt_nfold/react_app_demo_repo"
```

## Deploy Service (Snowsight)
Run `deployment/spcs_setup.sql` section-by-section after replacing placeholders.

## Authentication for Backend Container
For production-friendly operation, prefer one of:
1. Key-pair/JWT service principal (recommended)
2. Service-account password secret (demo fallback)

Containerized SPCS backend should not rely on interactive SSO `externalbrowser` login.

## Corporate-Wide Access Grants
After service creation, grant access to a broad consumer role (example):
```sql
GRANT USAGE ON SERVICE REACT_APP_DEMO TO ROLE REACT_APP_USERS;
GRANT SERVICE ROLE REACT_APP_DEMO!ALL_ENDPOINTS_USAGE TO ROLE REACT_APP_USERS;
```
Then grant `REACT_APP_USERS` to your standard internal user roles per governance policy.

## Where to Find the App in Snowflake UI
### SQL-first (most reliable)
```sql
SHOW SERVICES LIKE 'REACT_APP_DEMO%';
SHOW ENDPOINTS IN SERVICE REACT_APP_DEMO;
```
Use the `INGRESS_URL` from `SHOW ENDPOINTS` as the app URL.

### Snowsight navigation
1. Open Snowsight.
2. Navigate to Data -> Databases -> `ANALYTICS_DEV` -> `DBT_NFOLD`.
3. Open the Services object list and select `REACT_APP_DEMO`.
4. Open Endpoints and copy the `ui` ingress URL.

## Operational Checks
```sql
CALL SYSTEM$GET_SERVICE_STATUS('ANALYTICS_DEV.DBT_NFOLD.REACT_APP_DEMO');
CALL SYSTEM$GET_SERVICE_LOGS('ANALYTICS_DEV.DBT_NFOLD.REACT_APP_DEMO', '0', 'backend', 100);
CALL SYSTEM$GET_SERVICE_LOGS('ANALYTICS_DEV.DBT_NFOLD.REACT_APP_DEMO', '0', 'frontend', 100);
```
