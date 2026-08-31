"""Ephemeral simulation: quantify normalized-key recovery per vendor."""
from TEMPLATES.Python.connection import get_snowflake_connection

conn = get_snowflake_connection(
    role="DEVELOPER", warehouse="REPORTING_WH",
    database="ANALYTICS_DEV", schema="DBT_NFOLD_TRANSFORMATION",
)
cur = conn.cursor()

VENDORS = [
    ("Webroot",     "WEBROOT_USAGE",     None),
    ("KeepIT",      "KEEPIT_USAGE",      None),
    ("Bitdefender", "THIRD_PARTY_RECON_VENDOR_USAGE_PROD", "BITDEFENDER"),
    ("ESET",        "ESET_USAGE",        None),
    ("Acronis",     "ACRONIS_USAGE",     None),
]

for vendor, usage_table, filt_vendor in VENDORS:
    if filt_vendor:
        vendor_where = f"WHERE UPPER(vendor) = '{filt_vendor}' AND (COALESCE(quantity,0)<>0 OR COALESCE(amount,0)<>0)"
    else:
        vendor_where = "WHERE COALESCE(quantity,0)<>0 OR COALESCE(amount,0)<>0"
    q = f"""
    WITH usage_partner AS (
        SELECT DISTINCT
            billing_month::DATE AS billing_month,
            vendor_partner_name,
            UPPER(TRIM(vendor_partner_name)) AS exact_key,
            TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(vendor_partner_name), '[^a-z0-9]+', ' '), '\\s+', ' ')) AS norm_key
        FROM {usage_table}
        {vendor_where}
    ),
    joined AS (
        SELECT
            u.*,
            pe.sf_id AS exact_sf_id,
            pn.sf_id AS norm_sf_id
        FROM usage_partner u
        LEFT JOIN RECON_PARTNER_MAP_MONTHLY pe
            ON pe.billing_month = u.billing_month
           AND UPPER(TRIM(pe.partner_name)) = u.exact_key
        LEFT JOIN V_RECON_PARTNER_MAP_MONTHLY_NORM pn
            ON pn.billing_month = u.billing_month
           AND pn.PARTNER_NAME_NORMALIZED = u.norm_key
    )
    SELECT
        COUNT(*) AS total_distinct_partner_months,
        COUNT_IF(exact_sf_id IS NOT NULL) AS exact_match,
        COUNT_IF(exact_sf_id IS NULL AND norm_sf_id IS NOT NULL) AS norm_recovers,
        COUNT_IF(exact_sf_id IS NULL AND norm_sf_id IS NULL) AS still_unmapped
    FROM joined
    """
    cur.execute(q)
    row = cur.fetchone()
    total, exact_m, norm_rec, still = row
    print(f"{vendor:12s}: total={total:6d}  exact={exact_m:6d}  norm_recovers=+{norm_rec:4d}  still_unmapped={still:5d}")
