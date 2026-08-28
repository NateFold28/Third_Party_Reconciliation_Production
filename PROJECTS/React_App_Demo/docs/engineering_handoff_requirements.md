# React App Demo - Engineering Handoff Requirements

Date: 2026-06-08
Owner: Nate Fold

## Purpose
This document explains the current app architecture, what is already working, and exactly what Snowflake platform support is required to run the app in Snowflake.

## Executive Summary
- The app is currently a two-container web app:
  - Frontend: React static app served by nginx
  - Backend: FastAPI service that queries Snowflake
- Local development is working.
- Snowflake data connectivity from local backend is working.
- Production deployment target is Snowpark Container Services (SPCS).
- This implementation is not currently a Snowflake Native App package.

## Important Clarification: SPCS vs Native App
Both approaches exist, but they are different deployment models.

### Current implementation in this repo
- Uses containerized frontend + backend.
- Designed for SPCS service deployment with compute pool and image repository.
- Exposes a public endpoint for web access.

### Snowflake Native App model
- Packages SQL/artifacts and optional UI integrations for Snowsight distribution.
- Usually used for app distribution/installation model inside Snowflake accounts.
- Can include a React UI, but packaging and deployment workflow differs from this repo's current container workflow.

Conclusion:
- Native App does not replace SPCS for this repo as currently built.
- We can migrate to Native App later if distribution/governance goals require it.

## What Is Already Working
- Local frontend loads and calls backend API routes.
- Backend runs and returns health endpoint.
- Backend can authenticate and query Snowflake from local environment.

## What We Need From Snowflake Platform Engineering

### 1. Confirm or provide SPCS infrastructure
- Target compute pool name (existing preferred)
- Target database and schema for service objects
- Target image repository URL
- Confirmation whether service object already exists

### 2. Role and privilege access needed for deployment owner
- Ability to use compute pool
- Ability to push/pull images in target image repository
- Ability to create/alter/drop service in target schema
- Ability to create/use secrets used by service
- Ability to view service status, logs, and endpoints

### 3. Runtime authentication pattern for backend container
Choose one:
- Preferred long-term: service principal with key-pair/JWT
- Acceptable short-term: service account username/password via Snowflake secrets

### 4. Image build and push ownership
Decide who builds/pushes container images:
- Option A: Platform CI runner builds and pushes
- Option B: Admin-enabled machine builds and pushes

Note: Docker is required somewhere to build OCI images, but does not need to be installed on every developer laptop.

## Required Snowflake Objects (Target State)
- Compute pool for service runtime
- Image repository containing:
  - react-app-demo-frontend image
  - react-app-demo-backend image
- Secrets for backend runtime credentials
- Service object with frontend and backend containers
- Endpoint exposure and grants for consumer role(s)

## Required Inputs to Complete Deployment
- Compute pool name
- Image repository URL
- Snowflake account identifier for backend env vars
- Warehouse, database, schema, and role values for backend runtime
- Secret names and credential values (managed by platform)
- Consumer role(s) that need endpoint access

## Validation Commands After Deployment
- Show service:
  - SHOW SERVICES LIKE 'REACT_APP_DEMO%';
- Check endpoint URL:
  - SHOW ENDPOINTS IN SERVICE REACT_APP_DEMO;
- Check health/logs:
  - CALL SYSTEM$GET_SERVICE_STATUS('ANALYTICS_DEV.DBT_NFOLD.REACT_APP_DEMO');
  - CALL SYSTEM$GET_SERVICE_LOGS('ANALYTICS_DEV.DBT_NFOLD.REACT_APP_DEMO', '0', 'backend', 100);
  - CALL SYSTEM$GET_SERVICE_LOGS('ANALYTICS_DEV.DBT_NFOLD.REACT_APP_DEMO', '0', 'frontend', 100);

## Risks / Blockers to Resolve
- Missing deployment privileges (for compute pool, repository, service, secrets)
- Image build/push ownership not assigned
- Runtime auth method not finalized
- Endpoint grants not mapped to final user roles

## Ask to Engineering Team
Please confirm the following so deployment can proceed:
1. Which compute pool and image repository should this app use?
2. Who will own image build/push for this app?
3. Which role will own service deployment and operations?
4. Which auth pattern should backend container use in production?
5. Which consumer role(s) should receive endpoint access grants?

## Recommendation
Proceed with SPCS for this phase to get production hosting live quickly with the existing architecture. Re-evaluate Native App packaging later only if we need Snowflake app distribution semantics across accounts.
