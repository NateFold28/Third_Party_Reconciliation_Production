# React App SPCS Deployment Roadmap

**Created:** 2026-06-10  
**Config:** REACT_APP_POOL, REACT_APP_DB, REACT_APP_ROLE  
**Status:** Pre-flight validation phase

---

## What You Can Do Right Now (No Docker Required)

1. **Run PART A pre-flight checks** in Snowsight:
   - Verify compute pool `REACT_APP_POOL` exists
   - Verify database `REACT_APP_DB` is accessible
   - Verify your role grants include `CREATE SERVICE`, `BIND SERVICE ENDPOINT`
   - **File:** `deployment/spcs_setup_REACT_APP.sql` — run A1–A4 sections

2. **Run PART C data access grants** (if not already granted):
   - The app queries: `ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL`
   - Verify `REACT_APP_ROLE` has SELECT on that table
   - **File:** `deployment/spcs_setup_REACT_APP.sql` — run PART C

---

## Critical Blockers (Cannot Proceed Without)

### ❌ Docker Images Not Built
The backend and frontend containers must be built and pushed to the registry before the service can start.

**Blocked until:**
- You build images using `build_and_push.ps1`
- Images are pushed to: `vb54386-connectwise-ent.registry.snowflakecomputing.com/react_app_db/app_data/react_app_repo`

**Command (once Docker is available):**
```powershell
.\deployment\build_and_push.ps1 -ImageRepoUrl "vb54386-connectwise-ent.registry.snowflakecomputing.com/react_app_db/app_data/react_app_repo"
```

---

## Next Steps (In Order)

1. ✅ **Run PART A checks** to validate role/compute pool access
2. ✅ **Run PART C checks** to verify `REACT_APP_ROLE` has SELECT on `ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL`
3. ❌ **Build and push images** (requires Docker; may need admin or CI runner)
4. ✅ **Run PART B** to create the service
5. ✅ **Monitor** service status and get public URL
6. ✅ **Share** the endpoint URL with internal users

---

## Questions Answered ✅

| Question | Answer |
|----------|--------|
| Source database | `ANALYTICS` |
| Source schema | `DBO` |
| Source table | `CARR__RENEWALS_PORTFOLIO_LVL` |
| Schema inside REACT_APP_DB | Service created in `PUBLIC` (REACT_APP_DB not used for queries) |

---

## Files in This Repo

| File | Purpose | Status |
|------|---------|--------|
| `deployment/spcs_setup_REACT_APP.sql` | Customized SQL for your config | Ready to validate |
| `deployment/build_and_push.ps1` | Build and push images | Blocked — Docker/images |
| `app/backend/main.py` | FastAPI server | Ready |
| `app/frontend/` | React + Vite app | Ready |
| `sql/` | Query templates | Ready |

---

## Running PART A + C Now

In Snowsight, run these sections one at a time:

**PART A — Pre-flight checks:**
```sql
-- A1. Check compute pool
USE ROLE REACT_APP_ROLE;
SHOW COMPUTE POOLS LIKE 'REACT_APP_POOL';

-- A2. Check database access
USE DATABASE REACT_APP_DB;
SHOW SCHEMAS IN DATABASE REACT_APP_DB;

-- A3. Check role grants
SHOW GRANTS TO ROLE REACT_APP_ROLE;

-- A4. Check image repo (if sysadmin created it)
SHOW IMAGE REPOSITORIES IN DATABASE REACT_APP_DB;
```

**PART C — Verify data access:**
```sql
-- These may already be granted; run to confirm
GRANT USAGE ON DATABASE ANALYTICS TO ROLE REACT_APP_ROLE;
GRANT USAGE ON SCHEMA ANALYTICS.DBO TO ROLE REACT_APP_ROLE;
GRANT SELECT ON TABLE ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL TO ROLE REACT_APP_ROLE;
```

If all checks pass, you're ready for the next phase.
