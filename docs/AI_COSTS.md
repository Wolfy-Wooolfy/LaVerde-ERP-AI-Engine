# AI Costs — Budget Management

## Pricing Model

Using GPT-4o-mini (cheapest capable model):

| Model | Input ($/M tokens) | Output ($/M tokens) |
|-------|--------------------|---------------------|
| gpt-4o-mini | $0.15 | $0.60 |
| gpt-4o | $2.50 | $10.00 |
| gpt-4-turbo | $10.00 | $30.00 |

## Cost Per Lead Prioritization

Typical token counts per lead:
- System prompt: ~220 tokens
- User prompt per lead: ~120 tokens  
- Response (JSON): ~40 tokens

**Cost per lead = (220+120) × $0.15/M + 40 × $0.60/M ≈ $0.000075**

## Monthly Budget Analysis

With $10/month budget and 6-hour cache TTL:

| Leads scored/day | Daily cost | Monthly cost | Budget headroom |
|------------------|-----------|--------------|-----------------|
| 10 fresh | $0.00075 | $0.023 | 99.8% remaining |
| 50 fresh | $0.00375 | $0.113 | 98.9% remaining |
| 200 fresh | $0.015 | $0.45 | 95.5% remaining |

With steady-state 70% cache hit rate, effective cost is ~30% of fresh:
- 50 leads/day → ~$0.034/month at 70% hit rate

**The $10 budget comfortably supports the dashboard even at high usage.**

## Budget Enforcement

Two-tier protection:

1. **Warning at 80%**: Logged to `logs/ai.log`. Budget pill turns amber.
2. **Hard stop at 100%**: `BudgetExceededError` raised. All AI endpoints return HTTP 402. Dashboard degrades gracefully.

Budget resets on the 1st of each calendar month (UTC).

## Persistence

Budget totals are persisted to `logs/ai_budget.json` as `{"YYYY-MM": $X.XX}`. Server restart does not reset the counter mid-month.

## Scaling Estimates

| Users | Leads in system | Est. monthly cost |
|-------|-----------------|-------------------|
| 5 sales agents | 500 leads | ~$0.50/month |
| 20 sales agents | 2,000 leads | ~$2.00/month |
| 100 sales agents | 10,000 leads | ~$8.00/month |

At 10,000 leads with aggressive refreshing, approach the $10 limit. Recommended: increase `AI_MONTHLY_BUDGET_USD` or reduce `limit` in prioritize_overdue.
