# Stages 5–7 — Analytical Approach

**Project:** CW RMM Product Loss Reporting  
**Purpose:** Define the EDA questions, testing methods, and validation approach  
**Prerequisite:** Stages 1–3 complete (data landscape mapped, quality confirmed, semantics locked)

---

## Stage 5 — Exploratory Data Analysis Plan

### 5.1 Core EDA Questions (Must Answer Before Any Conclusions)

Each question below maps to a loss driver hypothesis from the reference deck. EDA must answer these with data, not assumption.

#### Q1: What is the loss decomposition by reason category?

- Compute: Monthly loss MRR by reason code (Price / Product / Billing / Support / M&A / Other)
- Visualize: Stacked area chart over 24 months — are categories stable or shifting?
- Check: What % is "Other" or "Unknown"? If >30%, the decomposition is unreliable.
- Segment: Does the mix differ by customer size, tenure, or billing model?

#### Q2: Is downsell a leading indicator of full churn?

- Compute: For customers who fully churned, what % had a downsell event in the prior 6 months?
- Control: What % of non-churned customers also downsell (base rate)?
- Test: Is the downsell → churn rate significantly higher than baseline?
- Time pattern: What is the median time between first downsell and full churn?

#### Q3: Does billing model correlate with loss?

- Compute: Loss rate (% of base MRR lost) by billing model (IP vs User vs other)
- Normalize: Control for customer size and tenure — is billing model an independent factor?
- Pattern: Are customers who switched billing models more likely to churn than those who never switched?
- Flag: Is "billing confusion" detectable in reason codes or support tickets pre-loss?

#### Q4: Do support/product issues cluster before loss events?

- Compute: Average support ticket volume in 30/60/90 days pre-loss vs matched retained customers
- Category: Which ticket categories (product bugs, billing questions, feature requests) spike pre-loss?
- Threshold: Is there a ticket count or category mix that is a reliable pre-loss signal?
- Timing: How far in advance does the support signal appear?

#### Q5: Is price truly causal, or correlated with other factors?

- Compute: For "Price" reason code losses — what was their actual price relative to cohort?
- Check: Were these customers on older (higher) pricing, or did they experience a price increase?
- Confound: Do "price" losses also show product/support issues (suggesting price is the stated, not real, reason)?
- Segment: Does price sensitivity differ by customer size or tenure?

#### Q6: What is the role of tenure and lifecycle stage?

- Compute: Loss rate by tenure band (0-6mo, 6-12mo, 12-24mo, 24mo+)
- Pattern: Is there an "onboarding cliff" or "renewal cliff"?
- Interaction: Does tenure interact with other drivers (e.g., newer customers churn for product reasons, mature customers for price)?

### 5.2 EDA Guardrails

| Rule | Rationale |
|------|-----------|
| All time-series analysis respects calendar month boundaries | Prevents partial-period distortion |
| No "peeking" at future data relative to the event | E.g., when analyzing pre-loss support tickets, only use tickets BEFORE the loss event |
| Always include a retained-customer control group | Prevents confirming any pattern that also exists in the base |
| Report absolute numbers alongside rates | A high rate on a tiny segment doesn't drive business impact |
| Flag any finding where the reason code is the only evidence | Behavioral confirmation required for actionability |

### 5.3 EDA Output Requirements

At the end of EDA, produce:

1. **Loss decomposition table** — MRR lost by reason category by quarter
2. **Behavioral signal summary** — which pre-loss behaviors are elevated vs control
3. **Hypothesis validation matrix** — for each initial hypothesis, state: confirmed / partially confirmed / not supported / insufficient data
4. **Explicit testable hypotheses** — carried forward to model design

---

## Stage 6 — Model Design (Baseline-First)

### 6.1 The Central Question

> Do we need a model at all, or is a structured decomposition sufficient?

For this analysis, the answer is likely: **a structured decomposition with statistical testing is sufficient.** ML is unlikely to be justified because:

- The decision is "where to invest" not "who will churn"
- Explainability to Finance is a hard constraint
- The output is a quarterly report, not a real-time score
- The reference deck already shows a decomposition framework

**However**, if EDA reveals that:
- Reason codes are unreliable (>40% unknown)
- Behavioral signals are strongly predictive
- Finance wants a customer-level risk score

...then a simple classification model (logistic regression) may earn its way in.

### 6.2 Baseline Approaches (Mandatory)

| Baseline | Method | When It's Sufficient |
|----------|--------|---------------------|
| **Decomposition** | Group MRR loss by reason code; compute shares | Sufficient if reason codes are >70% populated and directionally reliable |
| **Behavioral enrichment** | Decomposition + cross-validation against support/usage signals | Sufficient if you need more confidence than reason codes alone |
| **Statistical testing** | Chi-square / proportion tests on loss rates across cohorts | Sufficient if the question is "does billing model matter?" not "predict who churns" |

