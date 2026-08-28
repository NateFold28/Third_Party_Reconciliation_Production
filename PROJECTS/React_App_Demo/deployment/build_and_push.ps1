<#
.SYNOPSIS
    Build Docker images and push them to the Snowflake image repository.

.DESCRIPTION
    1. Authenticate Docker to the Snowflake image registry.
    2. Build the backend (FastAPI) and frontend (React/nginx) images.
    3. Push both images.

.NOTES
    Pre-requisites:
      - Docker Desktop running
      - Snowflake CLI (snow) installed and configured (snow --version)
      - Run: SHOW IMAGE REPOSITORIES in Snowsight to get IMAGE_REPO_URL
        Example: <org>-<account>.registry.snowflakecomputing.com/analytics_dev/dbt_nfold/react_app_demo_repo

.PARAMETER ImageRepoUrl
    The Snowflake image repository URL (without trailing slash).
    e.g. "myorg-myaccount.registry.snowflakecomputing.com/analytics_dev/dbt_nfold/react_app_demo_repo"

.PARAMETER Tag
    Image tag. Defaults to "latest".
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$ImageRepoUrl,

    [string]$Tag = "latest"
)

$ProjectRoot  = Join-Path $PSScriptRoot ".."
$BackendDir   = Join-Path $ProjectRoot "app\backend"
$FrontendDir  = Join-Path $ProjectRoot "app\frontend"
$BackendImage = "${ImageRepoUrl}/react-app-demo-backend:${Tag}"
$FrontendImage= "${ImageRepoUrl}/react-app-demo-frontend:${Tag}"

Write-Host "=== Authenticating Docker to Snowflake registry ==="
snow spcs image-registry login
if ($LASTEXITCODE -ne 0) { Write-Error "Registry login failed"; exit 1 }

Write-Host ""
Write-Host "=== Building backend image ==="
# Build context is project root so the Dockerfile can access both app/backend/ and sql/
docker build --platform linux/amd64 -t $BackendImage -f "$BackendDir\Dockerfile" $ProjectRoot
if ($LASTEXITCODE -ne 0) { Write-Error "Backend build failed"; exit 1 }

Write-Host ""
Write-Host "=== Building frontend image ==="
docker build --platform linux/amd64 -t $FrontendImage $FrontendDir
if ($LASTEXITCODE -ne 0) { Write-Error "Frontend build failed"; exit 1 }

Write-Host ""
Write-Host "=== Pushing images ==="
docker push $BackendImage
docker push $FrontendImage

Write-Host ""
Write-Host "Done. Images pushed:"
Write-Host "  $BackendImage"
Write-Host "  $FrontendImage"
Write-Host ""
Write-Host "Next: fill in <IMAGE_REPO_URL> in deployment/spcs_setup.sql and run in Snowsight."
