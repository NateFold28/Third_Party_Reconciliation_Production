"""Audit Snowflake schema for pipeline-generated objects and flag any that
don't end in _PROD. Also produces the vendor-vs-CW parity report the manual
recon team uses (qty and amount per vendor)."""
from __future__ import annotations

import sys

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")

from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


PIPELINE_PREFIXES = (
    "THIRD_PARTY_RECON",
    "ACRONIS_",
    "AUVIK_",
    "BITDEFENDER_",
    "ESET_",
    "EXIUM_",
    "KEEPIT_",
    "PROOFPOINT_",
    "SENTINELONE_",
    "WEBROOT_",
    "RECON_",
    "FLAG_",
)


def main() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    c = conn.cursor()

    print("=" * 100)
    print("PIPELINE OBJECT NAMING AUDIT")
    print("=" * 100)

    # Pull every TABLE/VIEW in the target schema
    c.execute("""
        SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, LAST_ALTERED
        FROM ANALYTICS_DEV.INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = 'DBT_NFOLD_TRANSFORMATION'
        ORDER BY TABLE_NAME
    """)
    all_objs = c.fetchall()

    pipeline_objs = [
        (name, ttype, rows, alt) for (name, ttype, rows, alt) in all_objs
        if any(name.startswith(p) for p in PIPELINE_PREFIXES)
    ]

    # Break out by _PROD suffix compliance
    has_prod = [o for o in pipeline_objs if o[0].endswith("_PROD")]
    no_prod = [o for o in pipeline_objs if not o[0].endswith("_PROD")]

    print(f"\nTotal pipeline-related objects: {len(pipeline_objs)}")
    print(f"  Ending in _PROD:     {len(has_prod)}")
    print(f"  NOT ending in _PROD: {len(no_prod)}")

    print(f"\n--- Objects MISSING _PROD suffix ({len(no_prod)}) ---")
    print(f"{'NAME':60s} {'TYPE':16s} {'ROWS':>12s}")
    for name, ttype, rows, _ in no_prod:
        rows_str = f"{rows:,}" if rows is not None else "-"
        print(f"  {name:58s} {ttype:16s} {rows_str:>12s}")

    print(f"\n--- Objects WITH _PROD suffix ({len(has_prod)}) ---")
    print(f"{'NAME':60s} {'TYPE':16s} {'ROWS':>12s}")
    for name, ttype, rows, _ in has_prod:
        rows_str = f"{rows:,}" if rows is not None else "-"
        print(f"  {name:58s} {ttype:16s} {rows_str:>12s}")

    # ------------------------------------------------------------------
    # PARITY REPORT — vendor total qty/$ vs CW total qty/$ per vendor
    # This is the metric the manual reconciliation team publishes.
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("VENDOR vs CW PARITY (from THIRD_PARTY_RECON_OUTPUT_PROD)")
    print("=" * 100)

    c.execute("""
        SELECT
          VENDOR,
          COUNT(*)                                                   AS rows_total,
          SUM(CASE WHEN EXCEPTION_TYPE = 'Clear' THEN 1 ELSE 0 END)  AS clear_rows,
          ROUND(100.0 * SUM(CASE WHEN EXCEPTION_TYPE = 'Clear' THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0), 1)                            AS clear_pct,

          ROUND(SUM(COALESCE(VENDOR_QUANTITY,          0)), 0)       AS vendor_qty,
          ROUND(SUM(COALESCE(TOTAL_BILLING_QUANTITY,   0)), 0)       AS cw_qty,
          ROUND(SUM(COALESCE(ABS_QTY_DELTA,            0)), 0)       AS abs_qty_delta_sum,
          ROUND(100.0 * SUM(COALESCE(VENDOR_QUANTITY,        0))
                / NULLIF(SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0)), 0), 1) AS qty_parity_pct,

          ROUND(SUM(COALESCE(VENDOR_AMOUNT,            0)), 0)       AS vendor_amt,
          ROUND(SUM(COALESCE(TOTAL_BILLING_AMOUNT,     0)), 0)       AS cw_amt,
          ROUND(SUM(COALESCE(ABS_AMOUNT_DELTA,         0)), 0)       AS abs_amt_delta_sum,
          ROUND(100.0 * SUM(COALESCE(VENDOR_AMOUNT,        0))
                / NULLIF(SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)), 0), 1)  AS amt_parity_pct
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
        GROUP BY VENDOR
        ORDER BY VENDOR
    """)

    rows = c.fetchall()
    hdr = (
        f"{'VENDOR':13s} "
        f"{'ROWS':>7s} "
        f"{'CLR%':>6s} | "
        f"{'VEN_QTY':>10s} {'CW_QTY':>10s} {'ABS_QDLT':>10s} {'QTY%':>7s} | "
        f"{'VEN_$':>13s} {'CW_$':>13s} {'ABS_$DLT':>13s} {'AMT%':>7s}"
    )
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in rows:
        (v, tot, clr, clrp,
         vq, cq, qd, qp,
         va, ca, ad, ap) = r
        print(
            f"{v:13s} "
            f"{tot:>7,} "
            f"{(clrp or 0):>5.1f}% | "
            f"{int(vq or 0):>10,} {int(cq or 0):>10,} "
            f"{int(qd or 0):>10,} {(qp or 0):>6.1f}% | "
            f"${int(va or 0):>12,} ${int(ca or 0):>12,} "
            f"${int(ad or 0):>12,} {(ap or 0):>6.1f}%"
        )

    # Overall roll-up
    c.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN EXCEPTION_TYPE = 'Clear' THEN 1 ELSE 0 END),
               SUM(COALESCE(VENDOR_QUANTITY, 0)),
               SUM(COALESCE(TOTAL_BILLING_QUANTITY, 0)),
               SUM(COALESCE(ABS_QTY_DELTA, 0)),
               SUM(COALESCE(VENDOR_AMOUNT, 0)),
               SUM(COALESCE(TOTAL_BILLING_AMOUNT, 0)),
               SUM(COALESCE(ABS_AMOUNT_DELTA, 0))
        FROM THIRD_PARTY_RECON_OUTPUT_PROD
    """)
    tot, clr, vq, cq, qd, va, ca, ad = c.fetchone()
    print("-" * len(hdr))
    print(
        f"{'OVERALL':13s} "
        f"{tot:>7,} "
        f"{100.0 * clr / max(tot, 1):>5.1f}% | "
        f"{int(vq):>10,} {int(cq):>10,} "
        f"{int(qd):>10,} "
        f"{(100.0 * vq / max(cq, 1)):>6.1f}% | "
        f"${int(va):>12,} ${int(ca):>12,} "
        f"${int(ad):>12,} "
        f"{(100.0 * va / max(ca, 1)):>6.1f}%"
    )

    conn.close()


if __name__ == "__main__":
    main()
