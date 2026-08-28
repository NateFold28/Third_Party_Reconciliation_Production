-- =============================================================================
-- SPCS Setup — React_App_Demo
--
-- This file is split into two sections:
--   PART A — Run by Analytics Engineering (requires ACCOUNTADMIN or SYSADMIN)
--   PART B — Run by Nate Fold (requires DEVELOPER role or equivalent)
--
-- Compute pool confirmed: SYSTEM_COMPUTE_POOL_CPU (platform-managed, do not create/drop)
-- =============================================================================


-- =============================================================================
-- PART A: ANALYTICS ENGINEERING RUNS THIS (ACCOUNTADMIN required)
-- Send this section to engineering and ask them to run it once.
-- =============================================================================

USE ROLE ACCOUNTADMIN;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD;

-- ---------------------------------------------------------------------------
-- A1. Image repository
--     Create a repo for this app's container images.
--     After creation, run SHOW IMAGE REPOSITORIES and share the repo URL with Nate.
-- ---------------------------------------------------------------------------
CREATE IMAGE REPOSITORY IF NOT EXISTS ANALYTICS_DEV.DBT_NFOLD.REACT_APP_DEMO_REPO;

SHOW IMAGE REPOSITORIES IN SCHEMA ANALYTICS_DEV.DBT_NFOLD;

-- ---------------------------------------------------------------------------
-- A2. No secrets needed
--     The backend uses the SPCS-injected OAuth token (/snowflake/session/token)
--     to authenticate to Snowflake at runtime. All app users authenticate via
--     their existing Snowflake SSO login at the service endpoint.
--     No service account passwords or secrets are required.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- A3. Grant Nate's role the privileges needed to create and manage the service
-- ---------------------------------------------------------------------------
GRANT CREATE SERVICE ON SCHEMA ANALYTICS_DEV.DBT_NFOLD TO ROLE DEVELOPER;
GRANT USAGE ON COMPUTE POOL SYSTEM_COMPUTE_POOL_CPU TO ROLE DEVELOPER;
GRANT BIND SERVICE ENDPOINT ON ACCOUNT TO ROLE DEVELOPER;

-- ---------------------------------------------------------------------------
-- A4. Grant image repository access so images can be pushed and pulled
-- ---------------------------------------------------------------------------
GRANT READ, WRITE ON IMAGE REPOSITORY ANALYTICS_DEV.DBT_NFOLD.REACT_APP_DEMO_REPO TO ROLE DEVELOPER;

-- ---------------------------------------------------------------------------
-- A5. Grant the service's runtime role (DEVELOPER) access to query data
--     The SPCS OAuth token runs as the service owner role, so that role needs
--     SELECT on the underlying tables the app queries.
-- ---------------------------------------------------------------------------
GRANT USAGE ON DATABASE ANALYTICS TO ROLE DEVELOPER;
GRANT USAGE ON SCHEMA ANALYTICS.DBO TO ROLE DEVELOPER;
GRANT SELECT ON TABLE ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL TO ROLE DEVELOPER;


-- =============================================================================
-- PART B: NATE FOLD RUNS THIS (DEVELOPER role)
-- Run after Part A is complete and image repo URL + images are confirmed.
-- =============================================================================

USE ROLE DEVELOPER;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD;

-- ---------------------------------------------------------------------------
-- B1. Verify compute pool is accessible
-- ---------------------------------------------------------------------------
SHOW COMPUTE POOLS LIKE 'SYSTEM_COMPUTE_POOL_CPU';

-- ---------------------------------------------------------------------------
-- B2. Verify image repository and get URL for build_and_push.ps1
-- ---------------------------------------------------------------------------
SHOW IMAGE REPOSITORIES IN SCHEMA ANALYTICS_DEV.DBT_NFOLD;

-- ---------------------------------------------------------------------------
-- B3. Create the service
--     Before running: replace <IMAGE_REPO_URL> with actual URL from B2.
--     Example URL format:
--       <org>-<account>.registry.snowflakecomputing.com/analytics_dev/dbt_nfold/react_app_demo_repo
-- ---------------------------------------------------------------------------
CREATE SERVICE IF NOT EXISTS REACT_APP_DEMO
    IN COMPUTE POOL SYSTEM_COMPUTE_POOL_CPU
    FROM SPECIFICATION $$
    spec:
      containers:
        - name: backend
          image: <IMAGE_REPO_URL>/react-app-demo-backend:latest
          env:
            SNOWFLAKE_ACCOUNT:   connectwise_ent.us-east-1
            SNOWFLAKE_DATABASE:  ANALYTICS
            SNOWFLAKE_SCHEMA:    DBO
            SNOWFLAKE_WAREHOUSE: CORTEX_WH
            SNOWFLAKE_ROLE:      DEVELOPER
          readinessProbe:
            port: 8000
            path: /health
        - name: frontend
          image: <IMAGE_REPO_URL>/react-app-demo-frontend:latest
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

-- ---------------------------------------------------------------------------
-- B4. Grant endpoint access to consumers
-- ---------------------------------------------------------------------------
GRANT USAGE ON SERVICE REACT_APP_DEMO TO ROLE DEVELOPER;
GRANT SERVICE ROLE REACT_APP_DEMO!ALL_ENDPOINTS_USAGE TO ROLE DEVELOPER;

-- ---------------------------------------------------------------------------
-- B5. Monitor — run these to validate after service creation
-- ---------------------------------------------------------------------------
SHOW SERVICES LIKE 'REACT_APP_DEMO%';
CALL SYSTEM$GET_SERVICE_STATUS('ANALYTICS_DEV.DBT_NFOLD.REACT_APP_DEMO');
CALL SYSTEM$GET_SERVICE_LOGS('ANALYTICS_DEV.DBT_NFOLD.REACT_APP_DEMO', '0', 'backend',  100);
CALL SYSTEM$GET_SERVICE_LOGS('ANALYTICS_DEV.DBT_NFOLD.REACT_APP_DEMO', '0', 'frontend', 100);

-- Get public URL — share this with users once healthy
SHOW ENDPOINTS IN SERVICE REACT_APP_DEMO;

-- ---------------------------------------------------------------------------
-- B6. Teardown (when done evaluating)
-- ---------------------------------------------------------------------------
-- DROP SERVICE IF EXISTS REACT_APP_DEMO;
-- Do NOT drop SYSTEM_COMPUTE_POOL_CPU — it is platform-managed and shared.
-- DROP IMAGE REPOSITORY IF EXISTS ANALYTICS_DEV.DBT_NFOLD.REACT_APP_DEMO_REPO;
