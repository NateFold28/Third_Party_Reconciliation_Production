"""
Verify PROOFPOINT_RECON_DETAIL (the current template output) passes the
ground-truth acceptance criteria we defined for every vendor rebuild:

  1. Row count within +/- 5% of STANDALONE snapshot
  2. Total vendor $ = SUM(amount) from PROOFPOINT_USAGE  (exact)
  3. Total zuora $ = SUM(zuora_charge_amount) from PROOFPOINT_BILLING_MATCHED (exact)
  4. Total marketplace $ = SUM(marketplace_amount) from PROOFPOINT_MARKETPLACE_BILLING_MATCHED (exact)
  5. OUTCOME_FLAG mix present (not empty)

Read-only. No mutations.
"""
from __future__ import annotations

import sys
from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[4] / "TEMPLATES" / "Python"
sys.path.insert(0, str(_TEMPLATES))

from connection import get_snowflake_connection  # type: ignore  # noqa: E402


def _fetchone(cur, sql: str):
    cur.execute(sql)
    return cur.fetchone()


def _fetchall(cur, sql: str):
    cur.execute(sql)
    return cur.fetchall()


def main() -> int:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    try:
        cur = conn.cursor()

        # ---- Output side: PROOFPOINT_RECON_DETAIL --------------------------
        row_count, vendor_amt, zuora_amt, mp_amt, bill_amt = _fetchone(
            cur,
            """
            SELECT COUNT(*)                              AS row_count,
                   SUM(vendor_amount)                    AS vendor_amt,
                   SUM(zuora_amount)                     AS zuora_amt,
                   SUM(marketplace_amount)               AS mp_amt,
                   SUM(total_billing_amount)             AS bill_amt
            FROM PROOFPOINT_RECON_DETAIL
            """,
        )

        # ---- Ground truth (raw inputs) -------------------------------------
        (usage_amt,) = _fetchone(
            cur,
            "SELECT SUM(amount) FROM PROOFPOINT_USAGE WHERE COALESCE(quantity, 0) <> 0 AND COALESCE(amount, 0) <> 0",
        )
        (raw_zuora_amt,) = _fetchone(
            cur, "SELECT SUM(zuora_charge_amount) FROM PROOFPOINT_BILLING_MATCHED"
        )
        (raw_mp_amt,) = _fetchone(
            cur,
            "SELECT SUM(marketplace_amount) FROM PROOFPOINT_MARKETPLACE_BILLING_MATCHED",
        )

        # ---- STANDALONE snapshot ------------------------------------------
        snap_row_count, snap_vendor_amt, snap_bill_amt = _fetchone(
            cur,
            """
            SELECT COUNT(*),
                   SUM(vendor_amount),
                   SUM(total_billing_amount)
            FROM THIRD_PARTY_STANDALONE_RECON_DETAIL__PROOFPOINT_SNAPSHOT_20260823
            """,
        )

        # ---- OUTCOME_FLAG mix ---------------------------------------------
        outcome_mix = _fetchall(
            cur,
            """
            SELECT outcome_flag, COUNT(*) AS n
            FROM PROOFPOINT_RECON_DETAIL
            GROUP BY 1
            ORDER BY n DESC
            """,
        )

        def _fmt(v):
            if v is None:
                return "NULL"
            return f"${float(v):,.2f}"

        def _pct(a, b):
            if not b:
                return "n/a"
            return f"{(float(a) - float(b)) / float(b) * 100:+.2f}%"

        print("=" * 78)
        print("PROOFPOINT TEMPLATE ACCEPTANCE CHECK")
        print("=" * 78)
        print()
        print(f"{'metric':<40} {'template':>18} {'ground truth':>18}")
        print("-" * 78)
        print(f"{'row count':<40} {row_count:>18,} {snap_row_count:>18,}   snap")
        print(f"{'vendor $ (RECON_DETAIL)':<40} {_fmt(vendor_amt):>18} {_fmt(usage_amt):>18}   {_pct(vendor_amt, usage_amt)}")
        print(f"{'zuora $ (RECON_DETAIL)':<40} {_fmt(zuora_amt):>18} {_fmt(raw_zuora_amt):>18}   {_pct(zuora_amt, raw_zuora_amt)}")
        print(f"{'marketplace $ (RECON_DETAIL)':<40} {_fmt(mp_amt):>18} {_fmt(raw_mp_amt):>18}   {_pct(mp_amt, raw_mp_amt)}")
        print(f"{'billing $ vs snap $':<40} {_fmt(bill_amt):>18} {_fmt(snap_bill_amt):>18}   {_pct(bill_amt, snap_bill_amt)}")
        print()
        print("OUTCOME_FLAG mix:")
        for flag, n in outcome_mix:
            print(f"  {flag:<45} {n:>8,}")
        print()

        # ---- Verdicts ------------------------------------------------------
        verdicts = []

        def _within(a, b, tol_pct):
            if a is None or b is None:
                return False
            if float(b) == 0:
                return float(a) == 0
            return abs(float(a) - float(b)) / abs(float(b)) <= tol_pct

        verdicts.append(("row count within +/-5% of snapshot",
                         _within(row_count, snap_row_count, 0.05)))
        verdicts.append(("vendor $ matches PROOFPOINT_USAGE exactly (<0.1%)",
                         _within(vendor_amt, usage_amt, 0.001)))
        verdicts.append(("zuora $ matches PROOFPOINT_BILLING_MATCHED exactly",
                         _within(zuora_amt, raw_zuora_amt, 0.001)))
        verdicts.append(("marketplace $ matches PROOFPOINT_MP_BILLING_MATCHED exactly",
                         _within(mp_amt, raw_mp_amt, 0.001)))
        verdicts.append(("outcome_flag mix non-empty",
                         len(outcome_mix) > 1))

        print("Verdicts:")
        all_ok = True
        for msg, ok in verdicts:
            mark = "OK  " if ok else "FAIL"
            print(f"  [{mark}] {msg}")
            all_ok = all_ok and ok

        print()
        print("=" * 78)
        if all_ok:
            print("RESULT: PROOFPOINT TEMPLATE VERIFIED  --  safe to use as pattern")
        else:
            print("RESULT: template FAILS one or more checks -- inspect before mirroring")
        print("=" * 78)
        return 0 if all_ok else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
