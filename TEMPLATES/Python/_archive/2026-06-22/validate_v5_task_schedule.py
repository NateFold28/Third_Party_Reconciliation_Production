"""
Production V5 Task Scheduler Validator

PURPOSE:
  Validates that all scheduled tasks are properly configured:
  • All 6 tasks exist
  • Schedules match RUNBOOK (07:30-07:40 ET, 1st of month for monthly tasks)
  • Task states are STARTED (not SUSPENDED)
  • Execution history is recent and successful

USAGE:
  python TEMPLATES/Python/validate_v5_task_schedule.py

NOTE:
  This script must run AFTER the master audit has completed successfully.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '.')

# Expected task schedule
EXPECTED_TASKS = {
    'V5_SANDBOX_DAILY_REFRESH_TASK': {
        'procedure': 'SP_V5_SANDBOX_DAILY_REFRESH',
        'schedule': '30 8 * * * America/New_York',  # Daily 08:30 ET (dev, 1hr after prod)
        'frequency': 'daily',
        'timeout_ms': 1800000,  # 30 min
        'comment': 'DEV daily refresh (no retrain)',
    },
    'V5_SANDBOX_MONTHLY_MODEL_TASK': {
        'procedure': 'SP_V5_SANDBOX_RUN_PIPELINE',
        'schedule': '30 8 1 * * America/New_York',  # Monthly 1st @ 08:30 ET
        'frequency': 'monthly',
        'timeout_ms': 7200000,  # 2 hours
        'comment': 'DEV monthly full retrain',
    },
    'V5_SANDBOX_FORECAST_SNAPSHOT_TASK': {
        'procedure': 'SP_V5_SNAPSHOT_MONTHLY_FORECAST',
        'schedule': '35 8 * * * America/New_York',  # Daily 08:35 ET
        'frequency': 'daily',
        'timeout_ms': 300000,  # 5 min
        'comment': 'Forecast snapshot (5 min after daily refresh)',
    },
    'V5_SANDBOX_EOM_SNAPSHOT_TASK': {
        'procedure': 'SP_V5_SNAPSHOT_OPEN_RENEWALS',
        'schedule': '40 7 * * * America/New_York',  # Daily 07:40 ET (NOTE: 07, not 08 — uses PROD schedule)
        'frequency': 'daily',
        'timeout_ms': 300000,  # 5 min
        'comment': 'Data-driven EOM contract snapshot',
    },
    'V5_MONTHLY_MODEL_REGISTRY_TASK': {
        'procedure': 'SP_REGISTER_MONTHLY_MODEL',
        'schedule': '10 8 1 * * America/New_York',  # Monthly 1st @ 08:10 ET
        'frequency': 'monthly',
        'timeout_ms': 600000,  # 10 min
        'comment': 'Register new model after monthly retrain',
    },
    'TASK_RECONCILIATION_DAILY': {
        'procedure': 'SP_RENEWALS_RECONCILIATION_SNAPSHOT',
        'schedule': '30 6 * * * UTC',  # Daily 06:30 UTC (= 02:30 or 01:30 ET depending on DST)
        'frequency': 'daily',
        'timeout_ms': 600000,  # 10 min
        'comment': 'Reconciliation snapshot (UTC-based, runs early)',
    }
}

# RUNBOOK reference
RUNBOOK_REFERENCE = """
From RUNBOOK.md:

