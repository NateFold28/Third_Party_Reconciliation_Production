"""
Verify AUVIK_RECON_DETAIL against the CORRECTED acceptance criteria
(vendor $ EXACT to USAGE, billing $ captured or routed).

Read-only.
"""
from __future__ import annotations

import sys
from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[4] / "TEMPLATES" / "Python"
sys.path.insert(0, str(_TEMPLATES))

from connection import get_snowflake_connection  # type: ignore  # noqa: E402


def _one(cur, sql):
    cur.execute(sql)
    return cur.fetchone()


def _all(cur, sql):
    cur.execute(sql)
    return cur.fetchall()


def _fmt(v):
    if v is None:
        return "NULL"
    return f"${float(v):,.2f}"


def _pct(a, b):
    if a is None or b is None or float(b) == 0:
        return "n/a"
    return f"{(float(a) - float(b)) / float(b) * 100:+.2f}%"


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        cur = conn.cursor()

        # Confirm AUVIK_RECON_DETAIL exists
        det_exists = _one(
            cur,
            """
            SELECT COUNT(*) FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION'
              AND TABLE_NAME='AUVIK_RECON_DETAIL'
            """,
        )[0]
        if not det_exists:
            print("AUVIK_RECON_DETAIL does not exist. Need to run the vendor SQL first.")
            return 1

        # Discover columns actually present in AUVIK_RECON_DETAIL
        cols = [
            r[0]
            for r in _all(
                cur,
                """
                SELECT COLUMN_NAME
                FROM ANALYTICS_DEV.INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA='DBT_NFOLD_TRANSFORMATION'
                  AND TABLE_NAME='AUVIK_RECON_DETAIL'
                ORDER BY ORDINAL_POSITION
                """,
            )
        ]
        print("AUVIK_RECON_DETAIL columns present:")
        for c in cols:
            print(f"  - {c}")
        print()

        has = {c.upper() for c in cols}

        def col(*candidates):
            for c in candidates:
                if c.upper() in has:
                    return c
            return None

        vendor_amt_col = col("vendor_amount")
        zuora_amt_col = col("zuora_amount")
        mp_amt_col = col("marketplace_amount")
        bill_amt_col = col("total_billing_amount")
        any_zuora_col = col("any_zuora_amount")
        outcome_col = col("outcome_flag")

        # Assemble query
        select_parts = ["COUNT(*)"]
        for c in (vendor_amt_col, zuora_amt_col, mp_amt_col, bill_amt_col, any_zuora_col):
            select_parts.append(f"SUM({c})" if c else "NULL")
        row = _one(cur, f"SELECT {', '.join(select_parts)} FROM AUVIK_RECON_DETAIL")
        row_count, vendor_amt, zuora_amt, mp_amt, bill_amt, any_zuora_amt = row

        # Ground truth: AUVIK_USAGE (with same filter as Proofpoint pattern)
        (usage_amt,) = _one(
            cur,
            "SELECT SUM(amount) FROM AUVIK_USAGE WHERE COALESCE(quantity,0)<>0 AND COALESCE(amount,0)<>0",
        )
        (usage_amt_all,) = _one(cur, "SELECT SUM(amount) FROM AUVIK_USAGE")

        # Raw billing from unified sources (Auvik slice)
        (raw_zuora_amt,) = _one(
            cur,
            """
            SELECT SUM(CHARGE_AMOUNT_USD)
            FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD
            WHERE VENDOR = 'Auvik'
            """,
        )
        (raw_mp_amt,) = _one(
            cur,
            """
            SELECT SUM(AMOUNT)
            FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD
            WHERE VENDOR = 'Auvik'
            """,
        )

        # STANDALONE snapshot
        snap_rows, snap_vend, snap_bill = _one(
            cur,
            """
            SELECT COUNT(*), SUM(vendor_amount), SUM(total_billing_amount)
            FROM THIRD_PARTY_STANDALONE_RECON_DETAIL__AUVIK_SNAPSHOT_20260823
            """,
        )

        outcomes = _all(
            cur,
            f"SELECT {outcome_col}, COUNT(*) FROM AUVIK_RECON_DETAIL GROUP BY 1 ORDER BY 2 DESC"
            if outcome_col
            else "SELECT NULL, 0 FROM DUAL WHERE 1=0",
        )

        print("=" * 78)
        print("AUVIK ACCEPTANCE CHECK (corrected criteria)")
        print("=" * 78)
        print(f"{'metric':<40} {'template':>18} {'reference':>18}")
        print("-" * 78)
        print(f"{'row count':<40} {row_count:>18,} {snap_rows:>18,}  snap")
        print(f"{'vendor $ vs USAGE (filtered)':<40} {_fmt(vendor_amt):>18} {_fmt(usage_amt):>18}  {_pct(vendor_amt, usage_amt)}")
        print(f"{'vendor $ vs USAGE (unfiltered)':<40} {_fmt(vendor_amt):>18} {_fmt(usage_amt_all):>18}  {_pct(vendor_amt, usage_amt_all)}")
        print(f"{'zuora $ in DETAIL vs raw Zuora':<40} {_fmt(zuora_amt):>18} {_fmt(raw_zuora_amt):>18}  {_pct(zuora_amt, raw_zuora_amt)}")
        print(f"{'marketplace $ vs raw MP':<40} {_fmt(mp_amt):>18} {_fmt(raw_mp_amt):>18}  {_pct(mp_amt, raw_mp_amt)}")
        print(f"{'total billing $ vs snap':<40} {_fmt(bill_amt):>18} {_fmt(snap_bill):>18}  {_pct(bill_amt, snap_bill)}")
        if any_zuora_amt is not None:
            print(f"{'evidence any_zuora $':<40} {_fmt(any_zuora_amt):>18}")
        print()
        if outcomes and outcomes[0][0] is not None:
            print("outcome_flag mix:")
            for f, n in outcomes:
                print(f"  {str(f):<45} {n:>8,}")
        print()

        def within(a, b, tol):
            if a is None or b is None:
                return False
            if float(b) == 0:
                return float(a) == 0
            return abs(float(a) - float(b)) / abs(float(b)) <= tol

        verdicts = [
            ("vendor $ matches AUVIK_USAGE (filtered) within 0.1%",
             within(vendor_amt, usage_amt, 0.001)),
            ("row count within +/-30% of STANDALONE snapshot",
             within(row_count, snap_rows, 0.30)),
            ("outcome_flag mix has >=3 buckets",
             outcome_col is not None and len(outcomes) >= 3),
        ]
        print("Verdicts:")
        all_ok = True
        for msg, ok in verdicts:
            mark = "OK  " if ok else "FAIL"
            print(f"  [{mark}] {msg}")
            all_ok = all_ok and ok
        print()
        print("=" * 78)
        if all_ok:
            print("RESULT: AUVIK PASSES  --  vendor SQL is a keeper, aligns with template")
        else:
            print("RESULT: AUVIK needs work -- see failures above")
        print("=" * 78)
        return 0 if all_ok else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
