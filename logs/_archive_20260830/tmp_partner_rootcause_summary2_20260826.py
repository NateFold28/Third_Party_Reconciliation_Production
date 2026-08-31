from TEMPLATES.Python.connection import fetch_dataframe

q = '''
WITH partners AS (
    SELECT * FROM VALUES
      ('Pure Technology /1', 'ACT-00151751'),
      ('Keystone Information Technology', 'ACT-00103890'),
      ('Keystone', 'ACT-00048610'),
      ('Virtual Communication Specialists 1', 'ACT-00246010'),
      ('In-Telecom', 'ACT-00246692'),
      ('The Learning Exchange', 'ACT-00239688'),
      ('GSG Computers, Inc.', 'ACT-00240157'),
      ('SHARKTOOTH NETWORKS INC', 'ACT-00153605'),
      ('Total Group International Ltd.', 'ACT-00149039'),
      ('DigitalBrainz', 'ACT-00217986'),
      ('Strong Connexions, Inc.', 'ACT-00245565')
    AS t(partner_name, sf_id)
),
usage_stats AS (
    SELECT p.partner_name, p.sf_id,
           COUNT(u.vendor_product_sku) AS usage_rows,
           SUM(IFF(UPPER(COALESCE(u.modifier,''))='DISABLED',1,0)) AS disabled_usage_rows,
           SUM(COALESCE(u.quantity,0)) AS usage_qty
    FROM partners p
    LEFT JOIN ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.THIRD_PARTY_RECON_VENDOR_USAGE_PROD u
      ON u.vendor='Acronis' AND u.billing_month='2026-06-01'::DATE
     AND u.vendor_partner_name = p.partner_name
    GROUP BY 1,2
),
matched_stats AS (
    SELECT p.partner_name, p.sf_id,
           COUNT(m.product_sku) AS matched_rows,
           SUM(COALESCE(m.zuora_quantity,0)) AS matched_qty
    FROM partners p
    LEFT JOIN ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_BILLING_MATCHED m
      ON m.billing_month='2026-06-01'::DATE
     AND m.sf_id = p.sf_id
    GROUP BY 1,2
),
resolved_stats AS (
    SELECT p.partner_name, p.sf_id,
           COUNT(r.sku_match_group) AS resolved_rows,
           SUM(COALESCE(r.zuora_quantity,0)) AS resolved_qty
    FROM partners p
    LEFT JOIN ANALYTICS_DEV.DBT_NFOLD_TRANSFORMATION.ACRONIS_ZUORA_RESOLVED r
      ON r.billing_month='2026-06-01'::DATE
     AND r.sf_id = p.sf_id
    GROUP BY 1,2
)
SELECT
  u.partner_name, u.sf_id,
  u.usage_rows, u.disabled_usage_rows, u.usage_qty,
  m.matched_rows, m.matched_qty,
  r.resolved_rows, r.resolved_qty
FROM usage_stats u
LEFT JOIN matched_stats m ON m.partner_name=u.partner_name AND m.sf_id=u.sf_id
LEFT JOIN resolved_stats r ON r.partner_name=u.partner_name AND r.sf_id=u.sf_id
ORDER BY u.partner_name
'''

print(fetch_dataframe(q).to_string(index=False, max_colwidth=140))
