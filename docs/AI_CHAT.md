# AI Chat Assistant — Architecture & Developer Guide

## Overview

The AI Chat Assistant provides a natural-language interface to CRM data. Users type questions in Arabic or English and receive data-backed answers synthesised by GPT-4o-mini.

The feature is read-only by design: it can query data but can never write, update, or delete records in Odoo.

---

## Architecture

```
Browser (Alpine.js chatDrawer)
         │  POST /api/v1/chat/message
         ▼
chat.py endpoint
         │
         ├─ 1. Budget check          (BudgetTracker)
         ├─ 2. Session management    (SessionManager)
         ├─ 3. Stage 1 – Intent parse (parse_intent)
         │        └─ IntentCache (1-hour TTL)
         ├─ 4. Stage 2a – Data fetch  (fetch_data_for_intent)
         │        └─ CrmService (read-only Odoo calls)
         └─ 5. Stage 2b – Response   (build_response)
                  └─ GPT-4o-mini synthesis
```

### Two-Stage Pipeline

**Stage 1 — Intent Parsing (GPT-4o-mini, temp=0.1)**

Classifies the user question into one of 17 allowed intents and extracts optional filters (stage name, salesperson name, limit). Returns structured JSON.

Cost: ~$0.00002/call. Cached for 1 hour — identical questions (case-insensitive) skip the AI call entirely.

**Stage 2 — Data Fetch + Response Generation**

- 2a: `fetch_data_for_intent` maps the intent to the matching `CrmService` method, applies filters, returns a typed dict.
- 2b: `build_response` passes the data + question to GPT-4o-mini for a human-readable answer with follow-up suggestions. Temperature=0.6.

---

## Module Layout

```
backend/modules/ai/chat/
├── __init__.py
├── schemas.py          # Pydantic models: ChatMessage, ChatSession, ChatRequest/Response, QueryIntent
├── prompts.py          # System prompts, ALLOWED_INTENTS, SUGGESTED_QUESTIONS
├── session_manager.py  # In-memory session store (asyncio.Lock, 24h TTL, 50-msg limit)
├── intent_parser.py    # Stage 1: classify question → QueryIntent
├── data_fetcher.py     # Stage 2a: intent → CRM data dict
└── response_builder.py # Stage 2b: data + question → markdown response text
```

`backend/modules/ai/cache.py` — `IntentCache` added at bottom (cachetools TTLCache, 1h, 500 entries).

`backend/api/v1/endpoints/chat.py` — FastAPI router with 3 endpoints.

---

## Allowed Intents

| Intent | Description |
|---|---|
| `list_overdue_by_salesperson` | Ranked list of overdue leads per salesperson |
| `list_overdue_by_team` | Ranked list of overdue leads per team |
| `list_overdue_by_stage` | Ranked list of overdue leads per stage |
| `count_by_stage` | Count leads in a stage (filter: `stage`) |
| `count_by_team` | Count leads in a team (filter: `team`) |
| `count_by_salesperson` | Count leads for a salesperson (filter: `salesperson`) |
| `missing_contact_summary` | How many leads are missing contact info |
| `data_quality_summary` | Full data-quality audit (all issue types) |
| `team_performance_summary` | Overview of overdue counts across all teams |
| `salesperson_performance_summary` | Overview of overdue counts across all salespersons |
| `leads_with_site_visit_signal` | Leads showing WhatsApp معاينة signals (requires Prioritizer) |
| `recommendation_top_priority` | Top-priority leads to follow up today (requires Prioritizer) |
| `free_form_analysis` | General analytical question — returns all available data |
| `total_leads_count` | Total number of CRM leads |
| `pipeline_health` | Combined pipeline health view |
| `stage_distribution` | Distribution of leads across stages |
| `unknown` | Question not matched — triggers clarification response |

### How to Add a New Intent

1. **Register the intent name** in `prompts.py` → `ALLOWED_INTENTS` set.
2. **Add a handler** in `data_fetcher.py`:
   ```python
   async def _handle_my_new_intent(filters: dict, crm: CrmService, prioritizer) -> dict:
       data = await crm.my_new_method()
       return {"type": "my_type", ...}
   ```