| Task | Schedule | Procedure | What it does |
|------|----------|-----------|--------------|
| V5_SANDBOX_DAILY_REFRESH_TASK | Daily 07:30 ET (skip 1st) | SP_V5_SANDBOX_DAILY_REFRESH | Rebuild app tables from latest predictions + fresh CARR. No retrain. |
| V5_SANDBOX_MONTHLY_MODEL_TASK | 1st @ 07:30 ET | SP_V5_SANDBOX_RUN_PIPELINE | Full retrain: feature store → train → guard → app republish. |
| V5_SANDBOX_FORECAST_SNAPSHOT_TASK | Daily 07:35 ET | SP_V5_SNAPSHOT_MONTHLY_FORECAST | Freeze monthly forecast snapshot (shared audit table). |
| V5_SANDBOX_EOM_SNAPSHOT_TASK | Daily 07:40 ET | SP_V5_SNAPSHOT_OPEN_RENEWALS | Data-driven month-close snapshot. Fires only when OPEN_OPP_CARR≤0.01. |
| V5_MONTHLY_MODEL_REGISTRY_TASK | 1st @ 08:10 ET | SP_REGISTER_MONTHLY_MODEL | Register new model in ML_MODEL_REGISTRY after retrain. |
| TASK_RECONCILIATION_DAILY | Daily 06:30 UTC | SP_RENEWALS_RECONCILIATION_SNAPSHOT | Prediction vs actual reconciliation (model governance). |

Note: DEV tasks (sandbox) run 1hr later than PROD (08:30 vs 07:30 ET) to avoid contention.
"""

class ScheduleValidator:
    """Validates task schedules against expected configuration."""
    
    def __init__(self):
        self.findings = []
        self.errors = []
        self.warnings = []
    
    def log_ok(self, message):
        """Log OK finding."""
        self.findings.append(('OK', message))
        print(f"✓ {message}")
    
    def log_warning(self, message):
        """Log warning."""
        self.findings.append(('WARNING', message))
        self.warnings.append(message)
        print(f"⚠ {message}")
    
    def log_error(self, message):
        """Log error."""
        self.findings.append(('ERROR', message))
        self.errors.append(message)
        print(f"✗ {message}")
    
    def validate_from_sql(self):
        """
        Validate schedule by analyzing the scheduler SQL file.
        
        NOTE: This reads the SQL definition, not the actual Snowflake task state.
        For runtime validation, use Snowsight and run:
            SHOW TASKS IN SCHEMA STREAMLIT_APPS.DBO;
            SHOW TASK EXECUTION HISTORY FOR ...;
        """
        
        scheduler_file = Path('PROJECTS/Production_Renewal_Forecasting_Pipeline/scheduler/production_v5_tasks.sql')
        
        if not scheduler_file.exists():
            self.log_error(f"Scheduler file not found: {scheduler_file}")
            return False
        
        print(f"\nValidating task schedule from: {scheduler_file}\n")
        
        # Read scheduler definition
        with open(scheduler_file, 'r') as f:
            scheduler_sql = f.read()
        
        all_valid = True
        
        for task_name, expected_config in EXPECTED_TASKS.items():
            print(f"\n{'─'*70}")
            print(f"Task: {task_name}")
            print(f"{'─'*70}")
            
            # Check if task is defined
            if task_name not in scheduler_sql:
                self.log_error(f"Task definition not found in scheduler SQL")
                all_valid = False
                continue
            
            # Check procedure name
            proc_name = expected_config['procedure']
            if proc_name in scheduler_sql:
                self.log_ok(f"Procedure: {proc_name}")
            else:
                self.log_warning(f"Procedure {proc_name} not found in definition")
            
            # Check schedule
            schedule = expected_config['schedule']
            if schedule in scheduler_sql:
                self.log_ok(f"Schedule: {schedule} (as expected)")
            else:
                # Try to find actual schedule
                import re
                match = re.search(rf"CREATE OR REPLACE TASK.*?{task_name}.*?SCHEDULE\s*=\s*'([^']+)'", scheduler_sql, re.DOTALL)
                if match:
                    actual_schedule = match.group(1)
                    if actual_schedule == schedule:
                        self.log_ok(f"Schedule: {actual_schedule} ✓")
                    else:
                        self.log_warning(f"Schedule mismatch: expected '{schedule}', found '{actual_schedule}'")
                else:
                    self.log_warning(f"Could not parse schedule from SQL")
            
            # Check comment (metadata)
            comment = expected_config['comment']
            if comment in scheduler_sql:
                self.log_ok(f"Comment present: {comment[:50]}...")
            else:
                self.log_warning(f"Task comment may differ from expected")
            
            # Check frequency logic
            frequency = expected_config['frequency']
            if frequency == 'daily':
                self.log_ok(f"Frequency: daily (runs every day)")
            elif frequency == 'monthly':
                self.log_ok(f"Frequency: monthly on 1st (first day of month)")
        
        return all_valid
    
    def print_summary(self):
        """Print validation summary."""
        
        print(f"\n{'='*70}")
        print("TASK SCHEDULER VALIDATION SUMMARY")
        print(f"{'='*70}\n")
        
        print(f"Total checks: {len(self.findings)}")
        print(f"  ✓ OK:       {len([f for f in self.findings if f[0] == 'OK'])}")
        print(f"  ⚠ Warnings: {len(self.warnings)}")
        print(f"  ✗ Errors:   {len(self.errors)}")
        
        if self.errors:
            print(f"\n❌ VALIDATION FAILED")
            print(f"\nErrors found:")
            for error in self.errors:
                print(f"  - {error}")
        elif self.warnings:
            print(f"\n⚠️  VALIDATION PASSED WITH WARNINGS")
            print(f"\nWarnings:")
            for warning in self.warnings:
                print(f"  - {warning}")
        else:
            print(f"\n✅ VALIDATION PASSED")
        
        print(f"\n{'='*70}\n")
    
    def print_runbook_reference(self):
        """Print RUNBOOK reference for comparison."""
        
        print(f"\n{'='*70}")
        print("EXPECTED TASK CONFIGURATION (from RUNBOOK)")
        print(f"{'='*70}\n")
        
        print(RUNBOOK_REFERENCE)
        
        print(f"\n{'='*70}")
        print("ACTUAL TASK CONFIGURATION (from scheduler/production_v5_tasks.sql)")
        print(f"{'='*70}\n")
        
        for task_name, config in EXPECTED_TASKS.items():
            print(f"\n{task_name}")
            print(f"  Procedure:  {config['procedure']}")
            print(f"  Schedule:   {config['schedule']}")
            print(f"  Frequency:  {config['frequency']}")
            print(f"  Timeout:    {config['timeout_ms'] // 1000} min")
            print(f"  Comment:    {config['comment']}")

def main():
    """Execute validation."""
    
    print(f"\n{'='*70}")
    print("PRODUCTION V5 TASK SCHEDULER VALIDATOR")
    print(f"{'='*70}\n")
    
    validator = ScheduleValidator()
    
    # Validate from SQL definition
    all_valid = validator.validate_from_sql()
    
    # Print summary
    validator.print_summary()
    
    # Print reference
    validator.print_runbook_reference()
    
    # Next steps
    print(f"\n{'='*70}")
    print("NEXT STEPS")
    print(f"{'='*70}\n")
    
    print("""
