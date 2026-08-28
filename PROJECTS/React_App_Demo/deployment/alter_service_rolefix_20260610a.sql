USE ROLE REACT_APP_ROLE;
USE DATABASE REACT_APP_DB;
USE SCHEMA APP_DATA;

ALTER SERVICE REACT_APP_DEMO
  FROM SPECIFICATION $$
spec:
  containers:
    - name: backend
      image: vb54386-connectwise-ent.registry.snowflakecomputing.com/react_app_db/app_data/react_app_repo/react-app-demo-backend:rolefix-20260610a
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
      image: vb54386-connectwise-ent.registry.snowflakecomputing.com/react_app_db/app_data/react_app_repo/react-app-demo-frontend:prodsync-20260610
      readinessProbe:
        port: 80
        path: /
  endpoints:
    - name: ui
      port: 80
      public: true
$$;

CALL SYSTEM$GET_SERVICE_STATUS('REACT_APP_DB.APP_DATA.REACT_APP_DEMO');
