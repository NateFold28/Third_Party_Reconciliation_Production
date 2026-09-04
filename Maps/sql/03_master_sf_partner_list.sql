-- =============================================================================
-- MASTER SALESFORCE PARTNER DIRECTORY
-- =============================================================================
-- Live reference view at one row per CWS account unique identifier (ACT-*).
--
-- CMS_ID is sourced from ACCOUNT_CONTINUUM_ID in the established Zuora source.
-- CWS_CMS_GROUP_ID_C is intentionally not used: it is a Salesforce group field
-- and does not match the numeric CMS partner IDs used by reconciliation.
-- =============================================================================

USE ROLE DEVELOPER;
USE WAREHOUSE REPORTING_WH;
USE DATABASE ANALYTICS_DEV;
USE SCHEMA DBT_NFOLD_TRANSFORMATION;

CREATE OR REPLACE VIEW MASTER_SF_PARTNER_LIST AS
WITH RECURSIVE account_base AS (
    SELECT
        TRIM(CWS_ACCOUNT_UNIQUE_IDENTIFIER_C) AS sf_id,
        ID::VARCHAR AS salesforce_account_id,
        NAME::VARCHAR AS name,
        CWS_ZI_PARENT_COMPANY_NAME_C::VARCHAR AS parent_name,
        CWS_ZI_ULTIMATE_PARENT_COMPANY_NAME_C::VARCHAR AS overall_parent_company,
        CWS_CMS_GROUP_ID_C::VARCHAR AS salesforce_cms_group_id,
        CWS_MANAGE_COMPANY_REC_ID_C::VARCHAR AS manage_company_record_id,
        COALESCE(IS_DELETED, FALSE) AS salesforce_is_deleted,
        LAST_MODIFIED_DATE AS salesforce_last_modified_date
    FROM ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__ACCOUNT
    WHERE NULLIF(TRIM(CWS_ACCOUNT_UNIQUE_IDENTIFIER_C), '') IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY TRIM(CWS_ACCOUNT_UNIQUE_IDENTIFIER_C)
        ORDER BY
            IFF(COALESCE(IS_DELETED, FALSE), 1, 0),
            LAST_MODIFIED_DATE DESC NULLS LAST,
            ID DESC
    ) = 1
),
application_identifier_agg AS (
    SELECT
        CWS_ACCOUNT_C::VARCHAR AS salesforce_account_id,
        LISTAGG(
            DISTINCT IFF(
                CWS_TYPE_C = 'Continuum' AND CWS_APPLICATION_C = 'Salesforce',
                NULLIF(TRIM(CWS_IDENTIFIER_C), ''),
                NULL
            ),
            ' | '
        ) WITHIN GROUP (
            ORDER BY IFF(
                CWS_TYPE_C = 'Continuum' AND CWS_APPLICATION_C = 'Salesforce',
                NULLIF(TRIM(CWS_IDENTIFIER_C), ''),
                NULL
            )
        ) AS continuum_salesforce_account_ids,
        LISTAGG(
            DISTINCT IFF(
                CWS_TYPE_C = 'Continuum' AND CWS_APPLICATION_C = 'Zuora',
                NULLIF(TRIM(CWS_IDENTIFIER_C), ''),
                NULL
            ),
            ' | '
        ) WITHIN GROUP (
            ORDER BY IFF(
                CWS_TYPE_C = 'Continuum' AND CWS_APPLICATION_C = 'Zuora',
                NULLIF(TRIM(CWS_IDENTIFIER_C), ''),
                NULL
            )
        ) AS continuum_zuora_account_ids
    FROM ANALYTICS.DBO_BASE_SALESFORCE.BASE_SALESFORCE__CWS_APPLICATION_IDENTIFIER_C
    WHERE COALESCE(IS_DELETED, FALSE) = FALSE
      AND COALESCE(_FIVETRAN_DELETED, FALSE) = FALSE
      AND CWS_TYPE_C = 'Continuum'
      AND NULLIF(TRIM(CWS_IDENTIFIER_C), '') IS NOT NULL
    GROUP BY 1
),
zuora_cms_history AS (
    SELECT
        CASE
            WHEN TRIM(COALESCE(SUBSCRIPTION_SOLD_TO_SFDC_ID, '')) ILIKE 'ACT-%'
                THEN TRIM(SUBSCRIPTION_SOLD_TO_SFDC_ID)
            WHEN TRIM(COALESCE(SFDC_ACCOUNT_NUMBER, '')) ILIKE 'ACT-%'
                THEN TRIM(SFDC_ACCOUNT_NUMBER)
            ELSE NULL
        END AS sf_id,
        NULLIF(TRIM(ACCOUNT_CONTINUUM_ID::VARCHAR), '') AS cms_id,
        MIN(BILLING_MONTH::DATE) AS first_billing_month,
        MAX(BILLING_MONTH::DATE) AS last_billing_month,
        COUNT(*) AS billing_line_count
    FROM ANALYTICS_DEV.DBT_NFOLD.FINAL_TPR_ENGINEERING_ZUORA_SOURCE_V2
    WHERE NULLIF(TRIM(ACCOUNT_CONTINUUM_ID::VARCHAR), '') IS NOT NULL
    GROUP BY 1, 2
    HAVING sf_id IS NOT NULL
),
zuora_cms_ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY sf_id
            ORDER BY last_billing_month DESC, billing_line_count DESC, cms_id
        ) AS cms_recency_rank
    FROM zuora_cms_history
),
zuora_cms_agg AS (
    SELECT
        sf_id,
        MAX(IFF(cms_recency_rank = 1, cms_id, NULL)) AS cms_id,
        LISTAGG(cms_id, ' | ') WITHIN GROUP (ORDER BY cms_id) AS cms_ids,
        COUNT(*) AS cms_id_count,
        MIN(first_billing_month) AS cms_first_billing_month,
        MAX(last_billing_month) AS cms_last_billing_month
    FROM zuora_cms_ranked
    GROUP BY 1
),
merge_direct AS (
    SELECT
        TRIM(OLD_ACCOUNT) AS old_sf_id,
        TRIM(NEW_ACCOUNT) AS direct_merged_to_sf_id,
        MERGED_BY_DATE AS merged_date,
        MAPPING_TYPE::VARCHAR AS merge_mapping_type
    FROM ANALYTICS.DBO.CW_DW__MERGED_ACCOUNT_MAP
    WHERE NULLIF(TRIM(OLD_ACCOUNT), '') IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY TRIM(OLD_ACCOUNT)
        ORDER BY MERGED_BY_DATE DESC NULLS LAST, TRIM(NEW_ACCOUNT)
    ) = 1
),
merge_paths (old_sf_id, current_sf_id, merge_depth) AS (
    SELECT old_sf_id, direct_merged_to_sf_id, 1
    FROM merge_direct

    UNION ALL

    SELECT p.old_sf_id, m.direct_merged_to_sf_id, p.merge_depth + 1
    FROM merge_paths p
    JOIN merge_direct m
      ON m.old_sf_id = p.current_sf_id
    WHERE p.merge_depth < 25
),
merge_resolved AS (
    SELECT old_sf_id, current_sf_id, merge_depth
    FROM merge_paths
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY old_sf_id
        ORDER BY merge_depth DESC, current_sf_id
    ) = 1
)
SELECT
    a.sf_id,
    c.cms_id,
    c.cms_ids,
    COALESCE(c.cms_id_count, 0) AS cms_id_count,
    a.name,
    a.parent_name,
    a.overall_parent_company,
    IFF(md.old_sf_id IS NOT NULL, 'Y', 'N') AS is_merged,
    md.merged_date,
    md.direct_merged_to_sf_id,
    COALESCE(mr.current_sf_id, a.sf_id) AS current_sf_id,
    current_account.name AS current_name,
    current_cms.cms_id AS current_cms_id,
    mr.merge_depth,
    md.merge_mapping_type,
    a.salesforce_account_id,
    a.salesforce_is_deleted,
    a.salesforce_last_modified_date,
    a.salesforce_cms_group_id,
    a.manage_company_record_id,
    ai.continuum_salesforce_account_ids,
    ai.continuum_zuora_account_ids,
    c.cms_first_billing_month,
    c.cms_last_billing_month,
    'ZUORA.ACCOUNT_CONTINUUM_ID'::VARCHAR AS cms_id_source
FROM account_base a
LEFT JOIN application_identifier_agg ai
  ON ai.salesforce_account_id = a.salesforce_account_id
LEFT JOIN zuora_cms_agg c
  ON c.sf_id = a.sf_id
LEFT JOIN merge_direct md
  ON md.old_sf_id = a.sf_id
LEFT JOIN merge_resolved mr
  ON mr.old_sf_id = a.sf_id
LEFT JOIN account_base current_account
  ON current_account.sf_id = COALESCE(mr.current_sf_id, a.sf_id)
LEFT JOIN zuora_cms_agg current_cms
  ON current_cms.sf_id = COALESCE(mr.current_sf_id, a.sf_id);

COMMENT ON VIEW MASTER_SF_PARTNER_LIST IS
    'Live Salesforce partner directory at one row per CWS account unique identifier, enriched with Zuora CMS IDs, application identifiers, and resolved merged-account lineage.';