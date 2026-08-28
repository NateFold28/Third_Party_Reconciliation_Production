"""
Deploy SP_V5_SANDBOX_DAILY_BLEND (updated to rebuild derived tables) and then
run SP_V5_SANDBOX_DAILY_REFRESH to validate the full pipeline end-to-end.

Steps:
  1. Extract and deploy SP_V5_SANDBOX_DAILY_BLEND from PROD_V1_5_orchestrator.sql
  2. Call SP_V5_SANDBOX_DAILY_REFRESH (which calls the blend + reconciliation snapshot)
  3. Smoke-test every app table for row counts and freshness
"""
import sys, re, pandas as pd
sys.path.insert(0, '.')
from TEMPLATES.Python.connection import get_snowflake_connection

ORCHESTRATOR = r'PROJECTS\Production_Renewal_Forecasting_Pipeline\sql\pipeline\PROD_V1_5_orchestrator.sql'


def _extract_blend_sp(full_sql: str) -> str | None:
    m = re.search(
        r'(CREATE OR REPLACE PROCEDURE STREAMLIT_APPS\.DBO\.SP_V5_SANDBOX_DAILY_BLEND'
        r'\(\).*?END;\s*\$\$)',
        full_sql, re.DOTALL
    )
    return m.group(1).strip() if m else None


def main():
    with open(ORCHESTRATOR, encoding='utf-8') as f:
        full_sql = f.read()

    blend_ddl = _extract_blend_sp(full_sql)

    conn = get_snowflake_connection()
    cur  = conn.cursor()
    for s in ['USE ROLE STREAMLIT_USER', 'USE WAREHOUSE REPORTING_WH',
              'USE DATABASE STREAMLIT_APPS', 'USE SCHEMA DBO']:
        cur.execute(s)
    print('Connected ✓\n')

    # ── 1. Deploy updated blend SP ───────────────────────────────────────────
    if blend_ddl:
        has_recon  = 'CONTRACT_RECONCILIATION' in blend_ddl
        has_clm    = 'CONTRACT_LVL_MONTHLY'    in blend_ddl
        has_prod   = 'PROD_MONTHLY_ALIGNED'    in blend_ddl
        print(f"Blend SP DDL ({len(blend_ddl):,} chars)"
              f" — PROD_MONTHLY_ALIGNED: {'✓' if has_prod else '⚠'}"
              f" | CONTRACT_LVL_MONTHLY: {'✓' if has_clm else '⚠'}"
              f" | CONTRACT_RECONCILIATION: {'✓' if has_recon else '⚠'}")
        cur.execute(blend_ddl)
        print('  SP_V5_SANDBOX_DAILY_BLEND deployed ✓')
    else:
        print('  ⚠ Could not extract blend SP — aborting')
        conn.close()
        return

    # ── 2. Run full daily refresh (calls blend + reconciliation snapshot) ────
    print('\nCalling SP_V5_SANDBOX_DAILY_REFRESH()...')
    cur.execute('CALL STREAMLIT_APPS.DBO.SP_V5_SANDBOX_DAILY_REFRESH()')
    refresh_result = cur.fetchone()
    refresh_msg = refresh_result[0] if refresh_result else 'no result'
    print(f'  Result: {refresh_msg}')
    ok = refresh_msg.startswith('OK') or refresh_msg.startswith('SKIPPED')
    if not ok:
        print('  ⚠ Daily refresh reported a problem — check pipeline log')

    # ── 3. Smoke tests on all app tables ────────────────────────────────────
    print('\n── Smoke tests ─────────────────────────────────────────────────────')
    tables = [
        ('V5_SANDBOX_APP_CONTRACT_DETAIL',        'RENEWAL_MONTH'),
        ('V5_SANDBOX_APP_MONTHLY_ROLLUP',         None),
        ('V5_SANDBOX_APP_CONTRACT_LVL_MONTHLY',   'RENEWAL_MONTH'),
        ('V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED',   'RENEWAL_MONTH'),
        ('V5_SANDBOX_APP_CONTRACT_RECONCILIATION','RENEWAL_MONTH'),
        ('V5_SANDBOX_APP_BACKTEST',               None),
        ('V5_SANDBOX_APP_RUNS',                   None),
    ]
    for tbl, date_col in tables:
        cur.execute(f'SELECT COUNT(*) AS n FROM {tbl}')
        n = cur.fetchone()[0]
        if date_col:
            cur.execute(f"SELECT MAX({date_col}) AS mx FROM {tbl}")
            mx = cur.fetchone()[0]
            print(f'  {tbl}: {n:,} rows, latest {date_col}={mx}')
        else:
            print(f'  {tbl}: {n:,} rows')

    # Calibration check — CHURN_PCT distinct values per segment
    print('\n── Calibration check ───────────────────────────────────────────────')
    cur.execute("""
        SELECT SEGMENT,
               COUNT(DISTINCT CHURN_PCT)  AS n_distinct_churn,
               ROUND(STDDEV(CHURN_PCT),3) AS stddev_churn
        FROM V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE RUN_ID <> 'V5_ANCHOR_FALLBACK'
        GROUP BY SEGMENT
        ORDER BY SEGMENT
    """)
    for seg, n_dist, sd in cur.fetchall():
        status = '✓' if n_dist > 5 else '⚠ FLAT — calibration broken'
        print(f'  {seg}: {n_dist} distinct CHURN_PCT values, stddev={sd}  {status}')

    # Netting spot-check
    print('\n── Netting spot-check (trailing 6m) ────────────────────────────────')
    cur.execute("""
        SELECT RENEWAL_MONTH, ROUND(AVG(NETTING_PP),2) AS avg_netting
        FROM V5_SANDBOX_APP_CONTRACT_RECONCILIATION
        WHERE RENEWAL_MONTH < DATE_TRUNC('MONTH', CURRENT_DATE())
          AND RENEWAL_MONTH >= DATEADD('MONTH', -6, DATE_TRUNC('MONTH', CURRENT_DATE()))
          AND NETTING_PP IS NOT NULL
        GROUP BY RENEWAL_MONTH ORDER BY RENEWAL_MONTH
    """)
    for m, avg in cur.fetchall():
        print(f'  {m}: {avg:+.2f}pp')

    conn.close()
    print('\nPipeline run complete.')


if __name__ == '__main__':
    main()
