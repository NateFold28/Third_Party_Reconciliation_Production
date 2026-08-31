from pathlib import Path
import sys
from textwrap import dedent
sys.path.insert(0, str(Path.cwd()))
from TEMPLATES.Python.connection import fetch_dataframe
q = dedent("""
WITH skus AS (
  SELECT DISTINCT prod_sku
  FROM analytics.dbo_transformation.seed__product_categorization
  WHERE vendor ILIKE '%proof%' OR sub_category ILIKE '%proof%'
), carr AS (
  SELECT
    DATE_TRUNC('MONTH', c.month_year)::DATE AS billing_month,
    a.cws_account_unique_identifier_c AS sf_id,
    a.name AS account_name,
    c.acc_id,
    c.prod_sku,
    c.ns_usage_qty,
    c.arr_budget_rate,
    c.transaction_source
  FROM analytics.dbo.carr__all_transactions c
  LEFT JOIN analytics.dbo_base_salesforce.base_salesforce__account a
    ON a.id = c.acc_id AND a.is_deleted = FALSE
  WHERE DATE_TRUNC('MONTH', c.month_year)::DATE IN ('2026-05-01','2026-06-01')
    AND c.prod_sku IN (SELECT prod_sku FROM skus)
)
SELECT *
FROM carr
WHERE UPPER(COALESCE(account_name,'')) LIKE '%BLUWAVE%'
   OR UPPER(COALESCE(account_name,'')) LIKE '%JEFF COMPUTERS%'
   OR sf_id IN ('ACT-00119428','ACT-00004890')
ORDER BY billing_month, account_name, prod_sku
""")
print(fetch_dataframe(q).to_string(index=False))
