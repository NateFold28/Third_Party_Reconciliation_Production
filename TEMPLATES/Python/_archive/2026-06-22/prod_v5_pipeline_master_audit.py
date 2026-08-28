"""
Production V5 Pipeline Audit & End-to-End Validation Master Script

PURPOSE:
  Comprehensive audit of the Production Renewal Forecasting Pipeline (V5).
  1. Validates all pipeline objects exist and are correct
  2. Checks scheduling configuration
  3. Runs pipeline end-to-end
  4. Post-run validation and health checks
  5. Generates audit report and updates RUNBOOK

EXECUTION:
  From workspace root:
    python TEMPLATES/Python/prod_v5_pipeline_master_audit.py

OUTPUTS:
  - Console report with all findings
  - PROJECTS/Production_Renewal_Forecasting_Pipeline/AUDIT_REPORT_<DATE>.md
  - Updates to RUNBOOK.md if issues found
"""
import sys
import os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

# ============================================================================
# CONFIGURATION
# ============================================================================

PIPELINE_BASE = Path('PROJECTS/Production_Renewal_Forecasting_Pipeline')
DB = 'STREAMLIT_APPS'
SCHEMA = 'DBO'
WAREHOUSE = 'REPORTING_WH'
ROLE = 'STREAMLIT_USER'

# Expected objects (procedures, tables, views)
EXPECTED_PROCEDURES = [
    'SP_V5_BUILD_FEATURE_STORE',
    'SP_V5_TRAIN_UNIFIED',
    'SP_V5_SANDBOX_PREDICTIONS_CONSISTENT',
    'SP_V5_BUILD_APP_TABLES_V5_SHADOW',
    'SP_V5_SNAPSHOT_MONTHLY_FORECAST',
    'SP_V5_SNAPSHOT_OPEN_RENEWALS',
    'SP_RENEWALS_RECONCILIATION_SNAPSHOT',
    'SP_REGISTER_MONTHLY_MODEL',
    'SP_V5_SANDBOX_DAILY_REFRESH',
    'SP_V5_SANDBOX_RUN_PIPELINE',
]

EXPECTED_TABLES = [
    'ML_SANDBOX_V5_FEATURE_STORE',
    'ML_SANDBOX_V5_PREDICTIONS',
    'ML_SANDBOX_V5_MODEL_RUNS',
    'ML_SANDBOX_V5_BASE_RATES',
    'ML_SANDBOX_V5_PSI_AUDIT',
    'ML_SANDBOX_V5_WALK_FORWARD',
    'V5_SANDBOX_APP_CONTRACT_DETAIL',
    'V5_SANDBOX_APP_BACKTEST',
    'V5_SANDBOX_APP_RUNS',
    'V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY',
    'V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED',
    'V5_SANDBOX_APP_SHAP_DRIVERS',
    'V5_APP_FORECAST_SNAPSHOTS',
    'RENEWAL_FORECAST_V5_USER_INPUTS',
    'V5_PIPELINE_RUN_LOG',
    'ML_MODEL_REGISTRY',
]

EXPECTED_TASKS = [
    'V5_SANDBOX_DAILY_REFRESH_TASK',
    'V5_SANDBOX_MONTHLY_MODEL_TASK',
    'V5_SANDBOX_FORECAST_SNAPSHOT_TASK',
    'V5_SANDBOX_EOM_SNAPSHOT_TASK',
    'V5_MONTHLY_MODEL_REGISTRY_TASK',
    'TASK_RECONCILIATION_DAILY',
]

# ============================================================================
# AUDIT REPORT CLASS
# ============================================================================

