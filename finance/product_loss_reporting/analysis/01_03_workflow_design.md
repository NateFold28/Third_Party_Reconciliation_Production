# Stages 1–3 — Reproducible Workflow Design

**Project:** CW RMM Product Loss Reporting  
**Purpose:** Define the repeatable data playbook for quarterly loss analysis  
**Prerequisite:** Stage 0 open questions must be resolved before execution

---

## Stage 1 — Data Landscape

### 1.1 Required Data Domains

| Domain | Role in Analysis | Priority |
|--------|-----------------|----------|
| **Billing / Invoicing** | Source of truth for MRR, product quantities, billing model (IP/User) | Critical — KPI source |
| **Contracts / Subscriptions** | Contract start/end dates, renewal dates, terms, products attached | Critical — defines loss events |
| **CRM / Accounts** | Customer attributes, tenure, segment, partner status, account ownership | Critical — segmentation and cohort |
| **Lost Product Reason Codes** | Stated reasons for cancellation or downsell | Critical — primary decomposition axis |
| **Support / Ticketing** | Ticket volume, categories, resolution times pre-loss | Important — behavioral signal |
| **Product Usage / Telemetry** | Endpoint counts, feature adoption, usage trends pre-loss | Important — engagement signal |
| **Pricing / Rate Cards** | Historical price changes, discount structures, billing model transitions | Important — price vs billing confusion |
| **M&A / Corporate Events** | Acquisition dates, migration timelines | Contextual — exclusion/flagging |

### 1.2 Data Source Identification (Step-by-Step Playbook)

**For each domain, the analyst must:**

1. Identify the system of record (e.g., ConnectWise billing system, Salesforce CRM, Zendesk/CW support)
2. Locate the Snowflake raw/staging tables that ingest from that system
3. Identify any existing dbt models that transform this data
4. Confirm with Finance which source they consider "authoritative" for revenue figures
5. Document freshness/SLA for each source

**Key questions to resolve with data owners:**

- Where does MRR live as Finance sees it? (billing system extract? revenue recognition system?)
- Are lost-product reason codes captured at cancellation time or backfilled?
- Is there a single customer master, or are there competing customer hierarchies?
- Does product usage data exist at sufficient granularity and history?

### 1.3 Loss Event Definition

The workflow must precisely define what constitutes a "loss event":

| Event Type | Definition | Data Signal |
|-----------|-----------|-------------|
| Full Churn | Customer cancels all CW RMM products | MRR goes to $0 for RMM; contract end date reached or early termination |
| Downsell | Customer reduces RMM quantity or downgrades plan | MRR decreases month-over-month for RMM products specifically |
| Billing Model Switch (loss component) | Customer switches billing model resulting in lower MRR | Requires isolating the "before vs after" MRR under each model |

**FLAG:** Billing model switches (IP → User or vice versa) may appear as loss when they are actually restructuring. The workflow must separate true economic loss from billing reclassification.

### 1.4 Reason Code Treatment

Lost product reason codes from the reference deck:

- Price
- Product (feature gaps, quality)
- Billing (confusion, methodology)
- Support (service quality, responsiveness)
- M&A (acquired/merged)
- Other / Unknown

**Workflow rules for reason codes:**

1. Treat as a **directional indicator**, not ground truth
2. Cross-validate against behavioral data (usage, support, billing changes)
3. Track the % of losses with "Other" or blank reason codes — if >30%, the taxonomy needs attention
4. Never report reason code splits as exact — always include "confidence: reason codes are rep-assigned and may be inconsistent"

### 1.5 Grain Alignment Check

| Source | Native Grain | Alignment to Customer-Month |
|--------|-------------|---------------------------|
| Billing | Invoice line item (product-month) | Aggregate to customer-month by summing RMM line items |
| Contracts | Contract (possibly multi-product) | Filter to RMM products; join to customer |
| CRM | Account | Direct join; watch for parent/child hierarchy issues |
| Support | Ticket (event-level) | Aggregate to customer-month (count, category mix) |
| Usage | Endpoint-day or similar | Aggregate to customer-month (avg endpoints, trend) |

---

## Stage 2 — Data Quality Playbook

### 2.1 Mandatory Quality Checks (Run Each Quarter)

Each check below must be implemented as a **parameterized, rerunnable SQL query** stored in the project repo.

#### Check 1: MRR Continuity
```
-- Verify no unexplained gaps in monthly MRR reporting
-- Expected: every customer with active RMM has an MRR value every month
-- Alert if: >5% of expected customer-months are missing
```
- Compare customer-month population against expected active base
- Flag months with sudden drops (>10% month-over-month base shrinkage)

#### Check 2: Reason Code Completeness
```
-- For all loss events, what % have a populated reason code?
-- Track over time — degradation signals process breakdown
```
- Threshold: <70% populated = data quality risk to be flagged in exec narrative

#### Check 3: Grain Uniqueness
```
-- At the customer-month grain, verify no duplicates
-- Duplicates indicate join fan-out or ingestion issues
```

