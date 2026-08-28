# Stage 10 Preview — Action Framework

**Project:** CW RMM Product Loss Reporting  
**Purpose:** Define the decision framework that maps analytical findings to business actions  
**Note:** This is the FRAMEWORK, not final recommendations. Actual recommendations require completed analysis.

---

## Framework Design Principle

This action framework is designed so that:
- Each finding category triggers a specific type of action
- Impact is measurable after action is taken
- The framework is reusable quarterly — as data changes, recommended actions update automatically
- Actions are owned by specific roles, not vaguely "the business"

---

## 1. Action Categories

### Category A: Pricing Actions

| Finding Pattern | Triggered Action | Owner | Impact Metric |
|----------------|-----------------|-------|---------------|
| Price is top loss driver AND loss is concentrated in specific cohorts (e.g., legacy pricing) | Targeted retention pricing / loyalty discount for high-risk cohort | Revenue Ops | Reduction in price-coded loss for targeted cohort (measure at 90-day post-action) |
| Price loss is diffuse across all segments | Systemic pricing review (not a retention action — a product/strategy decision) | Product + Finance | Long-term loss rate trend (6-month lag) |
| Price appears causal (preceded by price increase) | Evaluate price increase rollback or grandfathering for at-risk segment | Finance | Save rate for customers offered revised pricing |
| Price appears correlated but not causal (other issues present) | Do NOT lead with price reductions; address root cause first | — | — |

### Category B: Billing Actions

| Finding Pattern | Triggered Action | Owner | Impact Metric |
|----------------|-----------------|-------|---------------|
| Billing model confusion (IP vs User) precedes loss | Proactive billing education campaign for customers on mixed models | Customer Success | Reduction in billing-related support tickets + billing-coded loss |
| Billing model switch → immediate downsell/churn pattern | Introduce billing migration support program (guided transition) | Billing Ops | Retention rate for customers offered migration support vs control |
| Billing reason codes concentrated in specific segments | Targeted outreach to those segments before next billing cycle | Customer Success | Pre/post outreach loss comparison |

### Category C: Product Actions

| Finding Pattern | Triggered Action | Owner | Impact Metric |
|----------------|-----------------|-------|---------------|
| Product-coded loss is rising AND usage decline precedes it | Prioritize product roadmap items that address top product-loss reasons | Product Management | Quarterly product-loss rate trend |
| Product loss concentrated in specific feature areas | Targeted feature improvement or gap closure | Product Engineering | Loss rate for customers citing specific feature gap |
| Product loss is stable and small | No immediate action; monitor | — | — |
| Usage decline is a strong pre-loss signal | Build early-warning system; trigger CSM outreach at usage decline threshold | Customer Success + Product | Intervention success rate for triggered accounts |

### Category D: Support Actions

| Finding Pattern | Triggered Action | Owner | Impact Metric |
|----------------|-----------------|-------|---------------|
| High ticket volume in 90 days pre-loss AND tickets poorly resolved | Improve resolution quality for at-risk ticket categories | Support Leadership | Resolution satisfaction for target categories + downstream loss rate |
| Support-coded loss is rising | Root-cause specific support failure modes | Support Leadership | Trend reversal in support-coded loss |
| Support issues cluster with product issues | Reclassify as product gap (not support failure) | Product + Support | Accurate attribution informs correct investment |

---

## 2. Prioritization Logic

When multiple action categories are triggered, prioritize by:

```
Priority Score = (Addressable MRR at Risk) × (Confidence in Causal Link) × (Feasibility of Action)
```

| Factor | Scale | How Measured |
|--------|-------|-------------|
| Addressable MRR at Risk | $ amount | Direct from decomposition |
| Confidence in Causal Link | High / Medium / Low | From Stage 7 causal testing |
| Feasibility of Action | High / Medium / Low | Assessed with action owner |

**Rules:**
- Never recommend more than 3 actions per quarter (focus over breadth)
- Low-confidence findings → "investigate further" not "act now"
- High-MRR, high-confidence, high-feasibility = immediate action
- High-MRR, low-confidence = priority for next quarter's deeper analysis

---

## 3. Impact Measurement Protocol

For every action taken:

| Measurement | Timeline | Method |
|-------------|----------|--------|
| Leading indicator | 30 days | Behavioral change (ticket volume, usage, billing inquiries) |
| Lagging indicator | 90 days | Loss rate change for targeted cohort vs control |
| Trend confirmation | 2 quarters | Decomposition shows category shrinkage |

**Control methodology:**
- Where possible, use matched-cohort comparison (acted-upon vs similar-not-acted-upon)
- Where randomization isn't possible, use pre/post with trend adjustment
- Always report confidence interval, not point estimate

---

## 4. Quarterly Review Cadence

Each quarter, this framework produces:

1. **Updated decomposition** — has the loss mix changed?
2. **Action effectiveness report** — did prior quarter's actions work?
3. **Revised priority list** — what should we do THIS quarter?
4. **Emerging signals** — anything new appearing that wasn't in prior quarters?

This creates a closed feedback loop:

```
Analyze → Prioritize → Act → Measure → Re-Analyze
```

---

## 5. What This Framework Does NOT Do

- Does NOT prescribe specific dollar amounts for pricing changes
- Does NOT make individual customer-level retention decisions
- Does NOT replace product roadmap prioritization (it informs it)
- Does NOT guarantee loss reduction (some loss is structural/unaddressable)
- Does NOT cover competitive dynamics or market shifts

---

## 6. Framework Success Criterion

The framework is working if:

> Each quarter, the VP of Finance can look at the decomposition, see which
> actions were taken, evaluate their measured impact, and decide what to do
> next — all within a single 30-minute review session.

If the framework requires more than 30 minutes to understand and act on, it is too complex.
