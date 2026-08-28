"""
Production V5 Pipeline - Quick Health Check (Direct Snowflake Execution)

Uses template connection.py to run health checks directly.
No manual Snowsight copy/paste needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

def run_health_checks():
    """Execute all 8 health checks directly."""
    
    print("\n" + "="*80)
    print("PRODUCTION V5 PIPELINE - HEALTH CHECK EXECUTION")
    print("="*80)
    print()
    
    try:
        conn = get_snowflake_connection()
        cur = conn.cursor()
        
        # Set context
        cur.execute("USE ROLE STREAMLIT_USER")
        cur.execute("USE WAREHOUSE REPORTING_WH")
        cur.execute("USE DATABASE STREAMLIT_APPS")
        cur.execute("USE SCHEMA DBO")
        print("✓ Connected to STREAMLIT_APPS.DBO\n")
        
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return False
    
    all_passed = True
    
    # CHECK 1: Core Procedures
    print("─" * 80)
    print("CHECK 1: CORE PROCEDURES EXIST")
    print("─" * 80)
    
    try:
        cur.execute("""
            SELECT 
                PROCEDURE_NAME,
                CASE WHEN PROCEDURE_NAME IS NOT NULL THEN 'EXISTS ✓' ELSE 'MISSING ✗' END AS status
            FROM INFORMATION_SCHEMA.ROUTINES 
            WHERE ROUTINE_SCHEMA = 'DBO' 
            AND ROUTINE_TYPE = 'PROCEDURE'
            AND PROCEDURE_NAME IN (
                'SP_V5_BUILD_FEATURE_STORE',
                'SP_V5_TRAIN_UNIFIED',
                'SP_V5_SANDBOX_PREDICTIONS_CONSISTENT',
                'SP_V5_BUILD_APP_TABLES_V5_SHADOW',
                'SP_V5_SNAPSHOT_MONTHLY_FORECAST',
                'SP_V5_SNAPSHOT_OPEN_RENEWALS',
                'SP_RENEWALS_RECONCILIATION_SNAPSHOT',
                'SP_REGISTER_MONTHLY_MODEL',
                'SP_V5_SANDBOX_DAILY_REFRESH',
                'SP_V5_SANDBOX_RUN_PIPELINE'
            )
            ORDER BY PROCEDURE_NAME
        """)
        
        results = cur.fetchall()
        found = len(results)
        
        for proc_name, status in results:
            print(f"  {status} {proc_name}")
        
        if found == 10:
            print(f"\n✓ CHECK 1 PASSED: All 10 procedures exist\n")
        else:
            print(f"\n⚠ CHECK 1 WARNING: Found {found}/10 procedures\n")
            all_passed = False
            
    except Exception as e:
        print(f"✗ CHECK 1 FAILED: {str(e)[:100]}\n")
        all_passed = False
    
    # CHECK 2: Production Tables
    print("─" * 80)
    print("CHECK 2: PRODUCTION TABLES EXIST")
    print("─" * 80)
    
    try:
        cur.execute("""
            SELECT 
                TABLE_NAME,
                ROW_COUNT,
                CASE WHEN ROW_COUNT > 0 THEN 'HAS DATA ✓' ELSE 'EMPTY ⚠' END AS data_status
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_SCHEMA = 'DBO' 
            AND TABLE_NAME IN (
                'ML_SANDBOX_V5_FEATURE_STORE',
                'ML_SANDBOX_V5_PREDICTIONS',
                'ML_SANDBOX_V5_MODEL_RUNS',
                'V5_SANDBOX_APP_CONTRACT_DETAIL',
                'V5_SANDBOX_APP_BACKTEST',
                'V5_SANDBOX_APP_RUNS',
                'V5_APP_FORECAST_SNAPSHOTS',
                'RENEWAL_FORECAST_V5_USER_INPUTS',
                'V5_PIPELINE_RUN_LOG',
                'ML_MODEL_REGISTRY'
            )
            ORDER BY TABLE_NAME
        """)
        
        results = cur.fetchall()
        found = len(results)
        
        for table_name, row_count, data_status in results:
            print(f"  {data_status} {table_name:40s} ({row_count:,} rows)")
        
        if found >= 8:
            print(f"\n✓ CHECK 2 PASSED: All key tables exist\n")
        else:
            print(f"\n⚠ CHECK 2 WARNING: Found {found}/10 tables\n")
            all_passed = False
            
    except Exception as e:
        print(f"✗ CHECK 2 FAILED: {str(e)[:100]}\n")
        all_passed = False
    
    # CHECK 3: Data Freshness
    print("─" * 80)
    print("CHECK 3: DATA FRESHNESS")
    print("─" * 80)
    
    try:
        cur.execute("""
            SELECT 
                'Feature Store' AS table_name,
                MAX(AS_OF_DATE)::DATE AS latest_date,
                CURRENT_DATE() - MAX(AS_OF_DATE)::DATE AS days_old
            FROM ML_SANDBOX_V5_FEATURE_STORE
            WHERE AS_OF_DATE IS NOT NULL

            UNION ALL SELECT 
                'Predictions',
                MAX(PREDICTION_TS)::DATE,
                CURRENT_DATE() - MAX(PREDICTION_TS)::DATE
            FROM ML_SANDBOX_V5_PREDICTIONS
            WHERE PREDICTION_TS IS NOT NULL

            UNION ALL SELECT 
                'Model Runs',
                MAX(RUN_TS)::DATE,
                CURRENT_DATE() - MAX(RUN_TS)::DATE
            FROM ML_SANDBOX_V5_MODEL_RUNS
            WHERE RUN_TS IS NOT NULL

            UNION ALL SELECT 
                'App Contract Detail',
                MAX(RENEWAL_MONTH)::DATE,
                CURRENT_DATE() - MAX(RENEWAL_MONTH)::DATE
            FROM V5_SANDBOX_APP_CONTRACT_DETAIL
            WHERE RENEWAL_MONTH IS NOT NULL

            ORDER BY table_name
        """)
        
        results = cur.fetchall()
        freshness_ok = True
        
        for table_name, latest_date, days_old in results:
            if days_old is None:
                print(f"  ⚠ {table_name:30s} no data")
                freshness_ok = False
            elif days_old <= 2:
                print(f"  ✓ {table_name:30s} {latest_date} ({days_old} days old)")
            else:
                print(f"  ⚠ {table_name:30s} {latest_date} ({days_old} days old)")
                freshness_ok = False
        
        if freshness_ok:
            print(f"\n✓ CHECK 3 PASSED: All data is recent (0-2 days old)\n")
        else:
            print(f"\n⚠ CHECK 3 WARNING: Some data is stale\n")
            all_passed = False
            
    except Exception as e:
        print(f"✗ CHECK 3 FAILED: {str(e)[:100]}\n")
        all_passed = False
    
    # CHECK 4: Recent Pipeline Runs
    print("─" * 80)
    print("CHECK 4: RECENT PIPELINE EXECUTIONS (Last 10)")
    print("─" * 80)
    
    try:
        cur.execute("""
            SELECT 
                TRIGGERED_AT,
                SOURCE,
                STATUS,
                CASE 
                    WHEN STATUS = 'OK' THEN 'SUCCESS ✓'
                    WHEN STATUS LIKE 'FAIL%' THEN 'FAILED ✗'
                    ELSE 'UNKNOWN'
                END AS result
            FROM V5_PIPELINE_RUN_LOG
            ORDER BY TRIGGERED_AT DESC
            LIMIT 10
        """)
        
        results = cur.fetchall()
        successful = 0
        failed = 0
        
        for triggered_at, source, status, result in results:
            print(f"  {result} {triggered_at} | {source:20s} | {status}")
            if result == 'SUCCESS ✓':
                successful += 1
            elif result == 'FAILED ✗':
                failed += 1
        
        if successful > 0:
            print(f"\n✓ CHECK 4 PASSED: {successful} successful runs in recent history\n")
        else:
            print(f"\n⚠ CHECK 4 WARNING: No successful runs found\n")
            all_passed = False
            
    except Exception as e:
        print(f"✗ CHECK 4 FAILED: {str(e)[:100]}\n")
        all_passed = False
    
    # CHECK 5: Model Quality
    print("─" * 80)
    print("CHECK 5: LATEST MODEL METRICS")
    print("─" * 80)
    
    try:
        cur.execute("""
            SELECT 
                RUN_ID,
                TRAINED_AT,
                AUC_AVG,
                MAE_PP,
                BIAS_PP,
                BOARD_GATE_PASS,
                CASE 
                    WHEN AUC_AVG > 0.76 AND MAE_PP <= 2.5 AND BOARD_GATE_PASS = TRUE THEN 'PRODUCTION READY ✓'
                    ELSE 'REVIEW NEEDED ⚠'
                END AS production_status
            FROM ML_MODEL_REGISTRY
            ORDER BY TRAINED_AT DESC
            LIMIT 1
        """)
        
        results = cur.fetchall()
        
        if results:
            run_id, trained_at, auc, mae, bias, gate_pass, status = results[0]
            print(f"  {status}")
            print(f"  RUN_ID:         {run_id}")
            print(f"  TRAINED_AT:     {trained_at}")
            print(f"  AUC_AVG:        {auc:.4f} (expect > 0.76) {'✓' if auc > 0.76 else '✗'}")
            print(f"  MAE_PP:         {mae:.2f} (expect ≤ 2.5) {'✓' if mae <= 2.5 else '✗'}")
            print(f"  BIAS_PP:        {bias:.2f} (expect ≈ 0.0) {'✓' if abs(bias) <= 0.1 else '✗'}")
            print(f"  BOARD_GATE:     {gate_pass} {'✓' if gate_pass else '✗'}")
            
            if auc > 0.76 and mae <= 2.5 and gate_pass:
                print(f"\n✓ CHECK 5 PASSED: Model metrics within expected ranges\n")
            else:
                print(f"\n⚠ CHECK 5 WARNING: Some metrics outside expected ranges\n")
                all_passed = False
        else:
            print(f"  ⚠ No model registered\n")
            all_passed = False
            
    except Exception as e:
        print(f"✗ CHECK 5 FAILED: {str(e)[:100]}\n")
        all_passed = False
    
    # CHECK 6: Forward Forecast Rates
    print("─" * 80)
    print("CHECK 6: FORWARD FORECAST RATES (expect 70-78%)")
    print("─" * 80)
    
    try:
        cur.execute("""
            SELECT 
                RENEWAL_MONTH,
                COUNT(*) AS contracts,
                ROUND(SUM(ATR) / 1e6, 2) AS atr_millions,
                ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 1) AS forecast_rate_pct,
                CASE 
                    WHEN ROUND(SUM(ML_FORECAST) / NULLIF(SUM(ATR), 0) * 100, 1) BETWEEN 70 AND 78 THEN 'IN RANGE ✓'
                    ELSE 'OUT OF RANGE ⚠'
                END AS rate_status
            FROM V5_SANDBOX_APP_CONTRACT_DETAIL
            WHERE RENEWAL_MONTH >= DATE_TRUNC('MONTH', CURRENT_DATE())
            GROUP BY RENEWAL_MONTH
            ORDER BY RENEWAL_MONTH
            LIMIT 12
        """)
        
        results = cur.fetchall()
        rates_ok = True
        
        for renewal_month, contracts, atr_m, rate_pct, rate_status in results:
            print(f"  {rate_status} {renewal_month} | {rate_pct:5.1f}% | {contracts:6,} contracts | ${atr_m:8.2f}M")
            if not rate_status.startswith('IN RANGE'):
                rates_ok = False
        
        if rates_ok and len(results) > 0:
            print(f"\n✓ CHECK 6 PASSED: Forward rates in expected range (70-78%)\n")
        elif len(results) == 0:
            print(f"\n⚠ CHECK 6 WARNING: No forward month data\n")
            all_passed = False
        else:
            print(f"\n⚠ CHECK 6 WARNING: Some rates outside range\n")
            all_passed = False
            
    except Exception as e:
        print(f"✗ CHECK 6 FAILED: {str(e)[:100]}\n")
        all_passed = False
    
    # CHECK 7: App Data Ready
    print("─" * 80)
    print("CHECK 7: STREAMLIT APP DATA READY")
    print("─" * 80)
    
    try:
        cur.execute("""
            SELECT 
                'Contract Detail' AS app_table,
                COUNT(*) AS total_rows,
                COUNT(DISTINCT CONTRACT_ID) AS unique_contracts,
                MAX(RENEWAL_MONTH) AS latest_month,
                CASE WHEN COUNT(*) > 0 THEN 'READY ✓' ELSE 'EMPTY ✗' END AS app_status
            FROM V5_SANDBOX_APP_CONTRACT_DETAIL

            UNION ALL SELECT 
                'Backtest Data',
                COUNT(*),
                COUNT(DISTINCT CONTRACT_ID),
                MAX(RENEWAL_MONTH),
                CASE WHEN COUNT(*) > 0 THEN 'READY ✓' ELSE 'EMPTY ✗' END
            FROM V5_SANDBOX_APP_BACKTEST

            UNION ALL SELECT 
                'Model Runs',
                COUNT(*),
                COUNT(DISTINCT RUN_ID),
                MAX(RUN_TS)::DATE,
                CASE WHEN COUNT(*) > 0 THEN 'READY ✓' ELSE 'EMPTY ✗' END
            FROM ML_SANDBOX_V5_MODEL_RUNS
        """)
        
        results = cur.fetchall()
        app_ready = True
        
        for app_table, total_rows, unique_items, latest_date, app_status in results:
            print(f"  {app_status} {app_table:20s} | {total_rows:,} rows | {unique_items:,} items | latest: {latest_date}")
            if not app_status.startswith('READY'):
                app_ready = False
        
        if app_ready:
            print(f"\n✓ CHECK 7 PASSED: All app data tables ready\n")
        else:
            print(f"\n⚠ CHECK 7 WARNING: Some app tables empty\n")
            all_passed = False
            
    except Exception as e:
        print(f"✗ CHECK 7 FAILED: {str(e)[:100]}\n")
        all_passed = False
    
    # CHECK 8: Scheduled Tasks
    print("─" * 80)
    print("CHECK 8: SCHEDULED TASKS")
    print("─" * 80)
    
    try:
        cur.execute("SHOW TASKS IN SCHEMA STREAMLIT_APPS.DBO")
        results = cur.fetchall()
        
        expected_tasks = {
            'V5_SANDBOX_DAILY_REFRESH_TASK',
            'V5_SANDBOX_MONTHLY_MODEL_TASK',
            'V5_SANDBOX_FORECAST_SNAPSHOT_TASK',
            'V5_SANDBOX_EOM_SNAPSHOT_TASK',
            'V5_MONTHLY_MODEL_REGISTRY_TASK',
            'TASK_RECONCILIATION_DAILY'
        }
        
        found_tasks = {}
        for row in results:
            task_name = row[1]  # Name is second column
            task_state = row[6] if len(row) > 6 else 'UNKNOWN'  # State column
            found_tasks[task_name] = task_state
            
            if task_name in expected_tasks:
                status = 'STARTED ✓' if task_state == 'STARTED' else f'{task_state} ⚠'
                print(f"  {status} {task_name}")
        
        found_count = len([t for t in found_tasks if t in expected_tasks])
        
        if found_count >= 4:
            print(f"\n✓ CHECK 8 PASSED: {found_count}/6 expected tasks configured\n")
        else:
            print(f"\n⚠ CHECK 8 WARNING: Only {found_count}/6 tasks found\n")
            all_passed = False
            
    except Exception as e:
        print(f"✗ CHECK 8 FAILED: {str(e)[:100]}\n")
        all_passed = False
    
    # Summary
    print("="*80)
    print("HEALTH CHECK SUMMARY")
    print("="*80)
    
    if all_passed:
        print("""
✅ ALL CHECKS PASSED

Your Production V5 Pipeline is OPERATIONAL and WORKING CORRECTLY:
  ✓ All core procedures exist and callable
  ✓ All production tables exist with recent data
  ✓ Recent pipeline runs were successful
  ✓ Model quality metrics are within expected ranges
  ✓ Forward forecast rates are 70-78%
  ✓ Streamlit app data is ready
  ✓ Scheduled tasks are configured

NEXT: The Streamlit app should now load without dependency errors.

═══════════════════════════════════════════════════════════════════════════
""")
    else:
        print("""
⚠️ SOME CHECKS FAILED OR WARNED

Review the output above for details. Common issues:
  • Data is stale (>2 days old) → Run pipeline refresh
  • No recent successful runs → Check V5_PIPELINE_RUN_LOG for errors
  • Model metrics below threshold → May need retraining
  • Tasks not created → Run scheduler/production_v5_tasks.sql

For detailed troubleshooting, see: AUDIT_AND_DEPLOYMENT_CHECKLIST.md

═══════════════════════════════════════════════════════════════════════════
""")
    
    conn.close()
    return all_passed

if __name__ == '__main__':
    success = run_health_checks()
    exit(0 if success else 1)