#### Check 4: Cross-System MRR Reconciliation
```
-- Compare total RMM MRR from billing source vs Finance reporting
-- Quantify difference; identify known sources (timing, adjustments)
```
- Acceptable variance: <2% at aggregate level
- Document known reconciliation items

#### Check 5: Temporal Coverage
```
-- Verify data exists for full 24-month lookback
-- Flag any gaps, schema changes, or source migrations
```

#### Check 6: Loss Event vs Contract Status Alignment
```
-- Verify that loss events (MRR decrease) align with contract status changes
-- Misalignment suggests data integration issues
```

### 2.2 Quality Classification Framework

For each issue found:

| Classification | Criteria | Action |
|---------------|----------|--------|
| **Blocking** | Invalidates the primary KPI (e.g., MRR source unreliable) | Stop. Escalate to data engineering. |
| **Mitigatable** | Can be corrected with documented assumptions (e.g., fill missing reason codes as "Unknown") | Proceed with mitigation documented |
| **Acceptable** | Known limitation with bounded impact (e.g., 3% of records have null tenure) | Proceed; note in exec narrative |

### 2.3 Data Latency Assessment

| Question | Required Answer |
|----------|----------------|
| How fresh is billing data when quarterly analysis runs? | Must be within 5 business days of period close |
| Are there known backfill patterns? | Document; exclude in-flight periods from final numbers |
| Does Finance close the books before or after this analysis runs? | Must align — use Finance-closed numbers where possible |

---

## Stage 3 — Semantic Risk Assessment

### 3.1 Critical Semantic Questions

These must be answered and documented before any EDA:

| # | Question | Risk if Unanswered |
|---|----------|-------------------|
| 1 | How is "RMM product" defined in the billing system? (SKU list? product family? manual tag?) | Could include/exclude wrong products |
| 2 | How are billing model switches recorded? (new line item? amendment? cancellation + new contract?) | Could double-count loss or miss it entirely |
| 3 | How are mid-month changes handled? (prorated? full month? effective next month?) | Could distort monthly MRR change calculations |
| 4 | Are credits and adjustments distinguished from true loss? | Could inflate loss numbers |
| 5 | How are multi-product customers handled when only RMM is lost? | Must isolate RMM component without losing customer context |
| 6 | What is the relationship between "lost product reason code" and "churn reason code"? | Could be different systems, different taxonomies |

### 3.2 Known Semantic Risks (from Reference Deck)

| Risk | Source | Proposed Treatment |
|------|--------|-------------------|
| IP vs User billing confusion | Reference deck mentions this as a loss driver | Must be able to identify customers who switched billing models and separate "model confusion" loss from "price" loss |
| Downsell as precursor to churn | Reference deck shows this pattern | Build a trailing indicator: did downsell in month N predict full churn in months N+1 to N+6? |
| M&A-driven loss conflation | Acquisitions may trigger cancellations that aren't organic loss | Flag M&A accounts; report separately; do not include in "addressable loss" |
| Reason code = rep judgment | Inconsistent application across reps/regions | Cross-validate with quantitative signals; never report as sole source of truth |

### 3.3 Finance Must Explicitly Agree On

Before proceeding to EDA, Finance must confirm:

1. **The authoritative MRR source** — which system/table is "the number"?
2. **Loss event definition** — what exactly triggers a "loss" in their mental model?
3. **Treatment of billing restructuring** — is a model switch that results in lower MRR a "loss" or a "reclassification"?
4. **Addressable vs non-addressable loss** — should M&A and "other" be included in the total loss KPI or reported as a separate memo item?
5. **Reason code trust level** — does Finance treat reason codes as reliable or directional?

### 3.4 dbt Model Audit Scope

When executing this stage, the analyst should:

1. Pull the lineage graph for any existing dbt models that compute:
   - MRR / ARR
   - Churn / retention
   - Product-level revenue
   - Customer lifecycle events

2. For each model, document:
   - What one row represents
   - What joins are applied (and their cardinality)
   - What filters are hard-coded
   - Whether the grain matches customer-month

3. Flag models where:
   - Business meaning is ambiguous
   - Grain changes silently between upstream and downstream
   - Filters embed unstated business policy

---

## Workflow Execution Sequence (Quarterly Playbook)

```
┌─────────────────────────────────────────────────────┐
│  1. Pull latest data (automated dbt run)            │
│  2. Run quality checks (parameterized SQL suite)    │
│  3. Review quality results; classify issues         │
│  4. Run loss decomposition queries                  │
│  5. Cross-validate reason codes vs behavior         │
│  6. Produce decomposition table                     │
│  7. Generate executive narrative                    │
│  8. Present to VP Finance                           │
│  9. Log decisions and actions taken                 │
│  10. Archive results for trend comparison           │
└─────────────────────────────────────────────────────┘
```

Each step must be executable without manual intervention beyond step 3 (human judgment on quality issues) and step 8 (presentation).

---

## Exit Criteria for Stages 1–3

- [ ] All data domains identified with authoritative sources named
- [ ] Quality check suite written and stored in repo
- [ ] Semantic risks documented and Finance-acknowledged
- [ ] No blocking data quality issues outstanding
- [ ] Finance has agreed on loss definition, MRR source, and reason code treatment
