# Data Landscape â€” React_App_Demo

**Date:** 2026-06-05

## Primary Source Tables

| Table | Database | Schema | Grain | Freshness |
|-------|----------|--------|-------|-----------|
| CARR__RENEWALS_PORTFOLIO_LVL | ANALYTICS | DBO | One row per renewal opportunity per day (MASTER_DATE) | Unknown — assume daily |

## Filters Required
- `INCLUDE_FLAG_C = 1` — removes excluded/ineligible rows
- Date range on `MASTER_DATE`

## Key Columns
| Column | Meaning |
|--------|---------|
| MASTER_DATE | Renewal date (used for monthly truncation) |
| ADJ_ATR_C_BUDGET_RATE | Adjusted ARR at Risk (budget FX rate) |
| ALLOCATED_CARR_RENEW_GROSS_C_BUDGET_RATE | Gross renewal actuals (budget FX rate) |
| CWS_REGION_C | Segment / region dimension |
| PRODUCT_PORTFOLIO_UFR | Product portfolio dimension |

## Known Semantic Conflicts
- None identified for this PoC scope

## Join Logic
- No joins required — single table

## Open Source Questions
See docs/open_questions.md