3. **Register it** in `_INTENT_HANDLERS` dict in the same file.
4. **Update prompts** — add a description line in `INTENT_PARSING_SYSTEM_PROMPT` so Stage 1 knows when to emit this intent.
5. **Add tests** in `tests/unit/modules/ai/chat/test_data_fetcher.py`.

---

## Session Management

`SessionManager` stores sessions in-memory (no Redis dependency). Key parameters:

| Parameter | Default | Notes |
|---|---|---|
| `max_context_messages` | 20 | Context window sent to Stage 2 |
| `ttl_hours` | 24 | Session expiry |
| `MAX_SESSION_MESSAGES` | 50 | Hard lifetime cap before forcing new session |

A background task in `main.py` calls `cleanup_expired()` every 30 minutes to evict old sessions.

Sessions are identified by a UUID stored in `localStorage` (`chatSessionId`). A "New Chat" button generates a new UUID.

---

## Intent Cache

`IntentCache` wraps a `cachetools.TTLCache`:

- Key: `sha256(locale + ":" + question.strip().lower())[:32]`
- TTL: 3600 seconds (1 hour)
- Max entries: 500

On a cache hit, Stage 1 returns immediately with `cost_usd=0.0`.

---

## API Endpoints

### `POST /api/v1/chat/message`

**Rate limit:** 30/minute per IP.

**Request:**
```json
{"session_id": "uuid", "message": "Show overdue by salesperson"}
```

**Response:**
```json
{
  "session_id": "uuid",
  "message": {
    "id": "uuid",
    "role": "assistant",
    "content": "| Salesperson | Overdue |\n...",
    "timestamp": "...",
    "intent": "list_overdue_by_salesperson",
    "cost_usd": 0.00045
  },
  "suggested_followups": ["Which team has the most?", "..."]
}
```

### `DELETE /api/v1/chat/session/{session_id}`

Clears the session from memory. Returns `{"ok": true, "deleted": true/false}`.

### `GET /api/v1/chat/suggested-questions`

Returns 6 starter questions in the locale specified by the `lang` cookie.

---

## Frontend

The chat UI lives entirely in `base.html` (drawer component) and `frontend/static/js/chat.js` (Alpine.js data function `chatDrawer()`).

**Key files:**
- `frontend/static/js/chat.js` — Alpine.js component with `send()`, `newSession()`, `renderMarkdown()`.
- `frontend/static/vendor/marked.min.js` — self-hosted minimal markdown renderer (tables, bold, lists). No CDN.
- `frontend/translations/en.json` / `ar.json` — 16 translation keys prefixed `chat_`.

The drawer opens via the custom Alpine event `open-chat-drawer`, dispatched by the topbar button. The drawer listens with `@open-chat-drawer.window`.

---

## Greeting & Display Name

The dashboard greeting ("Good morning, La Verde") and the chat drawer welcome ("أهلاً La Verde") both read from `settings.DISPLAY_NAME`.

**Fallback chain:**
1. `DISPLAY_NAME` env var (if set) — used verbatim
2. First segment of `BASIC_AUTH_USERNAME` before `.` or `@`, capitalised

**To use the company name:**
```
# .env
DISPLAY_NAME=La Verde
```

Both the dashboard hero and the chat drawer welcome will then read "La Verde" consistently.

> **Common gotcha:** changing `DISPLAY_NAME` in `.env` has no effect until the server is restarted. Settings are read once at startup.

---

## Budget Integration

The chat pipeline participates in the existing monthly budget tracker. Each message records the sum of Stage 1 + Stage 2 costs. When the budget is exhausted, the endpoint returns HTTP 402 and the frontend shows a localised message.

---

## Security

- All endpoints require HTTP Basic auth (`get_current_user` dependency).
- All CRM data access is read-only. The `CrmService` methods used are query-only.
- No user data is persisted to disk; sessions live only in process memory.
- The `message` field is validated: `min_length=1`, `max_length=500`.
- marked.js escapes HTML before rendering to prevent XSS from AI responses.