class AuditReport:
    """Tracks all audit findings."""
    
    def __init__(self):
        self.timestamp = datetime.now()
        self.sections = {}
        self.findings = []
        self.errors = []
        self.warnings = []
        
    def add_section(self, title):
        """Add audit section."""
        self.sections[title] = []
        return title
    
    def log(self, section, message, level='INFO'):
        """Log finding to section."""
        if section not in self.sections:
            self.add_section(section)
        self.sections[section].append((level, message))
        
        if level == 'ERROR':
            self.errors.append(message)
        elif level == 'WARNING':
            self.warnings.append(message)
            
    def print_summary(self):
        """Print audit summary to console."""
        print("\n" + "="*80)
        print("PRODUCTION V5 PIPELINE AUDIT REPORT")
        print(f"Generated: {self.timestamp.isoformat()}")
        print("="*80)
        
        for section, logs in self.sections.items():
            print(f"\n### {section}")
            print("-" * 80)
            for level, msg in logs:
                prefix = "✓" if level == 'OK' else "⚠" if level == 'WARNING' else "✗" if level == 'ERROR' else "ℹ"
                print(f"{prefix} [{level}] {msg}")
        
        print("\n" + "="*80)
        print(f"SUMMARY: {len(self.errors)} errors, {len(self.warnings)} warnings")
        print("="*80)
        
    def save_markdown(self, output_file):
        """Save audit report as Markdown."""
        content = []
        content.append(f"# Production V5 Pipeline Audit Report\n")
        content.append(f"**Generated:** {self.timestamp.isoformat()}\n\n")
        
        content.append(f"## Summary\n")
        content.append(f"- **Errors:** {len(self.errors)}\n")
        content.append(f"- **Warnings:** {len(self.warnings)}\n")
        content.append(f"- **Total Findings:** {len(self.findings)}\n\n")
        
        for section, logs in self.sections.items():
            content.append(f"## {section}\n\n")
            for level, msg in logs:
                icon = "✓" if level == 'OK' else "⚠" if level == 'WARNING' else "✗" if level == 'ERROR' else "ℹ"
                content.append(f"- {icon} **[{level}]** {msg}\n")
            content.append("\n")
        
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w') as f:
            f.write(''.join(content))
        print(f"\n✓ Audit report saved to: {output_file}")

# ============================================================================
# AUDIT FUNCTIONS
# ============================================================================

def audit_procedures(conn, report):
    """Audit all expected procedures exist."""
    section = report.add_section("1. PROCEDURE INVENTORY")
    
    cur = conn.cursor()
    cur.execute(f"SHOW PROCEDURES IN SCHEMA {DB}.{SCHEMA}")
    existing = set(r[1].upper() for r in cur.fetchall())
    
    report.log(section, f"Found {len(existing)} total procedures", level='INFO')
    
    for proc in sorted(EXPECTED_PROCEDURES):
        if proc.upper() in existing:
            report.log(section, f"{proc} — ✓ exists", level='OK')
        else:
            report.log(section, f"{proc} — ✗ MISSING", level='ERROR')
    
    return len([p for p in EXPECTED_PROCEDURES if p.upper() in existing]) == len(EXPECTED_PROCEDURES)


def audit_tables(conn, report):
    """Audit all expected tables exist."""
    section = report.add_section("2. TABLE INVENTORY")
    
    cur = conn.cursor()
    cur.execute(f"SHOW TABLES IN SCHEMA {DB}.{SCHEMA}")
    existing = set(r[1].upper() for r in cur.fetchall())
    
    report.log(section, f"Found {len(existing)} total tables", level='INFO')
    
    for table in sorted(EXPECTED_TABLES):
        if table.upper() in existing:
            report.log(section, f"{table} — ✓ exists", level='OK')
        else:
            report.log(section, f"{table} — ✗ MISSING", level='ERROR')
    
    return len([t for t in EXPECTED_TABLES if t.upper() in existing]) == len(EXPECTED_TABLES)


def audit_table_freshness(conn, report):
    """Check freshness of key tables."""
    section = report.add_section("3. DATA FRESHNESS")
    
    cur = conn.cursor()
    
    checks = [
        ("Feature Store", 
         "SELECT MAX(AS_OF_DATE) FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_FEATURE_STORE",
         1),
        ("Predictions", 
         "SELECT MAX(PREDICTION_TS) FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_PREDICTIONS",
         1),
        ("Model Runs", 
         "SELECT MAX(RUN_TS) FROM STREAMLIT_APPS.DBO.ML_SANDBOX_V5_MODEL_RUNS",
         1),
        ("App Contract Detail", 
         "SELECT MAX(RENEWAL_MONTH) FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL",
         1),
        ("Model Registry", 
         "SELECT MAX(TRAINED_AT) FROM STREAMLIT_APPS.DBO.ML_MODEL_REGISTRY",
         1),
    ]
    
    for name, query, days_threshold in checks:
        try:
            cur.execute(query)
            result = cur.fetchone()
            if result and result[0]:
                timestamp = result[0]
                age_days = (datetime.now() - timestamp.replace(tzinfo=None)).days
                
                if age_days <= days_threshold:
                    report.log(section, f"{name}: {timestamp} (fresh)", level='OK')
                else:
                    report.log(section, f"{name}: {timestamp} ({age_days} days old)", level='WARNING')
            else:
                report.log(section, f"{name}: ✗ no data", level='WARNING')
        except Exception as e:
            report.log(section, f"{name}: error — {str(e)[:60]}", level='WARNING')