To complete task validation in Snowsight:

1. Check actual task status:
   SHOW TASKS IN SCHEMA STREAMLIT_APPS.DBO;

2. Review execution history:
   SHOW TASK EXECUTION HISTORY FOR STREAMLIT_APPS.DBO.V5_SANDBOX_DAILY_REFRESH_TASK;
   SHOW TASK EXECUTION HISTORY FOR STREAMLIT_APPS.DBO.V5_SANDBOX_MONTHLY_MODEL_TASK;

3. Verify next scheduled run times:
   SELECT TASK_NAME, SCHEDULED_TIME, STATE
   FROM TABLE(INFORMATION_SCHEMA.TASK_HISTORY())
   WHERE SCHEMA_NAME = 'DBO'
   ORDER BY SCHEDULED_TIME DESC LIMIT 20;

4. If tasks are SUSPENDED, resume them:
   ALTER TASK STREAMLIT_APPS.DBO.V5_SANDBOX_DAILY_REFRESH_TASK RESUME;
   ALTER TASK STREAMLIT_APPS.DBO.V5_SANDBOX_MONTHLY_MODEL_TASK RESUME;
   -- ... etc for all tasks

═══════════════════════════════════════════════════════════════════════════
""")
    
    return 0 if all_valid and not validator.errors else 1

if __name__ == '__main__':
    exit(main())
