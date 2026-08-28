"""
Production V5 Pipeline - SIMPLIFIED EXECUTION SCRIPT

This script prepares everything for you to run in Snowsight with copy/paste.

No local Snowflake connection needed — just copy/paste SQL into Snowsight.
"""

import sys
from pathlib import Path
from datetime import datetime

AUDIT_SQL = Path('PROJECTS/Production_Renewal_Forecasting_Pipeline/sql/audit/PROD_V1_MASTER_AUDIT.sql')
SCHEDULER_SQL = Path('PROJECTS/Production_Renewal_Forecasting_Pipeline/scheduler/production_v5_tasks.sql')

def print_header(title):
    """Print formatted header."""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}\n")

def print_step(num, title):
    """Print step header."""
    print(f"\n{'─'*80}")
    print(f"STEP {num}: {title}")
    print(f"{'─'*80}\n")

def main():
    """Execute simplified audit runner."""
    
    print_header("PRODUCTION V5 PIPELINE - EXECUTION GUIDE")
    
    print("""
✅ READY TO EXECUTE

Your production pipeline audit is fully prepared. Here's what to do:
""")
    
    # STEP 1: Deploy scheduled tasks
    print_step(1, "DEPLOY SCHEDULED TASKS (5 min)")
    
    print("""
📍 Location: Snowsight → New Query Tab

1. Copy the entire scheduler file below
2. Paste into Snowsight
3. Click "Run All"
4. Expected result: "All scheduled tasks created (SUSPENDED)"

⚠️  IMPORTANT: Tasks are created SUSPENDED for safety. Do NOT resume yet.

─────────────────────────────────────────────────────────────────────────

""")
    
    # Show scheduler file
    print("SCHEDULER FILE TO COPY/PASTE:\n")
    print("📄 File: scheduler/production_v5_tasks.sql\n")
    
    with open(SCHEDULER_SQL, 'r') as f:
        scheduler_content = f.read()
    
    print("```sql")
    print(scheduler_content[:2000])
    print("\n... (paste the full file)")
    print("```\n")
    
    print(f"""
Full file ready to copy: {SCHEDULER_SQL}

What this does:
  ✓ Creates 6 scheduled tasks (all SUSPENDED for safety)
  ✓ Daily tasks: refresh (07:30), forecast snapshot (07:35), EOM snapshot (07:40), reconciliation (06:30 UTC)
  ✓ Monthly tasks: retrain (1st @ 07:30), model registry (1st @ 08:10)
  
Expected output:
  Task V5_SANDBOX_DAILY_REFRESH_TASK created.
  Task V5_SANDBOX_MONTHLY_MODEL_TASK created.
  Task V5_SANDBOX_FORECAST_SNAPSHOT_TASK created.
  Task V5_SANDBOX_EOM_SNAPSHOT_TASK created.
  Task V5_MONTHLY_MODEL_REGISTRY_TASK created.
  Task TASK_RECONCILIATION_DAILY created.

═══════════════════════════════════════════════════════════════════════════
""")
    
    # STEP 2: Run master audit
    print_step(2, "RUN MASTER AUDIT (45 min total)")
    
    print("""
📍 Location: Snowsight → New Query Tab

1. Copy the entire master audit script below
2. Paste into Snowsight
3. Click "Run All"
4. Wait ~45 minutes (includes full pipeline execution)
5. Review each section output

Expected results by section:
  ✓ Section 1: All 10 procedures → OK
  ✓ Section 2: All 15 tables → OK
  ✓ Section 3: Data freshness → recent dates (0-2 days)
  ✓ Section 4: Recent runs → successful statuses
  ✓ Section 5: Task status → STARTED
  ✓ Section 6: Guard check → completes without errors
  ✓ Section 7: Pipeline execution → "OK | ..." message
  ✓ Section 8: Post-validation → rates 70-78%, AUC > 0.76

─────────────────────────────────────────────────────────────────────────

""")
    
    # Show audit file
    print("AUDIT FILE TO COPY/PASTE:\n")
    print("📄 File: sql/audit/PROD_V1_MASTER_AUDIT.sql\n")
    
    with open(AUDIT_SQL, 'r') as f:
        audit_content = f.read()
    
    print("```sql")
    print(audit_content[:2000])
    print("\n... (paste the full file)")
    print("```\n")
    
    print(f"""
Full file ready to copy: {AUDIT_SQL}

What this does:
  ✓ Validates all objects exist (procedures, tables)
  ✓ Checks data freshness
  ✓ Reviews recent pipeline runs
  ✓ Validates task configuration
  ✓ EXECUTES FULL PIPELINE (30-45 min)
  ✓ Validates post-execution results
  ✓ Generates summary report

═══════════════════════════════════════════════════════════════════════════
""")
    
    # STEP 3: Resume tasks (optional)
    print_step(3, "RESUME TASKS - OPTIONAL (run ONLY if audit passes)")
    
    print("""
Once audit completes successfully:

📍 Location: Snowsight → New Query Tab

Paste these commands to RESUME the scheduled tasks:

```sql
USE ROLE STREAMLIT_USER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE STREAMLIT_APPS;
USE SCHEMA DBO;

-- Resume all scheduled tasks
ALTER TASK V5_SANDBOX_DAILY_REFRESH_TASK         RESUME;
ALTER TASK V5_SANDBOX_MONTHLY_MODEL_TASK         RESUME;
ALTER TASK V5_SANDBOX_FORECAST_SNAPSHOT_TASK     RESUME;
ALTER TASK V5_SANDBOX_EOM_SNAPSHOT_TASK          RESUME;
ALTER TASK V5_MONTHLY_MODEL_REGISTRY_TASK        RESUME;
ALTER TASK TASK_RECONCILIATION_DAILY             RESUME;

-- Confirm tasks are running
SHOW TASKS IN SCHEMA STREAMLIT_APPS.DBO;
```

This starts the automatic daily/monthly pipeline execution.

═══════════════════════════════════════════════════════════════════════════
""")
    
    # STEP 4: Validate app
    print_step(4, "VALIDATE STREAMLIT APP (5 min)")
    
    print("""
In Snowflake Streamlit Editor:

1. Open the Production_Forecast_App_V2 app
2. Hard refresh (Ctrl+Shift+R)
3. Check for errors (should load cleanly)
4. Test navigation:
   - Click "Open Renewals" tab → should show contracts
   - Click "All Renewals" tab → should show forecast + actuals
   - Click "Model Performance" tab → should show backtest results
5. Verify forward rates are 70-78%

═══════════════════════════════════════════════════════════════════════════
""")
    
    # Summary
    print_header("QUICK SUMMARY")
    
    print(f"""
Total execution time: ~50 minutes

Timeline:
  Step 1 (Deploy tasks)      →  5 min    (FAST)
  Step 2 (Run audit)         → 45 min    (includes pipeline)
  Step 3 (Resume tasks)      →  1 min    (FAST)
  Step 4 (Test app)          →  5 min    (FAST)
                               ────────
                               ~56 min total

What to do RIGHT NOW:

1️⃣  COPY scheduler file to Snowsight
   Source: {SCHEDULER_SQL}
   Paste into Snowsight → Run All
   Expected: 6 tasks created

2️⃣  COPY master audit to Snowsight
   Source: {AUDIT_SQL}
   Paste into Snowsight → Run All
   Wait ~45 minutes

3️⃣  Review audit results
   All sections should show OK/PASS
   If any FAIL, see troubleshooting below

4️⃣  Resume tasks (if audit passes)
   Run the STEP 3 commands in Snowsight

5️⃣  Test Streamlit app
   Hard refresh and verify navigation

═══════════════════════════════════════════════════════════════════════════
""")
    
    # Troubleshooting
    print_header("TROUBLESHOOTING")
    
    print("""
If Section 7 (Pipeline Execution) FAILS:

  FAIL @ step=build_feature_store
    → CARR upstream data is stale
    → Fix: Wait for AE dbt job to complete (finishes ~07:00 ET)
    → Re-run: CALL SP_V5_SANDBOX_RUN_PIPELINE();

  FAIL @ step=train_unified
    → Snowpark Python memory error
    → Fix: Scale warehouse: ALTER WAREHOUSE REPORTING_WH SET WAREHOUSE_SIZE='XLARGE';
    → Re-run: CALL SP_V5_SANDBOX_RUN_PIPELINE();

  Any other FAIL
    → Check V5_PIPELINE_RUN_LOG for details
    → Run: SELECT * FROM STREAMLIT_APPS.DBO.V5_PIPELINE_RUN_LOG ORDER BY TRIGGERED_AT DESC LIMIT 10;
    → Fix the underlying issue and re-run

═══════════════════════════════════════════════════════════════════════════
""")
    
    # Final instructions
    print_header("READY TO BEGIN?")
    
    print("""
✅ Everything is prepared. Just follow the 4 steps above.

🎯 Next action: Copy scheduler file to Snowsight and run it.

📧 Questions? Check AUDIT_AND_DEPLOYMENT_CHECKLIST.md for detailed guide.

═══════════════════════════════════════════════════════════════════════════
""")

if __name__ == '__main__':
    main()
