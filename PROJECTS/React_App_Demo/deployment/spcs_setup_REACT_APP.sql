-- =============================================================================
-- SPCS Setup — React_App_Demo (REACT_APP Configuration)
--
-- Your Configuration:
--   - Compute Pool:        REACT_APP_POOL
--   - Database:            REACT_APP_DB
--   - Role:                REACT_APP_ROLE
--   - Image Repo URL:      vb54386-connectwise-ent.registry.snowflakecomputing.com/react_app_db/app_data/react_app_repo
--
-- Status: BLOCKERS IDENTIFIED
--   [x] Compute pool exists
--   [x] Role + privileges confirmed by sysadmin
--   [ ] Docker images not yet built/pushed
--   [ ] Source data schema/location not yet confirmed
--
-- Next Actions:
--   1. Determine source data location (schema/tables the app will query)
--   2. Build and push Docker images using build_and_push.ps1
--   3. Run PART B below to create the service
--
-- =============================================================================


-- =============================================================================
-- PART A: PRE-FLIGHT CHECKS (Run now to validate access)
-- =============================================================================

-- A1. Verify compute pool exists and is accessible to REACT_APP_ROLE
USE ROLE REACT_APP_ROLE;
SHOW COMPUTE POOLS LIKE 'REACT_APP_POOL';
-- Expected: REACT_APP_POOL should appear in results
-- If not found: Escalate to sysadmin to verify creation and grants


-- A2. Verify database and schema access
USE DATABASE REACT_APP_DB;
SHOW SCHEMAS IN DATABASE REACT_APP_DB;
-- Review available schemas and decide which one the app will use
-- (e.g., PUBLIC, or a custom schema like REACT_APP_DEMO or APP_DATA)


-- A3. Verify role privileges for service creation
-- If the following succeeds, you have the right grants:
SHOW GRANTS TO ROLE REACT_APP_ROLE;
-- Look for: CREATE SERVICE, USAGE on REACT_APP_POOL, BIND SERVICE ENDPOINT
-- If any are missing: Escalate to sysadmin


-- A4. Verify image repository access (if it exists)
-- Run only if sysadmin has already created the repo:
SHOW IMAGE REPOSITORIES IN DATABASE REACT_APP_DB;
-- If repo doesn't exist yet, sysadmin creates it with ACCOUNTADMIN:
--   CREATE IMAGE REPOSITORY IF NOT EXISTS REACT_APP_DB.PUBLIC.REACT_APP_REPO;


-- =============================================================================
-- PART B: CREATE SERVICE (Run after images are built and pushed)
-- =============================================================================

-- B1. Switch to REACT_APP_ROLE and target database
USE ROLE REACT_APP_ROLE;
USE DATABASE REACT_APP_DB;
USE SCHEMA APP_DATA;  -- Service created in APP_DATA schema where repo lives

-- B2. Create the service
--     Images must exist in the registry before this step.
--     Replace <IMAGE_REPO_URL> with the full registry path.
--
-- BLOCKER: Images not yet built. This command will fail until:
--   1. build_and_push.ps1 is executed with your ImageRepoUrl
--   2. Both images are confirmed pushed to the registry
CREATE SERVICE IF NOT EXISTS REACT_APP_DEMO
    IN COMPUTE POOL REACT_APP_POOL
    FROM SPECIFICATION $$
    spec:
      containers:
        - name: backend
          image: vb54386-connectwise-ent.registry.snowflakecomputing.com/react_app_db/app_data/react_app_repo/react-app-demo-backend:latest
          env:
            SNOWFLAKE_ACCOUNT:   connectwise_ent.us-east-1
            SNOWFLAKE_DATABASE:  STREAMLIT_APPS
            SNOWFLAKE_SCHEMA:    DBO
            SNOWFLAKE_WAREHOUSE: CORTEX_WH
            SNOWFLAKE_ROLE:      REACT_APP_ROLE
          readinessProbe:
            port: 8000
            path: /health
        - name: frontend
          image: vb54386-connectwise-ent.registry.snowflakecomputing.com/react_app_db/app_data/react_app_repo/react-app-demo-frontend:latest
          readinessProbe:
            port: 80
            path: /
      endpoints:
        - name: ui
          port: 80
          public: true
    $$
    MIN_INSTANCES = 1
    MAX_INSTANCES = 1
    COMMENT = 'React App Demo — PoC for React vs Streamlit evaluation';


-- B3. Grant endpoint access
GRANT USAGE ON SERVICE REACT_APP_DEMO TO ROLE REACT_APP_ROLE;
GRANT SERVICE ROLE REACT_APP_DEMO!ALL_ENDPOINTS_USAGE TO ROLE REACT_APP_ROLE;


-- B4. Monitor service status (run after creation succeeds)
SHOW SERVICES LIKE 'REACT_APP_DEMO%';
CALL SYSTEM$GET_SERVICE_STATUS('REACT_APP_DB.APP_DATA.REACT_APP_DEMO');

-- View logs (if service is running)
CALL SYSTEM$GET_SERVICE_LOGS('REACT_APP_DB.APP_DATA.REACT_APP_DEMO', '0', 'backend',  100);
CALL SYSTEM$GET_SERVICE_LOGS('REACT_APP_DB.APP_DATA.REACT_APP_DEMO', '0', 'frontend', 100);

-- Get the public endpoint URL (once service is healthy)
SHOW ENDPOINTS IN SERVICE REACT_APP_DEMO;


-- =============================================================================
-- PART C: DATA ACCESS GRANTS (Required — source data is in ANALYTICS.DBO)
-- =============================================================================

-- The React app backend queries production-aligned Streamlit app tables:
--   STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
--   STREAMLIT_APPS.DBO.V5_SANDBOX_APP_BACKTEST
--   STREAMLIT_APPS.DBO.V5_SANDBOX_APP_RUNS
-- These grants may have already been assigned by your sysadmin.
-- Run them to verify/ensure REACT_APP_ROLE has SELECT access.

USE ROLE ACCOUNTADMIN;  -- May require elevated role to grant these

GRANT USAGE ON DATABASE STREAMLIT_APPS TO ROLE REACT_APP_ROLE;
GRANT USAGE ON SCHEMA STREAMLIT_APPS.DBO TO ROLE REACT_APP_ROLE;
GRANT SELECT ON TABLE STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL TO ROLE REACT_APP_ROLE;
GRANT SELECT ON TABLE STREAMLIT_APPS.DBO.V5_SANDBOX_APP_BACKTEST TO ROLE REACT_APP_ROLE;
GRANT SELECT ON TABLE STREAMLIT_APPS.DBO.V5_SANDBOX_APP_RUNS TO ROLE REACT_APP_ROLE;


-- =============================================================================
-- PART D: TEARDOWN (when done evaluating)
-- =============================================================================

-- DROP SERVICE IF EXISTS REACT_APP_DEMO;
-- DROP IMAGE REPOSITORY IF EXISTS REACT_APP_DB.PUBLIC.REACT_APP_REPO;
