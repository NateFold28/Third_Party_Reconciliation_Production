USE ROLE REACT_APP_ROLE;
USE DATABASE REACT_APP_DB;
USE SCHEMA APP_DATA;

CREATE SERVICE IF NOT EXISTS REACT_APP_DEMO
    IN COMPUTE POOL REACT_APP_POOL
    FROM SPECIFICATION $$
spec:
  containers:
    - name: backend
      image: vb54386-connectwise-ent.registry.snowflakecomputing.com/react_app_db/app_data/react_app_repo/react-app-demo-backend:latest
      env:
        SNOWFLAKE_ACCOUNT:   connectwise_ent.us-east-1
        SNOWFLAKE_DATABASE:  ANALYTICS
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

SHOW SERVICES LIKE 'REACT_APP_DEMO%';
CALL SYSTEM$GET_SERVICE_STATUS('REACT_APP_DB.APP_DATA.REACT_APP_DEMO');
SHOW ENDPOINTS IN SERVICE REACT_APP_DEMO;
