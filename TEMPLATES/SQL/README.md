# SQL Templates

This folder provides reusable PowerShell functions for Snowflake CLI workflows.

## Files

- `snowflake_cli_functions.ps1`: reusable functions to ensure venv + Snowflake CLI + query execution.

## One-time setup per terminal

```powershell
. .\TEMPLATES\SQL\snowflake_cli_functions.ps1
Initialize-SnowflakeSqlEnvironment
```

## Common commands

```powershell
snowflake --version
snowflake connection list
Test-SnowflakeConnection
Invoke-SnowflakeQuery -Query "SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE();"
```

## Function reference

- `Get-ProjectRoot`: finds repo root by scanning upward for `.venv`.
- `Ensure-ProjectVenv`: validates `.venv` and installs `snowflake-cli-labs` if missing.
- `Get-SnowCliPath`: resolves `.venv\\Scripts\\snow.exe` path.
- `Set-SnowflakeAlias`: exposes `snowflake` command in current PowerShell session.
- `Test-SnowflakeConnection`: runs `snow connection test` for default or named profile.
- `Invoke-SnowflakeQuery`: runs SQL from terminal with optional named connection and output format.
- `Initialize-SnowflakeSqlEnvironment`: bootstrap helper for everything above.
