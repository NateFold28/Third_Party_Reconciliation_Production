-- React_App_Demo â€” Validation Checks
-- Run in Snowsight before every publish
USE ROLE DEVELOPER;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD;

-- Row count
SELECT COUNT(*) AS row_count FROM /* your_table */;
