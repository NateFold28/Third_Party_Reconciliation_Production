"""
Probe exactly what values are in the app tables and what the app displays.
Checks every relevant column at both portfolio and contract grain.
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection(); cur = conn.cursor()
for s in ["USE ROLE STREAMLIT_USER", "USE WAREHOUSE REPORTING_WH",
          "USE DATABASE STREAMLIT_APPS", "USE SCHEMA DBO"]:
    cur.execute(s)
print("Connected\n")

# ── What columns exist in the contract detail table? ─────────────────────────
cur.execute("""
    SELECT COLUMN_NAME, DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'DBO'
      AND TABLE_NAME = 'V5_SANDBOX_APP_CONTRACT_DETAIL'
    ORDER BY ORDINAL_POSITION
""")
cols = cur.fetchall()
print("=== V5_SANDBOX_APP_CONTRACT_DETAIL columns ===")
for c in cols:
    print(f"  {c[0]:<45} {c[1]}")

# Determine which EFFECTIVE / BOARD columns actually exist in the table
col_names_contract = [c[0] for c in cols]
def _pick_column(candidates):
    for n in candidates:
        if n in col_names_contract:
            return n
    return None

EFFECTIVE_COL_ACTUAL = _pick_column(["EFFECTIVE_FORECAST_ML_ONLY", "EFFECTIVE_FORECAST", "EFFECTIVE_FORECAST_ML"])
BOARD_COL_ACTUAL = _pick_column(["BOARD_RENEWAL_FORECAST", "FINANCE_FORECAST"])
print(f"Using EFFECTIVE column: {EFFECTIVE_COL_ACTUAL}; Using BOARD column: {BOARD_COL_ACTUAL}")
if not EFFECTIVE_COL_ACTUAL:
    print("ERROR: No EFFECTIVE_FORECAST variant found in V5_SANDBOX_APP_CONTRACT_DETAIL")
    conn.close(); sys.exit(1)
if not BOARD_COL_ACTUAL:
    print("ERROR: No BOARD_RENEWAL_FORECAST or FINANCE_FORECAST column found in V5_SANDBOX_APP_CONTRACT_DETAIL")
    conn.close(); sys.exit(1)

# ── What columns exist in the monthly aligned table? ─────────────────────────
print()
cur.execute("""
    SELECT COLUMN_NAME, DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'DBO'
      AND TABLE_NAME = 'V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED'
    ORDER BY ORDINAL_POSITION
""")
cols2 = cur.fetchall()
print("=== V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED columns ===")
for c in cols2:
    print(f"  {c[0]:<45} {c[1]}")

# ── Pull all forecast-related columns for the last 6 settled months ───────────
print()
print("=== CONTRACT DETAIL — all forecast columns (portfolio rollup, last 6 settled months) ===")
cur.execute(f"""
        SELECT
                DATE_TRUNC('month', RENEWAL_MONTH) AS MO,
                SUM(ATR)                                    AS ATR,
                SUM(COALESCE(ACTUAL_RETAINED_ARR, 0))       AS ACTUAL,
                SUM(COALESCE(ML_FORECAST, 0))               AS ML_FORECAST,
                SUM(COALESCE({EFFECTIVE_COL_ACTUAL}, 0))    AS EFFECTIVE_FORECAST,
                SUM(COALESCE(FINANCE_FORECAST, 0))          AS FINANCE_FORECAST,
                SUM(COALESCE({BOARD_COL_ACTUAL}, 0))        AS BOARD_RENEWAL_FORECAST
        FROM V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE IS_MATURE = TRUE AND IS_MATURED_MONTH = TRUE
            AND COALESCE(ATR, 0) > 1000
            AND RUN_ID != 'V5_ANCHOR_FALLBACK'
            AND RENEWAL_MONTH >= DATEADD('month', -6, DATE_TRUNC('month', CURRENT_DATE()))
        GROUP BY 1 ORDER BY 1
