# React_App_Demo

**Created:** 2026-06-05  
**Type:** app-backed (SPCS)  
**Owner:** Nate  

## Business Ask
Proof-of-concept React app deployed on Snowflake Container Services (SPCS).  
Displays renewal ATR / Actuals data with date filters across three views.  
Goal: evaluate React on SPCS vs Streamlit for internal team apps.

## Repo Map

| Path | Purpose |
|------|---------|
| docs/ | Stage docs, analysis contract, data landscape, open questions |
| sql/ | Source queries (monthly_summary, segment_rollup, portfolio_rollup) |
| app/backend/ | FastAPI Python server — queries Snowflake, serves JSON |
| app/frontend/ | React + Vite app — date filters, 3 tab views, Recharts chart |
| app/docker-compose.yml | Local dev: spins up both containers |
| deployment/spcs_setup.sql | SPCS compute pool, image repo, secrets, service spec |
| deployment/build_and_push.ps1 | Build Docker images and push to Snowflake registry |
| tests/ | Validation queries |

## Quick Start — Local Dev

```powershell
# 1. Fill in Snowflake creds
Copy-Item app\backend\.env.example app\backend\.env
# Edit app\backend\.env with your account/user/password

# 2. Start both containers
cd app
docker-compose up

# 3. Open the app
Start-Process http://localhost:3000

# 4. Test the API directly
Invoke-RestMethod http://localhost:8000/api/monthly-summary
```

## Deploy to SPCS

```powershell
# 1. Build and push images
.\deployment\build_and_push.ps1 -ImageRepoUrl "<your_repo_url>"

# 2. Run deployment/spcs_setup.sql in Snowsight (section by section)

# 3. Get the public URL
# SHOW ENDPOINTS IN SERVICE REACT_APP_DEMO;
```

## Deployment Reality on Corporate Laptops

1. If Docker install is blocked by admin policy, image build/push must run on:
  - a CI runner, or
  - an admin-enabled platform machine.
2. Snowflake CLI is still usable from this repo venv:
  - `c:/Users/Nate.Fold/projects/.venv/Scripts/snow.exe`

## Where to Find the App in Snowflake UI

Run in Snowsight worksheet:

```sql
SHOW SERVICES LIKE 'REACT_APP_DEMO%';
SHOW ENDPOINTS IN SERVICE REACT_APP_DEMO;
```

Use the `INGRESS_URL` from `SHOW ENDPOINTS`.

Snowsight path:
1. Data -> Databases -> ANALYTICS_DEV -> DBT_NFOLD
2. Services -> REACT_APP_DEMO
3. Endpoints -> copy `ui` ingress URL

## Architecture

```
Browser
  └─► SPCS Ingress (public endpoint)
        ├─► frontend container (nginx, port 80)  — serves React SPA
        └─► backend  container (FastAPI, port 8000) — queries Snowflake
```

## Current State
See [docs/current_state.md](docs/current_state.md) for latest status and blockers.