# AI Prompts — Design Decisions

## Lead Prioritization Prompt

Located in `backend/modules/ai/prompts.py`.

### System Prompt Design

The system prompt instructs GPT-4o-mini to act as a "real estate sales analyst" — domain-specific framing improves response quality vs. generic "CRM analyst" framing.

Key design decisions:

1. **Explicit tier definitions**: The 5-tier scoring rubric (critical/high/medium/low/dead) maps directly to score ranges. This prevents the model from inventing tiers.

2. **JSON-only response format**: The prompt explicitly says "Respond ONLY with valid JSON" and "Never include text outside the JSON." Combined with `response_format: {"type": "json_object"}`, this enforces structured output.

3. **Never refuse instruction**: "Never refuse to score" prevents the model from declining to score leads with sparse data. Instead, sparse data results in a low score with reasoning.

4. **Max word limits**: `reasoning` capped at 20 words, `recommended_action` at 10 words. This controls token output and keeps the UI compact.

### User Prompt

The user prompt builds a structured text block from `LeadContext` fields:
- Stage name + criticality flag (most important signal)
- Salesperson/team assignment (quality signal)
- Dates (overdue urgency)
- Contact info completeness (engagement signal)

### Iterating on Prompts

1. Edit `prompts.py` — the prompts are version-controlled and testable.
2. Run `pytest tests/unit/modules/ai/test_prompts.py` to verify structure.
3. Use `pytest -m live_api` to test against real OpenAI (costs money).
4. Update `docs/AI_PROMPTS.md` with findings.

### Known Prompt Limitations

- The model doesn't have access to the actual deal value (budget/property price) — this would significantly improve scoring accuracy.
- Activity notes are not included — the Odoo field is complex to parse.
- Stage names are used as-is from Odoo — inconsistent naming reduces accuracy.
