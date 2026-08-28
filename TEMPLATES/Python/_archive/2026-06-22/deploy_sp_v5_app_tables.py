"""
Deploy SP_V5_BUILD_APP_TABLES_V5_SHADOW and its new tables to Snowflake.

Reads the authoritative SQL from:
  PROJECTS/Production_Renewal_Forecasting_Pipeline/sql/pipeline/PROD_V1_3_app_tables.sql

Deploys:
  1. V5_SANDBOX_FORECAST_COMPAT view (race-condition guard: HAVING COUNT(DISTINCT SEGMENT) >= 4)
  2. SP_V5_BUILD_APP_TABLES_V5_SHADOW stored procedure (same guard + reconciliation table)
  3. Calls the SP once to rebuild all app tables (including new CONTRACT_RECONCILIATION)
  4. Smoke tests: run/segment counts, netting spot-check
"""
import sys, re
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

SQL_FILE = r'PROJECTS\Production_Renewal_Forecasting_Pipeline\sql\pipeline\PROD_V1_3_app_tables.sql'


def _extract_view_ddl(full_sql: str) -> str | None:
    """Extract the CREATE OR REPLACE VIEW ... ending just before the semicolon
    that closes it (the line ending in 'JOIN latest_run lr ON lr.RUN_ID = p.RUN_ID;')."""
    m = re.search(
        r'(CREATE OR REPLACE VIEW STREAMLIT_APPS\.DBO\.V5_SANDBOX_FORECAST_COMPAT AS.*?'
        r'JOIN latest_run lr ON lr\.RUN_ID = p\.RUN_ID)',
        full_sql, re.DOTALL
    )
    return m.group(1).strip() if m else None


def _extract_sp_ddl(full_sql: str) -> str | None:
    """Extract the CREATE OR REPLACE PROCEDURE ... AS $$ ... END; $$ block.

    Snowflake stored procedures use $$ as body delimiters. We locate the
    opening $$ after AS and the closing $$ that follows END;, then grab
    everything including the trailing $$ (no semicolon — the connector
    does not need one here).
    """
    m = re.search(
        r'(CREATE OR REPLACE PROCEDURE STREAMLIT_APPS\.DBO\.SP_V5_BUILD_APP_TABLES_V5_SHADOW'
        r'\(\).*?END;\s*\$\$)',
        full_sql, re.DOTALL
    )
    return m.group(1).strip() if m else None


def main():
    with open(SQL_FILE, encoding='utf-8') as f:
        full_sql = f.read()

    view_ddl = _extract_view_ddl(full_sql)
    sp_ddl   = _extract_sp_ddl(full_sql)

    conn = get_snowflake_connection()
    cur  = conn.cursor()
    for s in ['USE ROLE STREAMLIT_USER', 'USE WAREHOUSE REPORTING_WH',
              'USE DATABASE STREAMLIT_APPS', 'USE SCHEMA DBO']:
        cur.execute(s)
    print('Connected ✓\n')

    # ── 1. Deploy compat view ────────────────────────────────────────────────
    if view_ddl:
        guard_ok = 'HAVING COUNT(DISTINCT SEGMENT) >= 4' in view_ddl
        print(f"View DDL extracted ({len(view_ddl):,} chars) — race-condition guard: {'✓' if guard_ok else '⚠ MISSING'}")
        cur.execute(view_ddl)
        print('  V5_SANDBOX_FORECAST_COMPAT deployed ✓')
    else:
        print('  ⚠ Could not extract view DDL — check regex')

    # ── 2. Deploy stored procedure ───────────────────────────────────────────
    if sp_ddl:
        guard_ok  = 'HAVING COUNT(DISTINCT SEGMENT) >= 4' in sp_ddl
        recon_ok  = 'V5_SANDBOX_APP_CONTRACT_RECONCILIATION' in sp_ddl
        print(f"\nSP DDL extracted ({len(sp_ddl):,} chars)"
              f" — guard: {'✓' if guard_ok else '⚠'}"
              f" — reconciliation table: {'✓' if recon_ok else '⚠'}")
        cur.execute(sp_ddl)
        print('  SP_V5_BUILD_APP_TABLES_V5_SHADOW deployed ✓')
    else:
        print('  ⚠ Could not extract SP DDL — check regex')

    # ── 3. Run the SP to rebuild all app tables ──────────────────────────────
    print('\nCalling SP_V5_BUILD_APP_TABLES_V5_SHADOW() to rebuild all tables...')
    cur.execute('CALL STREAMLIT_APPS.DBO.SP_V5_BUILD_APP_TABLES_V5_SHADOW()')
    result = cur.fetchone()
    print(f'  SP result: {result[0] if result else "no result"}')

    # ── 4. Smoke tests ───────────────────────────────────────────────────────
    print('\n── Smoke tests ──────────────────────────────────────────────────')

    # 4a. Compat view — should show complete run with >= 4 segments
    cur.execute("""
        SELECT SOURCE_RUN_ID, COUNT(DISTINCT SEGMENT) AS segs, COUNT(*) AS n_rows
        FROM V5_SANDBOX_FORECAST_COMPAT GROUP BY SOURCE_RUN_ID
    """)
    for run_id, segs, n_rows in cur.fetchall():
        status = '✓ COMPLETE' if segs >= 4 else '⚠ PARTIAL — guard may not be working'
        print(f'  Compat view → {run_id}: {segs} segments, {n_rows:,} rows  {status}')

    # 4b. Reconciliation table — should have rows for both matured and open months
    cur.execute("""
        SELECT COUNT(*) AS total,
               COUNT_IF(IS_MATURE) AS matured,
               COUNT_IF(NOT IS_MATURE) AS forward,
               COUNT_IF(NETTING_PP IS NOT NULL) AS has_netting,
               ROUND(AVG(IFF(IS_MATURE AND NETTING_PP IS NOT NULL, NETTING_PP, NULL)), 3) AS avg_netting_pp
        FROM V5_SANDBOX_APP_CONTRACT_RECONCILIATION
    """)
    row = cur.fetchone()
    if row:
        total, matured, forward, has_netting, avg_net = row
        print(f'\n  CONTRACT_RECONCILIATION: {total:,} rows'
              f' ({matured:,} mature, {forward:,} forward)')
        print(f'  Netting PP: {has_netting:,} rows have value, avg = {avg_net:.3f}pp')

    # 4c. Netting spot-check — trailing 6 months
    cur.execute("""
        SELECT RENEWAL_MONTH,
               ROUND(AVG(NETTING_PP), 2) AS avg_netting,
               ROUND(MIN(NETTING_PP), 2) AS min_netting,
               ROUND(MAX(NETTING_PP), 2) AS max_netting
        FROM V5_SANDBOX_APP_CONTRACT_RECONCILIATION
        WHERE RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
          AND RENEWAL_MONTH >= DATEADD('MONTH', -6, DATE_TRUNC('MONTH', CURRENT_DATE()))
          AND NETTING_PP IS NOT NULL
        GROUP BY RENEWAL_MONTH ORDER BY RENEWAL_MONTH
    """)
    rows = cur.fetchall()
    if rows:
        print('\n  Netting PP by month (trailing 6m):')
        for m, avg, mn, mx in rows:
            print(f'    {m}: avg={avg:.2f}pp  [{mn:.2f}–{mx:.2f}]')

    conn.close()
    print('\nDeploy complete.')


if __name__ == '__main__':
    main()