""")
rows = cur.fetchall()
col_names = [d[0] for d in cur.description]
df = pd.DataFrame(rows, columns=col_names)
for c in col_names[1:]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
df["MO_STR"] = df["MO"].astype(str).str[:7]

print(f"\n  {'Month':<8}  {'ATR $M':>7}  {'Act%':>6}  {'ML_F%':>7}  {'EFF_F%':>7}  {'FIN_F%':>7}  {'BOARD_F%':>9}")
print("  " + "-" * 62)
for _, r in df.iterrows():
    atr = r["ATR"]
    def pct(col): return r[col]/atr*100 if atr else 0
    print(f"  {r['MO_STR']:<8}  ${atr/1e6:>6.2f}M"
          f"  {pct('ACTUAL'):>5.1f}%"
          f"  {pct('ML_FORECAST'):>6.1f}%"
          f"  {pct('EFFECTIVE_FORECAST'):>6.1f}%"
          f"  {pct('FINANCE_FORECAST'):>6.1f}%"
          f"  {pct('BOARD_RENEWAL_FORECAST'):>8.1f}%")

# ── Same for monthly aligned table ───────────────────────────────────────────
print()
print("=== PROD_MONTHLY_ALIGNED — all columns (last 6 months) ===")
try:
    cur.execute("""
        SELECT *
        FROM V5_SANDBOX_APP_PROD_MONTHLY_ALIGNED
        WHERE RENEWAL_MONTH >= DATEADD('month', -6, DATE_TRUNC('month', CURRENT_DATE()))
        ORDER BY RENEWAL_MONTH
    """)
    rows2 = cur.fetchall()
    col_names2 = [d[0] for d in cur.description]
    df2 = pd.DataFrame(rows2, columns=col_names2)
    print(f"  Columns: {col_names2}")
    print()
    print(df2.to_string(index=False, max_cols=20))
except Exception as e:
    print(f"  ERROR: {e}")

# ── Forward months — what does the app actually display? ──────────────────────
print()
print("=== FORWARD Jul-Sep 2026 — all forecast columns (portfolio rollup) ===")
cur.execute(f"""
        SELECT
                DATE_TRUNC('month', RENEWAL_MONTH) AS MO,
                SUM(ATR)                                    AS ATR,
                SUM(COALESCE(ML_FORECAST, 0))               AS ML_FORECAST,
                SUM(COALESCE({EFFECTIVE_COL_ACTUAL}, 0))    AS EFFECTIVE_FORECAST,
                SUM(COALESCE(FINANCE_FORECAST, 0))          AS FINANCE_FORECAST,
                SUM(COALESCE({BOARD_COL_ACTUAL}, 0))        AS BOARD_RENEWAL_FORECAST
        FROM V5_SANDBOX_APP_CONTRACT_DETAIL
        WHERE RENEWAL_MONTH BETWEEN '2026-07-01' AND '2026-09-30'
            AND IS_MATURE = FALSE
            AND RUN_ID != 'V5_ANCHOR_FALLBACK'
            AND COALESCE(ATR, 0) > 1000
        GROUP BY 1 ORDER BY 1
""")
rows3 = cur.fetchall()
col_names3 = [d[0] for d in cur.description]
df3 = pd.DataFrame(rows3, columns=col_names3)
for c in col_names3[1:]:
    df3[c] = pd.to_numeric(df3[c], errors="coerce").fillna(0)
df3["MO_STR"] = df3["MO"].astype(str).str[:7]

print(f"\n  {'Month':<8}  {'ATR $M':>7}  {'ML_F%':>7}  {'EFF_F%':>7}  {'FIN_F%':>7}  {'BOARD_F%':>9}")
print("  " + "-" * 55)
for _, r in df3.iterrows():
    atr = r["ATR"]
    def pct(col): return r[col]/atr*100 if atr else 0
    print(f"  {r['MO_STR']:<8}  ${atr/1e6:>6.2f}M"
          f"  {pct('ML_FORECAST'):>6.1f}%"
          f"  {pct('EFFECTIVE_FORECAST'):>6.1f}%"
          f"  {pct('FINANCE_FORECAST'):>6.1f}%"
          f"  {pct('BOARD_RENEWAL_FORECAST'):>8.1f}%")

conn.close()
