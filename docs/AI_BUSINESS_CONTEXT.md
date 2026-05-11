# AI Business Context — Egyptian Real Estate

This document describes the domain-specific rules baked into the AI lead
prioritization system. Customize this section when deploying to a different
market.

## Target Market

Egyptian real estate company. Sales cycle involves brokers, site visits
(معاينة), and heavy WhatsApp usage for follow-up. Email is not an effective
channel for this audience.

## Communication Hierarchy

| Priority | Channel | When to use |
|----------|---------|-------------|
| 1 | WhatsApp / Phone call | Default first action |
| 2 | Schedule site visit (معاينة) | Customer is warm, expressed interest |
| 3 | Email | Last resort — only after multiple failed WhatsApp/call attempts |

Sales reps should **never** see "Send email" as a primary recommended action.
The prompt actively discourages this pattern.

## Scoring Tiers

| Score | Tier | Meaning |
|-------|------|---------|
| 90–100 | critical | Hot — near closing, recent site visit or strong interest signal |
| 70–89 | high | Warm — engaged, recent chatter, needs follow-up |
| 50–69 | medium | Mid-funnel — interested but communication gaps |
| 30–49 | low | Cold — stale, multiple failed contact attempts |
| 0–29 | dead | No engagement, very long silence, no response history |

## Chatter Signal Detection

The system reads the last 3 `mail.message` records per lead (via `search_read`
on `mail.message`, read-only) and applies keyword-based detection before
sending to the AI.

### Site Visit Keywords (`has_site_visit`)

Arabic: `معاينة`, `زيارة`, `دخل`, `اتفرج`, `شاف الموقع`
English: `site visit`, `visited`, `viewing`, `tour`

**Effect on scoring:** Leads with a recent site visit get higher scores. The
AI is instructed to treat a site visit as a strong buy-intent signal.

### Phone Attempt Keywords (`has_phone_attempt`)

Arabic: `مردش`, `مرد`, `مغلق`, `اتصلت`, `كلمته`
English: `didn't answer`, `no response`, `called`, `no answer`

**Effect on scoring:** When phone attempts are detected but unsuccessful, the
AI recommends switching to WhatsApp rather than calling again.

## AI Response Schema

```json
{
  "score": 75,
  "tier": "high",
  "reasoning": "Recent site visit with strong interest, 8 days overdue.",
  "recommended_action": "Schedule follow-up call via WhatsApp",
  "key_signal": "معاينة mentioned 5 days ago"
}
```

The `key_signal` field is shown in the dashboard card as the single most
important data point that drove the score. It helps sales reps quickly
understand WHY a lead ranked where it did without reading the full reasoning.

## Prompt Location

`backend/modules/ai/prompts.py` — `LEAD_PRIORITIZATION_SYSTEM_PROMPT`

The prompt is version-controlled, testable, and documented in
`tests/unit/modules/ai/test_prompt_with_chatter.py`.

## Adapting for Other Markets

To deploy this for a non-Egyptian real estate market:

1. Edit `LEAD_PRIORITIZATION_SYSTEM_PROMPT` in `prompts.py`:
   - Change the communication channel hierarchy
   - Update the scoring guidelines for your deal cycle
   - Adjust the `key_signal` examples

2. Update keyword lists in `backend/modules/ai/chatter.py`:
   - `SITE_VISIT_KEYWORDS` — terms your sales team uses for property viewings
   - `PHONE_ATTEMPT_KEYWORDS` — terms for failed/successful call attempts

3. Update `docs/AI_BUSINESS_CONTEXT.md` to reflect the new market.

## Cost Impact

| Scenario | Input tokens/lead | Cost/10 leads | Monthly (100 leads/day, 30d, 90% cache) |
|----------|------------------|---------------|------------------------------------------|
| Without chatter | ~500 | ~$0.002 | ~$1.50 |
| With chatter (3 msgs) | ~1000 | ~$0.004 | ~$3.00 |

Both scenarios are well within the $10/month budget cap.
HTML stripping keeps chatter tokens minimal even for long messages.
