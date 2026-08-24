"""Diagnose KeepIT duplication: $182M vs expected ~$1-2M."""
from __future__ import annotations

import sys

sys.path.insert(0, r"C:\Users\Nate.Fold\projects")
from TEMPLATES.Python.connection import get_snowflake_connection  # noqa: E402


def main() -> None:
    conn = get_snowflake_connection(
        role="DEVELOPER",
        warehouse="REPORTING_WH",
        database="ANALYTICS_DEV",
        schema="DBT_NFOLD_TRANSFORMATION",
    )
    c = conn.cursor()

    print("=== KEEPIT_RECON_DETAIL total ===")
    c.execute(
        "SELECT COUNT(*), SUM(VENDOR_AMOUNT), SUM(ZUORA_AMOUNT), SUM(MARKETPLACE_AMOUNT) FROM KEEPIT_RECON_DETAIL"
    )
    r = c.fetchone()
    print(f"  rows={r[0]:,}  vendor_amt=${r[1] or 0:,.0f}  zuora_amt=${r[2] or 0:,.0f}  mp_amt=${r[3] or 0:,.0f}")

    print("\n=== KEEPIT_RECON_DETAIL by billing month ===")
    c.execute(
        """SELECT BILLING_MONTH, COUNT(*) AS n_rows,
                  ROUND(SUM(VENDOR_AMOUNT), 0) AS vendor_amt,
                  ROUND(SUM(ZUORA_AMOUNT), 0) AS zuora_amt,
                  COUNT(DISTINCT SF_ID) AS n_partners
           FROM KEEPIT_RECON_DETAIL GROUP BY 1 ORDER BY 1"""
    )
    for r in c.fetchall():
        print(
            f"  {r[0]}  rows={r[1]:>6,}  vendor=${r[2] or 0:>12,.0f}  zuora=${r[3] or 0:>12,.0f}  partners={r[4]:>4,}"
        )

    print("\n=== Zuora source (raw) for KeepIT ===")
    c.execute(
        "SELECT COUNT(*), COUNT(DISTINCT SF_ID), MIN(BILLING_MONTH), MAX(BILLING_MONTH), "
        "ROUND(SUM(CHARGE_AMOUNT_USD), 0) FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD WHERE VENDOR='KeepIT'"
    )
    r = c.fetchone()
    print(f"  rows={r[0]:,}  partners={r[1]:,}  {r[2]} .. {r[3]}  total_charge=${r[4] or 0:,.0f}")

    print("\n=== Zuora source by billing month ===")
    c.execute(
        """SELECT BILLING_MONTH, COUNT(*), ROUND(SUM(CHARGE_AMOUNT_USD), 0)
           FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD WHERE VENDOR='KeepIT'
           GROUP BY 1 ORDER BY 1"""
    )
    for r in c.fetchall():
        print(f"  {r[0]}  rows={r[1]:>5,}  charge=${r[2] or 0:>12,.0f}")

    print("\n=== Marketplace source for KeepIT ===")
    c.execute(
        "SELECT COUNT(*), ROUND(SUM(AMOUNT), 0) FROM THIRD_PARTY_RECON_SOURCE_MARKETPLACE_PROD WHERE VENDOR='KeepIT'"
    )
    r = c.fetchone()
    print(f"  rows={r[0]:,}  amt=${r[1] or 0:,.0f}")

    print("\n=== Sample offender rows (high dollar exceptions) ===")
    c.execute(
        """SELECT BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME, OUTCOME_FLAG,
                  ROUND(VENDOR_AMOUNT, 0), ROUND(ZUORA_AMOUNT, 0),
                  ROUND(ABS_AMOUNT_DELTA, 0),
                  ZUORA_QUANTITY, VENDOR_QUANTITY,
                  ARRAY_TO_STRING(CW_SKUS, '|'), ARRAY_TO_STRING(ZUORA_SKUS, '|'),
                  VENDOR_SOURCE_ROW_COUNT
           FROM KEEPIT_RECON_DETAIL
           WHERE OUTCOME_FLAG <> 'Clear'
           ORDER BY ABS_AMOUNT_DELTA DESC NULLS LAST
           LIMIT 10"""
    )
    for r in c.fetchall():
        print(
            f"  {r[0]} sf={r[1]} '{r[2]}' flag='{r[3]}' vend=${r[4] or 0:,.0f} "
            f"zuora=${r[5] or 0:,.0f} delta=${r[6] or 0:,.0f} zq={r[7]} vq={r[8]} "
            f"cw_sku={r[9]!r} zuora_sku={r[10]!r} vendor_rows={r[11]}"
        )

    print("\n=== Duplicated CW Invoice detail — top by amount ===")
    c.execute(
        """SELECT BILLING_MONTH, SF_ID, VENDOR_PARTNER_NAME,
                  ROUND(ZUORA_AMOUNT, 0) AS zuora_amt,
                  ZUORA_QUANTITY, VENDOR_SOURCE_ROW_COUNT,
                  ARRAY_TO_STRING(ZUORA_SKUS, '|')
           FROM KEEPIT_RECON_DETAIL
           WHERE OUTCOME_FLAG = 'Duplicated CW Invoice'
           ORDER BY ZUORA_AMOUNT DESC NULLS LAST
           LIMIT 10"""
    )
    for r in c.fetchall():
        print(
            f"  {r[0]} sf={r[1]} '{r[2]}' zuora=${r[3] or 0:,.0f} zq={r[4]} vendor_rows={r[5]} skus={r[6]!r}"
        )

    print("\n=== Sample Zuora raw for one huge-dollar partner ===")
    c.execute(
        """SELECT SF_ID FROM KEEPIT_RECON_DETAIL 
           WHERE OUTCOME_FLAG='Duplicated CW Invoice'
           ORDER BY ZUORA_AMOUNT DESC NULLS LAST LIMIT 1"""
    )
    top_sf = c.fetchone()
    if top_sf:
        top_sf = top_sf[0]
        print(f"  top offender SF_ID: {top_sf}")
        c.execute(
            f"""SELECT BILLING_MONTH, PRODUCT_SKU, CHARGE_NAME, QTY, UNIT_PRICE_USD, CHARGE_AMOUNT_USD, INVOICE_NUMBER
                FROM THIRD_PARTY_RECON_SOURCE_ZUORA_PROD 
                WHERE VENDOR='KeepIT' AND SF_ID='{top_sf}' 
                ORDER BY BILLING_MONTH, PRODUCT_SKU LIMIT 20"""
        )
        for r in c.fetchall():
            print(f"    {r[0]} sku={r[1]!r} charge='{r[2]}' qty={r[3]} unit=${r[4]} amt=${r[5]:,.0f} inv={r[6]}")


if __name__ == "__main__":
    main()