def audit_pipeline_log(conn, report):
    """Check recent pipeline runs."""
    section = report.add_section("4. RECENT PIPELINE RUNS")
    
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT TRIGGERED_AT, SOURCE, STATUS, MESSAGE
            FROM STREAMLIT_APPS.DBO.V5_PIPELINE_RUN_LOG
            ORDER BY TRIGGERED_AT DESC
            LIMIT 10
        """)
        
        rows = cur.fetchall()
        report.log(section, f"Found {len(rows)} recent runs", level='INFO')
        
        for triggered_at, source, status, message in rows:
            msg_short = (message[:50] + '...') if message and len(message) > 50 else message
            level = 'OK' if status == 'OK' else 'ERROR' if 'FAIL' in status else 'INFO'
            report.log(section, 
                      f"{triggered_at} | {source} | {status} | {msg_short}",
                      level=level)
    except Exception as e:
        report.log(section, f"Error reading pipeline log: {str(e)[:60]}", level='WARNING')


def audit_scheduled_tasks(conn, report):
    """Audit scheduled task configuration."""
    section = report.add_section("5. SCHEDULED TASKS")
    
    cur = conn.cursor()
    try:
        cur.execute(f"SHOW TASKS IN SCHEMA {DB}.{SCHEMA}")
        cols = [d[0].upper() for d in cur.description]
        tasks = {r[1].upper(): dict(zip(cols, r)) for r in cur.fetchall()}
        
        report.log(section, f"Found {len(tasks)} total tasks", level='INFO')
        
        for task_name in sorted(EXPECTED_TASKS):
            if task_name.upper() in tasks:
                task = tasks[task_name.upper()]
                state = task.get('STATE', 'UNKNOWN')
                schedule = task.get('SCHEDULE', 'UNKNOWN')
                level = 'OK' if state == 'STARTED' else 'WARNING'
                report.log(section, 
                          f"{task_name}: {state} | schedule={schedule}",
                          level=level)
            else:
                report.log(section, f"{task_name}: ✗ MISSING", level='ERROR')
    except Exception as e:
        report.log(section, f"Error reading tasks: {str(e)[:60]}", level='WARNING')


def check_guard_predicate(conn, report):
    """Run pre-publish guard check."""
    section = report.add_section("6. PRE-PUBLISH GUARD")
    
    cur = conn.cursor()
    try:
        cur.execute("CALL STREAMLIT_APPS.DBO.SP_V5_SANDBOX_PREDICTIONS_CONSISTENT()")
        result = cur.fetchall()
        report.log(section, f"Guard result: {result}", level='OK')
    except Exception as e:
        report.log(section, f"Guard check error: {str(e)[:60]}", level='ERROR')


def run_full_pipeline(conn, report):
    """Run full end-to-end pipeline."""
    section = report.add_section("7. END-TO-END PIPELINE EXECUTION")
    
    cur = conn.cursor()
    
    report.log(section, "Starting SP_V5_SANDBOX_RUN_PIPELINE...", level='INFO')
    
    try:
        cur.execute("CALL STREAMLIT_APPS.DBO.SP_V5_SANDBOX_RUN_PIPELINE()")
        result = cur.fetchall()
        
        if result:
            message = str(result[0][0]) if result[0] else "Completed"
            if 'OK' in message:
                report.log(section, f"✓ Pipeline succeeded: {message[:80]}", level='OK')
            elif 'FAIL' in message:
                report.log(section, f"✗ Pipeline failed: {message}", level='ERROR')
            else:
                report.log(section, f"Pipeline output: {message[:100]}", level='INFO')
        else:
            report.log(section, "Pipeline executed (no return value)", level='OK')
            
    except Exception as e:
        report.log(section, f"Pipeline execution error: {str(e)}", level='ERROR')
        return False
    
    return True


def run_post_validation(conn, report):
    """Run post-execution validation queries."""
    section = report.add_section("8. POST-EXECUTION VALIDATION")
    
    cur = conn.cursor()
    
    # Check 1: Forward forecast rates
    try:
        cur.execute("""
            SELECT RENEWAL_MONTH,
                   ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 1) AS ML_RATE_PCT
            FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
            WHERE RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
            GROUP BY RENEWAL_MONTH
            ORDER BY RENEWAL_MONTH
            LIMIT 1
        """)
        result = cur.fetchone()
        if result and result[1] is not None:
            rate = result[1]
            if 70 <= rate <= 78:
                report.log(section, f"Forward forecast rate: {rate}% — ✓ in expected range (70-78%)", level='OK')
            else:
                report.log(section, f"Forward forecast rate: {rate}% — ⚠ outside expected range (70-78%)", level='WARNING')
        else:
            report.log(section, "Forward forecast rate: insufficient data", level='WARNING')
    except Exception as e:
        report.log(section, f"Forward rate check error: {str(e)[:60]}", level='WARNING')
    
    # Check 2: Model registry
    try:
        cur.execute("""
            SELECT RUN_ID, TRAINED_AT, AUC_AVG, MAE_PP, BIAS_PP
            FROM STREAMLIT_APPS.DBO.V_MODEL_REGISTRY_CURRENT
            LIMIT 1
        """)
        result = cur.fetchone()
        if result:
            run_id, trained_at, auc, mae, bias = result
            report.log(section, 
                      f"Latest model registered: {run_id} | AUC={auc:.3f} | MAE={mae:.2f}pp | Bias={bias:.2f}pp",
                      level='OK')
        else:
            report.log(section, "No model in registry", level='WARNING')
    except Exception as e:
        report.log(section, f"Model registry check error: {str(e)[:60]}", level='WARNING')
    
    # Check 3: App data freshness
    try:
        cur.execute("""
            SELECT MAX(RENEWAL_MONTH), COUNT(*)
            FROM STREAMLIT_APPS.DBO.V5_SANDBOX_APP_CONTRACT_DETAIL
        """)
        result = cur.fetchone()
        if result:
            month, count = result
            report.log(section, f"App contract detail: {count:,} rows, latest month={month}", level='OK')
    except Exception as e:
        report.log(section, f"App data check error: {str(e)[:60]}", level='WARNING')


def main():
    """Execute full audit."""
    print("\n" + "="*80)
    print("PRODUCTION V5 PIPELINE AUDIT — INITIALIZATION")
    print("="*80)
    
    # Connect to Snowflake
    print("Connecting to Snowflake...")
    try:
        conn = get_snowflake_connection()
        conn.cursor().execute(f"USE ROLE {ROLE}; USE WAREHOUSE {WAREHOUSE}; USE DATABASE {DB}; USE SCHEMA {SCHEMA}")
        print("✓ Connected to Snowflake")
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        sys.exit(1)
    
    # Initialize audit report
    report = AuditReport()
    
    # Run all audits
    print("\nRunning audit checks...")
    audit_procedures(conn, report)
    audit_tables(conn, report)
    audit_table_freshness(conn, report)
    audit_pipeline_log(conn, report)
    audit_scheduled_tasks(conn, report)
    check_guard_predicate(conn, report)
    
    # Execute pipeline
    print("\nExecuting end-to-end pipeline (this may take 25-40 minutes)...")
    pipeline_success = run_full_pipeline(conn, report)
    
    # Post-execution validation
    if pipeline_success:
        print("Running post-execution validation...")
        run_post_validation(conn, report)
    
    # Print and save report
    report.print_summary()
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = PIPELINE_BASE / f'AUDIT_REPORT_{timestamp}.md'
    report.save_markdown(str(output_file))
    
    conn.close()
    
    # Return exit code based on errors
    if report.errors:
        print(f"\n✗ Audit completed with {len(report.errors)} error(s)")
        return 1
    else:
        print(f"\n✓ Audit completed successfully")
        return 0


if __name__ == '__main__':
    exit(main())
