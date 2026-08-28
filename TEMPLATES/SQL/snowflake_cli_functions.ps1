Set-StrictMode -Version Latest

$script:SnowCliPath = $null

function Get-ProjectRoot {
    param(
        [string]$StartPath = $PSScriptRoot
    )

    $current = Resolve-Path -Path $StartPath
    while ($null -ne $current) {
        if (Test-Path (Join-Path $current.Path ".venv")) {
            return $current.Path
        }

        $parent = Split-Path -Path $current.Path -Parent
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current.Path) {
            break
        }
        $current = Resolve-Path -Path $parent
    }

    throw "Could not find project root containing .venv"
}

function Get-ProjectVenvPython {
    param(
        [string]$ProjectRoot
    )

    $pythonPath = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"
    if (-not (Test-Path $pythonPath)) {
        throw "Missing virtual environment interpreter: $pythonPath. Create it with: python -m venv .venv"
    }

    return $pythonPath
}

function Ensure-ProjectVenv {
    param(
        [string]$ProjectRoot = (Get-ProjectRoot)
    )

    $pythonPath = Get-ProjectVenvPython -ProjectRoot $ProjectRoot
    & $pythonPath -m pip show snowflake-cli-labs *> $null
    if ($LASTEXITCODE -ne 0) {
        & $pythonPath -m pip install --upgrade pip snowflake-cli-labs
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install snowflake-cli-labs in project venv"
        }
    }

    return $pythonPath
}

function Get-SnowCliPath {
    param(
        [string]$ProjectRoot = (Get-ProjectRoot)
    )

    $snowPath = Join-Path $ProjectRoot ".venv\\Scripts\\snow.exe"
    if (-not (Test-Path $snowPath)) {
        Ensure-ProjectVenv -ProjectRoot $ProjectRoot | Out-Null
    }

    if (-not (Test-Path $snowPath)) {
        throw "Snowflake CLI executable not found at: $snowPath"
    }

    return $snowPath
}

function Set-SnowflakeAlias {
    param(
        [string]$ProjectRoot = (Get-ProjectRoot)
    )

    $script:SnowCliPath = Get-SnowCliPath -ProjectRoot $ProjectRoot
    function global:snowflake {
        param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
        & $script:SnowCliPath @Args
    }
}

function Test-SnowflakeConnection {
    param(
        [string]$ConnectionName = "default",
        [string]$ProjectRoot = (Get-ProjectRoot)
    )

    $snowPath = Get-SnowCliPath -ProjectRoot $ProjectRoot
    if ($ConnectionName -eq "default") {
        & $snowPath connection test
    }
    else {
        & $snowPath connection test -c $ConnectionName
    }
}

function Invoke-SnowflakeQuery {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Query,
        [string]$ConnectionName = "default",
        [ValidateSet("TABLE", "JSON", "JSON_EXT", "CSV")]
        [string]$OutputFormat = "TABLE",
        [string]$ProjectRoot = (Get-ProjectRoot)
    )

    $snowPath = Get-SnowCliPath -ProjectRoot $ProjectRoot

    if ($ConnectionName -eq "default") {
        & $snowPath sql --format $OutputFormat -q $Query
    }
    else {
        & $snowPath sql -c $ConnectionName --format $OutputFormat -q $Query
    }
}

function Initialize-SnowflakeSqlEnvironment {
    param(
        [string]$ProjectRoot = (Get-ProjectRoot)
    )

    Ensure-ProjectVenv -ProjectRoot $ProjectRoot | Out-Null
    Set-SnowflakeAlias -ProjectRoot $ProjectRoot

    Write-Host "Snowflake SQL environment ready"
    Write-Host "Project root: $ProjectRoot"
    Write-Host "Use: snowflake --version"
    Write-Host 'Use: Invoke-SnowflakeQuery -Query "SELECT CURRENT_USER();"'
}