### 6.3 When to Escalate to ML

| Trigger | Model Type | Justification Required |
|---------|-----------|----------------------|
| Reason codes unreliable AND behavioral signals available | Logistic regression or decision tree | Must demonstrate it identifies loss drivers not visible in decomposition |
| Finance wants customer-level scores for proactive retention | Logistic regression with SHAP explanations | Must prove lift over "just target customers with support tickets + downsell" |
| Multiple interacting factors (tenure × billing × price) | Regularized regression or gradient boosted tree | Must prove interactions materially change the story vs simple segment cuts |

### 6.4 Decision Interface

If a model is used, its output must be:

- A **driver importance ranking** (which factors explain the most loss)
- NOT a customer-level churn probability (that's out of scope per Stage 0)
- Expressed in business terms (e.g., "billing model confusion accounts for ~$X/month of addressable loss")

### 6.5 Evaluation Metrics (Business-Relevant)

| Metric | Purpose |
|--------|---------|
| % of total loss explained by identified drivers | Measures completeness of the decomposition |
| Stability of driver rankings across quarters | Measures whether findings are structural vs noise |
| Agreement with reason code data where populated | Measures convergence of behavioral and stated evidence |
| Finance acceptance (qualitative) | Does the decomposition match their understanding? |

---

## Stage 7 — Validation Plan

### 7.1 Temporal Validation (Mandatory)

| Check | Method |
|-------|--------|
| Train/test split | Use months 1–18 as "training" period; validate decomposition holds for months 19–24 |
| Rolling stability | Compute loss decomposition for each rolling 6-month window; check category shares are stable |
| Event alignment | Verify that known business events (price changes, product launches) show up in expected categories at expected times |

### 7.2 Causal vs Correlation Testing

For each proposed driver, explicitly test:

| Driver | Causal Test | Correlation Trap |
|--------|-------------|-----------------|
| Price | Did price increases PRECEDE loss? Or is "price" the stated reason for loss driven by other factors? | Customers who churn always feel price was too high — doesn't mean reducing price would have retained them |
| Billing confusion | Did billing model switches precede loss? Do support tickets about billing show up pre-loss? | Billing confusion may be a symptom of poor onboarding, not a root cause |
| Support issues | Did ticket volume spike BEFORE loss event, or AFTER the customer decided to leave? | Customers leaving may file complaints as exit behavior, not as a true driver |
| Product gaps | Did usage decline BEFORE the loss event? | Customers who decided to leave stop using the product — usage decline may be effect, not cause |

### 7.3 Finance Trust Checks

| Validation | Purpose |
|-----------|---------|
| Total loss MRR matches Finance-reported churn | Ensures no double-counting or missing data |
| Category shares are "plausible" to Finance stakeholders | If decomposition shows 60% price but Finance believes it's 20%, investigate the gap |
| Known large losses appear correctly categorized | Spot-check the top 10 individual losses each quarter |
| Trend direction matches qualitative business knowledge | If Finance knows "support got worse in Q3", the data should show it |

### 7.4 Robustness Checks

| Test | Method | Failure Signal |
|------|--------|---------------|
| Sensitivity to reason code assignment | Re-run decomposition treating all "Other" as each category in turn | If conclusions flip, reason codes are too sparse to trust |
| Sensitivity to MRR calculation | Vary the loss threshold (e.g., >5% vs >10% MRR drop = downsell) | If ranking changes dramatically, the threshold is arbitrary |
| Segment stability | Check if decomposition holds across small/medium/large customers | If it reverses (Simpson's paradox), segment-level reporting is required |
| Time stability | Check if driver rankings are consistent across quarters | If unstable, findings are period-specific not structural |

### 7.5 Go / No-Go Criteria

| Outcome | Criteria | Action |
|---------|----------|--------|
| **Go** | Decomposition is stable, Finance-aligned, and explains >70% of loss | Publish quarterly; begin action framework |
| **Go with constraints** | Decomposition works for some segments/periods but not all | Publish with explicit scope limitations |
| **No-go** | Data quality issues invalidate the KPI, or decomposition is unstable | Return to Stage 2; escalate data issues |

---

## Exit Criteria for Stages 5–7

- [ ] All 6 core EDA questions answered with data
- [ ] Hypothesis validation matrix complete
- [ ] Baseline approach selected and justified (decomposition vs model)
- [ ] Temporal validation demonstrates stability
- [ ] Causal vs correlation explicitly addressed for each driver
- [ ] Finance trust checks passed
- [ ] Go / no-go decision recorded
