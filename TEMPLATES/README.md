# Templates Wiring Guide

Use this guide as the single source of truth for running Snowflake SQL queries and Python scripts from this repo.

## Locations

- Python templates: `.\TEMPLATES\Python`
- SQL templates: `.\TEMPLATES\SQL`

## Python path (direct script execution)

Always run Python with the project virtual environment interpreter:

```powershell
.\.venv\Scripts\python.exe .\TEMPLATES\Python\snowflake_analysis_template.py
```

Reusable Python connector helpers are in `.\TEMPLATES\Python\connection.py`.

## SQL path (direct query execution)

Load reusable SQL helper functions once per PowerShell terminal:

```powershell
. .\TEMPLATES\SQL\snowflake_cli_functions.ps1
Initialize-SnowflakeSqlEnvironment
```

Then run queries directly:

```powershell
Invoke-SnowflakeQuery -Query "SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE();"
```

Or use the alias directly:

```powershell
snowflake sql -q "SELECT CURRENT_ACCOUNT();"
```

## What is guaranteed by these templates

- Python scripts always run from `.venv`.
- Snowflake Python connector can be auto-installed if missing.
- Snowflake CLI can be auto-installed in `.venv` if missing.
- SQL queries can be executed consistently from terminal with one helper call.

## Recommended startup command block

From repo root in a new terminal:

```powershell
. .\TEMPLATES\SQL\snowflake_cli_functions.ps1
Initialize-SnowflakeSqlEnvironment
snowflake connection test
```

Then run either:

- SQL directly via `Invoke-SnowflakeQuery`
- Python directly via `.\.venv\Scripts\python.exe <script.py>`
