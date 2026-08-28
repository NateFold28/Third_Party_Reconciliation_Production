# Executive Narrative Skeleton

**Project:** CW RMM Product Loss Reporting  
**Audience:** VP of Finance, Product Leadership  
**Format:** 1–2 page memo (quarterly delivery)  
**Status:** SKELETON — to be populated after analysis execution

---

## Page 1: What Is Happening

### Opening Statement (2 sentences max)

> CW RMM lost $[X]M in monthly recurring revenue over the past [N] months
> from existing customers. This analysis decomposes that loss into
> addressable categories and identifies where intervention will have the
> highest return.

### The Loss Decomposition (Single Table)

| Loss Category | MRR Lost (Quarterly) | % of Total | Trend vs Prior Quarter | Confidence |
|--------------|---------------------|-----------|----------------------|------------|
| Price | $___K | __% | ↑ / → / ↓ | High / Medium |
| Billing Confusion | $___K | __% | ↑ / → / ↓ | Medium |
| Product Gaps | $___K | __% | ↑ / → / ↓ | Medium |
| Support Issues | $___K | __% | ↑ / → / ↓ | Low–Medium |
| M&A (non-addressable) | $___K | __% | ↑ / → / ↓ | High |
| Other / Unknown | $___K | __% | ↑ / → / ↓ | — |
| **Total** | **$___K** | **100%** | | |

### Key Behavioral Finding (1 paragraph)

> [Insert: the single most important behavioral insight from EDA.
> Example: "Customers who downsell are X times more likely to fully churn
> within 6 months, and downsell has been rising since Q_. This suggests
> a window for intervention between first downsell and full exit."]

---

## Page 2: Why You Should Trust This + What To Do

### Why This Is Trustworthy

| Trust Factor | Evidence |
|-------------|---------|
| Numbers reconcile to Finance reporting | Total loss MRR within [X]% of Finance-reported churn |
| Findings are stable over time | Category shares consistent across [N] quarters tested |
| Not just reason codes | Behavioral signals (support, usage, billing changes) confirm stated reasons |
| Known limitations disclosed | [X]% of loss events have unclear reason codes; M&A flagged separately |

### What Is Addressable vs What Is Not

| Category | Addressable? | Why / Why Not |
|----------|-------------|---------------|
| Price (targeted cohort) | Yes | Specific cohort identified; retention pricing has measurable effect |
| Billing confusion | Yes | Process improvement; education campaign |
| Product gaps | Partially | Requires roadmap investment; longer payback |
| Support quality | Yes | Operational improvement within current team |
| M&A | No | External event; not within control |
| Unknown | Unclear | Requires better reason code capture |

### Recommended Actions This Quarter (Max 3)

1. **[Action 1]** — Owner: ___, Expected impact: $___K/month addressable, Confidence: High/Medium
2. **[Action 2]** — Owner: ___, Expected impact: $___K/month addressable, Confidence: High/Medium
3. **[Action 3]** — Owner: ___, Expected impact: $___K/month addressable, Confidence: Medium

### Trade-offs and Risk

> [Insert: What could go wrong. What we don't know. What assumptions we're
> making. What would change the recommendation.]
>
> Example: "If price-coded losses are actually driven by product gaps
> (customers citing price because it's easier than explaining feature
> frustration), then pricing interventions will fail. The support ticket
> cross-validation suggests [X]% of price losses have concurrent product
> complaints — this is the primary residual uncertainty."

### How We'll Know If Actions Worked

| Action | Leading Indicator (30 days) | Lagging Indicator (90 days) |
|--------|---------------------------|----------------------------|
| Action 1 | [metric] | [metric] |
| Action 2 | [metric] | [metric] |
| Action 3 | [metric] | [metric] |

---

## Appendix (not in exec memo — available on request)

- Full decomposition data table (24 months)
- EDA notebook with methodology
- Data quality report and known limitations
- Sensitivity analysis results
- Reason code reliability assessment

---

## Narrative Principles (Internal — Do Not Include in Delivered Memo)

1. **No jargon.** If a term requires a footnote, rewrite the sentence.
2. **Numbers first, narrative second.** The table is the star; the text explains it.
3. **Uncertainty is stated, not hidden.** Low-confidence findings are labeled as such.
4. **Actions are specific and owned.** "Improve retention" is not an action. "Launch billing education campaign for IP-model customers by [date], owned by [name]" is.
5. **The memo stands alone.** No verbal walk-through required.
6. **Short.** If it exceeds 2 pages, cut.
