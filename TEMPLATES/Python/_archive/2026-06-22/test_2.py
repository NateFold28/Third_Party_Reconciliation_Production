import pandas as pd
import matplotlib.pyplot as plt

from connection import get_snowflake_connection, fetch_dataframe

print("✓ connection helpers imported successfully")

conn = get_snowflake_connection()

# Query Snowflake
query = """
-- Monthly Summary: ATR, Actuals, Actual %
SELECT
    DATE_TRUNC('MONTH', MASTER_DATE)::DATE AS RENEWAL_MONTH,
    SUM(ADJ_ATR_C_BUDGET_RATE) AS "ATR",
    SUM(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE) AS "Actuals",
    DIV0(
        SUM(ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE),
        SUM(ADJ_ATR_C_BUDGET_RATE)
    ) * 100 AS "Actual %"
FROM ANALYTICS.DBO.CARR__RENEWALS_PORTFOLIO_LVL
WHERE INCLUDE_FLAG_C = 1
  AND MASTER_DATE >= '2026-01-01'
  AND MASTER_DATE <= '2026-12-31'
GROUP BY 1
ORDER BY 1 ASC
"""

# Fetch results into a DataFrame
df = fetch_dataframe(query, conn=conn)

# Plot
plt.figure(figsize=(12, 6))
plt.plot(df['RENEWAL_MONTH'], df['ATR'], marker='o', label='ATR', linewidth=2)
plt.plot(df['RENEWAL_MONTH'], df['Actuals'], marker='s', label='Actuals', linewidth=2)
plt.xlabel('Renewal Month')
plt.ylabel('Amount')
plt.title('Monthly ATR vs Actuals')
plt.legend()
plt.xticks(rotation=45)
plt.tight_layout()
plt.savefig('monthly_atr_actuals.png')
plt.show()

print("✓ Plot saved as 'monthly_atr_actuals.png'")
conn.close()
