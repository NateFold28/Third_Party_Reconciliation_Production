# 00 — Analysis Contract: CW RMM Product Loss

**Project:** CW RMM Product Loss Reporting  
**Status:** DRAFT — Pending stakeholder sign-off  
**Date:** 2026-05-08  
**Analyst:** Nate Fold  
**Workflow Version:** Analytical Workflow OS v1

---

## 1. Verbatim Ask

> "Develop a reproducible workflow to analyze the factors causing CW RMM
> product loss, clearly explain what is happening, and identify actionable
> steps to reduce losses going forward."
>
> — VP of Finance

---

## 2. The Actual Decision

> "After seeing this analysis, someone will decide whether to
> **invest in specific retention interventions (pricing, billing, product, support)
> to reduce CW RMM product loss, and in what priority order.**"

This is a resource allocation and prioritization decision:
- Which loss drivers are largest and most addressable?
- Where should the business focus retention resources?

---

## 3. Decision Owner

- **Primary:** VP of Finance
- **Consumption format:** Quarterly executive memo + supporting decomposition table
- **Decision cadence:** Quarterly review (rerunnable each quarter)
- **Secondary consumers:** Product leadership, Revenue Operations

---

## 4. Primary KPI

**Net RMM Product MRR Loss**

| Attribute | Definition |
|-----------|-----------|
| Formula | `SUM(MRR at period_start) - SUM(MRR at period_end)` for customers who reduced or cancelled CW RMM products during the period, excluding new business and expansion |
| Grain | Customer-month |
| Directionality | Lower is better (less loss) |
| Business meaning | The total monthly recurring revenue lost from existing CW RMM customers through cancellation or downsell in a given month |
| Exclusions | New logos, pure expansion (no loss component), credits/adjustments that are not true product loss |

**OPEN QUESTION for Finance:** Is this gross loss (before any win-backs or saves) or net loss (after successful save attempts)? This must be locked before Stage 1.

**OPEN QUESTION:** Should "loss" include only full churn, or also downsell (partial reduction)? The reference deck suggests both. Finance must confirm.

---

## 5. Secondary Diagnostics (max 5)

| # | Metric | Business Lever |
|---|--------|---------------|
| 1 | Loss by Reason Category (Price / Product / Billing / Support / M&A / Other) | Identifies which category of intervention has largest addressable pool |
| 2 | Downsell-to-Churn Conversion Rate | Measures whether downsell is a leading indicator of full loss — early intervention signal |
| 3 | Loss Rate by Billing Model (IP vs User vs other) | Tests whether billing confusion is a driver — billing simplification lever |
| 4 | Support Ticket Volume in 90 days pre-loss | Tests whether product/support issues precede loss — product investment lever |
| 5 | Average Tenure at Loss | Identifies lifecycle stage vulnerability — onboarding vs mature customer problem |

---

## 6. Time Horizon

| Dimension | Value |
|-----------|-------|
| Lookback window | 24 months rolling (to capture seasonality and trend) |
| Minimum lookback | 12 months (for initial quarterly run) |
| Prediction window | None — this is a diagnostic decomposition, not a forecast |
| Exclusions | Partial billing cycles at period boundaries; first 90 days post-acquisition for M&A accounts (flag separately) |
| Refresh cadence | Quarterly |

---

## 7. Unit of Analysis

**Customer-Month**

- One row per CW RMM customer per calendar month
- Rationale: Aligns with MRR recognition, enables month-over-month change detection, and matches Finance reporting cadence
- Aggregation risk: Customers with multiple contracts/products must be handled carefully — a customer who downsells one product and expands another in the same month should show both movements, not net them away at the customer level

**OPEN QUESTION:** Should this be **contract-month** instead? If a customer has multiple RMM contracts, do we need contract-level granularity to identify which specific loss reasons apply? Finance must advise.

---

## 8. Hard Constraints

| Constraint | Detail |
|-----------|--------|
| Data sources | Only Finance-approved sources (billing system of record, CRM, support system). No shadow spreadsheets. |
| Interpretability | All outputs must be explainable in plain English to Finance. No black-box models unless a simpler decomposition is proven insufficient. |
| Reproducibility | All logic must be version-controlled (dbt + Git). No manual steps allowed in the quarterly refresh. |
| Reason codes | Use existing lost-product reason codes as a starting taxonomy. Do NOT invent new categories without Finance approval. |
| Privacy | No individual user-level PII in outputs. Customer-level aggregation is acceptable. |
| Tooling | Snowflake, dbt, Python (Snowpark). Delivery via Power BI or Streamlit. |

---

## 9. Explicitly Out of Scope

- **Forecasting/predicting** which customers will churn (this is decomposition, not prediction)
- **New business acquisition** analysis
- **Non-RMM products** — scoped to CW RMM only
- **Competitive win/loss** analysis (external market factors)
- **Individual deal-level negotiation** support
- **Customer satisfaction / NPS** as a primary driver (may appear as diagnostic only)
- **Pricing optimization** recommendations (downstream of this analysis, not part of it)

---

## 10. Risks and Misuse

| Risk | Mitigation |
|------|-----------|
| Reason codes are inconsistently applied by reps | Treat reason codes as directional, not authoritative. Cross-validate with behavioral signals (usage drop, billing change, support volume). |
| Billing model confusion conflated with price sensitivity | Decompose billing-related loss separately from true price elasticity loss. |
| Downsell counted as "loss" when it may be right-sizing | Define thresholds: downsell >X% of MRR in a single month = loss signal. Small adjustments excluded. |
| Analysis used to blame specific teams | Frame findings as system-level drivers, not team accountability. Emphasize actionability. |
| Numbers treated as exact when they contain estimation | All outputs include confidence/data-quality annotations. Executive narrative includes limitations section. |
| Survivorship bias — only analyzing lost customers | Include control comparisons against retained customers where relevant in EDA. |

---

## 11. Agreement and Lock-In

| Role | Name | Status |
|------|------|--------|
| Decision Owner (VP Finance) | ___________________ | [ ] Agreed |
| Analyst / DS Owner | Nate Fold | [ ] Agreed |
| Data Engineering (dbt owner) | ___________________ | [ ] Agreed |

**Before proceeding to Stage 1, the following must be resolved:**

1. Gross loss vs net loss definition
2. Churn-only vs churn + downsell scope
3. Customer-month vs contract-month grain
4. Confirmation of authoritative billing data source

---

## Exit Criteria Checklist

- [x] Decision is singular and explicit (prioritize retention interventions)
- [x] Primary KPI is defined with formula and grain
- [ ] Open questions resolved (3 items above)
- [x] Time horizon locked (24-month rolling, quarterly refresh)
- [x] Unit of analysis stated (customer-month, pending confirmation)
- [x] Out-of-scope items agreed
- [x] Risks acknowledged

**Stage 0 status: DRAFT — pending open question resolution and sign-off.**
