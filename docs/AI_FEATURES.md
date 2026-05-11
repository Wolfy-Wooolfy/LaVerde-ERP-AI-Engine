# AI Features — CRM AI Engine Phase 4

## Overview

Phase 4 adds AI-powered lead prioritization to the CRM AI Engine. The system analyzes overdue leads and assigns each a priority score (0–100) with a short reasoning explanation, helping sales managers decide which leads to chase first.

## Feature: Smart Lead Prioritization

**Endpoint:** `POST /api/v1/ai/prioritize-overdue`

Fetches up to N overdue leads from Odoo, scores each one using GPT-4o-mini, and returns a ranked list sorted by priority score.

### Score Tiers

| Score | Tier | Meaning |
|-------|------|---------|
| 90–100 | critical | Near-closing stage, high urgency |
| 70–89 | high | Established interest, needs immediate follow-up |
| 50–69 | medium | Mid-funnel, needs nurturing |
| 30–49 | low | Early-stage or stale |
| 0–29 | dead | Likely lost, low ROI to pursue |

### Dashboard Integration

The AI Priority Queue section appears between the heatmap and the tables on `/dashboard`. It:
- Loads asynchronously (does not block initial page render)
- Shows 3-item skeleton while loading
- Displays top 10 overdue leads with score badge, reasoning, and recommended action
- Provides a "View in Odoo" link for each lead
- Has a manual Refresh button (15-second cooldown to avoid rate abuse)

The topbar budget pill shows real-time AI spend status.

## AI Module Architecture

```
backend/modules/ai/
├── __init__.py
├── client.py         — Async OpenAI HTTP client (direct httpx, no SDK)
├── prioritizer.py    — Lead scoring service, fetches Odoo data
├── budget_tracker.py — Monthly spend tracking with hard stop
├── prompts.py        — Prompt templates (centralized, version-controlled)
├── schemas.py        — Pydantic models for AI requests/responses
├── cache.py          — 6-hour TTL cache (in-memory + JSON disk backup)
└── exceptions.py     — AIServiceError, BudgetExceededError, etc.
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/ai/prioritize-overdue` | Fetch + score all overdue leads |
| POST | `/api/v1/ai/prioritize-lead/{id}` | Score a single lead |
| GET | `/api/v1/ai/budget` | Current monthly spend status |
| GET | `/api/v1/ai/health` | AI service health |

All require Basic Auth. All return graceful errors if AI is disabled or budget is exceeded.

## Graceful Degradation

- **Budget exhausted:** Returns HTTP 402 with `AI_BUDGET_EXCEEDED` code. Dashboard shows "Budget exhausted" message. All other dashboard functions continue to work.
- **AI disabled (`AI_ENABLED=false`):** Returns HTTP 503. No AI section loads on dashboard.
- **OpenAI unreachable:** Returns HTTP 502. Cached results shown if available.
- **Invalid AI response:** Logged and re-tried once; returns 502 if both attempts fail.

## Limitations

- Phase 4 only covers Lead Prioritization. Daily Briefing (Phase 5) and Natural Language Queries (Phase 6) are disabled by feature flags.
- AI can only READ from Odoo — the read-only enforcement is unchanged.
- Scores are computed on-demand; there is no background job that keeps scores current.
- Lead context is derived from available Odoo fields; missing fields reduce scoring accuracy.
