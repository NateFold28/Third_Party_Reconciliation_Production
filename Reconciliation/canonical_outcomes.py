"""Single source of truth for reconciliation outcome classification.

All published pipeline and app outcomes must come from ``strict_outcome_case``.
Vendor-native flags are evidence inputs only and are never authoritative Clear
classifications.
"""
from __future__ import annotations


def structural_evidence_case(native_flag: str = "OUTCOME_FLAG") -> str:
    """Normalize native evidence into marketplace or mapping classification."""
    raw = f"UPPER(TRIM(SPLIT_PART(COALESCE({native_flag}, ''), '|', 1)))"
    return f"""
CASE
    WHEN {raw} IN ('UNMAPPED PARTNER', 'PARTNER_MAPPING_REQUIRED',
                        'VENDOR SKU, NO CW SKU', 'VENDOR_ADDON_NO_CW_SKU',
                        'VENDOR_PRODUCT_NO_CW_SKU', 'VENDOR_SKU_NO_CW_SKU',
                        'SKU_MISMATCH_BILLING_ON_OTHER_SKU', 'UNMAPPED SKU',
                        'CW SKU, NO VENDOR SKU', 'CW_ONLY_ADDON_NO_VENDOR',
                        'CW_SKU_NO_VENDOR_SKU')
      OR {raw} LIKE 'UNMAPPED PARTNER%'
        THEN 'UNMAPPED_PARTNER'
    WHEN {raw} IN ('MARKETPLACE TIMING', 'MARKETPLACE BILLING DELAY',
                        'MARKETPLACE_BILLING_NO_VENDOR', 'BILLING_TIMING_ADJACENT_MONTH')
        THEN 'MARKETPLACE_BILLING_DELAY'
    ELSE NULL
END
""".strip()


def strict_outcome_case(
    *,
    structural_evidence_code: str = "STRUCTURAL_EVIDENCE_CODE",
    sf_id: str = "SF_ID",
    vendor_amount: str = "VENDOR_AMOUNT",
    cw_amount: str = "TOTAL_BILLING_AMOUNT",
    api_quantity: str = "API_QUANTITY",
    avg_api_quantity: str = "AVG_API_QUANTITY",
) -> str:
    """Return the one canonical, mutually exclusive Snowflake CASE expression.

    Marketplace timing and mapping evidence are structural classifications.
    All other outcomes are monetary. Only point-in-time API quantity can drive
    the API bucket; average API quantity is retained as exploratory output only.
    """
    structural_code = f"UPPER(TRIM(COALESCE({structural_evidence_code}, '')))"
    # Keep this argument for compatibility with existing callers, but do not
    # reference it in classification SQL. AVG API is exploratory only.
    _ = avg_api_quantity
    api_present = f"COALESCE({api_quantity}, 0) > 0"
    no_api = f"COALESCE({api_quantity}, 0) <= 0"

    return f"""
CASE
    -- Proofpoint pattern: prior-month marketplace evidence explains a current
    -- no-billing case before ordinary monetary classification.
    WHEN {structural_code} = 'MARKETPLACE_BILLING_DELAY'
        THEN 'Marketplace Billing Delay'

    -- CW coverage is Clear regardless of other native evidence labels.
    WHEN COALESCE({vendor_amount}, 0) > 0
     AND COALESCE({cw_amount}, 0) > 0
     AND COALESCE({cw_amount}, 0) >= COALESCE({vendor_amount}, 0)
        THEN 'Clear'

    -- Partner/SKU mapping failures remain explicit structural classifications.
    WHEN {sf_id} IS NULL
            OR {structural_code} IN (
                'UNMAPPED_PARTNER',
                'VENDOR_SKU_NO_CW_SKU',
                'CW_SKU_NO_VENDOR_SKU'
            )
      OR STARTSWITH(UPPER(TRIM(COALESCE({sf_id}, ''))), 'UNMAPPED_')
      OR STARTSWITH(UPPER(TRIM(COALESCE({sf_id}, ''))), 'UNMAPPED-')
      OR UPPER(TRIM(COALESCE({sf_id}, ''))) IN ('', 'UNKNOWN', 'NONE', 'UNMAPPED', 'NULL')
        THEN 'Unmapped Partner'

    -- Duplicate billing intentionally remains a side flag. Do not enable this
    -- primary category until the duplicate-source audit is complete.
    -- WHEN <duplicate billing side signal>
    --     THEN 'Duplicated CW Invoice'

    -- API-confirmed shortfall includes zero or positive CW billing.
    WHEN {api_present}
     AND COALESCE({vendor_amount}, 0) > 0
     AND COALESCE({cw_amount}, 0) >= 0
     AND COALESCE({cw_amount}, 0) < COALESCE({vendor_amount}, 0)
        THEN 'API Usage, Insufficient CW Billing'

    -- Without API evidence, an exact zero CW amount is a no-billing case.
    WHEN {no_api}
     AND COALESCE({vendor_amount}, 0) > 0
     AND COALESCE({cw_amount}, 0) = 0
        THEN 'Vendor Billing, No CW Billing'

    -- Without API evidence, every positive monetary shortfall is insufficient.
    WHEN {no_api}
     AND COALESCE({vendor_amount}, 0) > 0
     AND COALESCE({cw_amount}, 0) > 0
     AND COALESCE({cw_amount}, 0) < COALESCE({vendor_amount}, 0)
        THEN 'Vendor Billing, Insufficient CW Billing'

    -- Includes CW credits when no positive vendor charge exists.
    WHEN COALESCE({cw_amount}, 0) <> 0
     AND COALESCE({vendor_amount}, 0) <= 0
        THEN 'CW Billing, No Vendor Billing'

    -- OUTPUT excludes rows with no activity on either side, so this branch is
    -- defensive and keeps the published taxonomy closed.
    ELSE 'Vendor Billing, No CW Billing'
END
""".strip()
