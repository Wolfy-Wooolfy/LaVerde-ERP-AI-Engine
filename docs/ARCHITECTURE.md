# Architecture — LaVerde ERP AI Engine v6.0

## Overview

LaVerde ERP AI Engine is a **read-only** FastAPI application that queries Odoo ERP via JSON-RPC
and presents AI-powered intelligence dashboards for Sales Managers, HR, Finance, and Top Management.

**Hard rule:** This engine NEVER writes to Odoo. `ALLOWED_METHODS` in `shared/odoo/client.py`
is a frozenset of read-only ORM methods. Any write attempt raises `ReadOnlyViolationError` before
any network call is made.

---

## Directory Structure (v6.0)

```
backend/
├── core/                  # Foundation
│   ├── config.py          # Settings (Pydantic BaseSettings)
│   ├── exceptions.py      # LaVerdeERPError + Odoo error hierarchy
│   ├── security.py        # HTTP Basic Auth
│   ├── cache.py           # TTLCache wrapper
│   ├── logging.py         # Loguru setup
│   ├── metrics.py         # Request counters, uptime
│   └── limiter.py         # slowapi rate limiter
│
├── shared/                # Cross-module services
│   ├── odoo/
│   │   └── client.py      # OdooClient (read-only, shared by ALL modules)
│   └── ai/
│       ├── client.py      # OpenAIClient (httpx-based, no openai SDK)
│       ├── budget_tracker.py  # Monthly spend enforcement
│       ├── cache.py       # Two-tier AI result cache (memory + disk)
│       ├── exceptions.py  # AIServiceError hierarchy
│       └── module_registry.py  # AIModuleSpec + AIModuleRegistry
│
├── modules/               # ERP modules (one per business domain)
│   ├── crm/               # ✅ Active
│   │   ├── ai/
│   │   │   ├── prioritizer.py   # Lead scoring via AI
│   │   │   ├── prompts.py       # Prompt builders (EN + AR)
│   │   │   ├── chatter.py       # Odoo chatter parsing
│   │   │   ├── schemas.py       # LeadContext, LeadPriority, etc.
│   │   │   ├── registry.py      # CRM_MODULE spec registration
│   │   │   └── chat/            # Natural language chat engine
│   │   │       ├── intent_parser.py
│   │   │       ├── data_fetcher.py
│   │   │       ├── response_builder.py
│   │   │       ├── session_manager.py
│   │   │       ├── prompts.py
│   │   │       └── schemas.py
│   │   ├── domain.py      # BASE_DOMAIN, stage ID helpers
│   │   ├── schemas.py     # Pydantic response models
│   │   └── service.py     # CrmService (8 concurrent Odoo calls)
│   │
│   ├── customer_service/  # 🚧 Coming Soon
│   ├── hr/                # 🚧 Coming Soon
│   ├── contracts/         # 🚧 Coming Soon
│   ├── collections/       # 🚧 Coming Soon
│   ├── accounting/        # 🚧 Coming Soon
│   └── project_mgmt/      # 🚧 Coming Soon
│
└── api/                   # HTTP layer
    ├── deps.py            # Auth + DI helpers
    └── v1/
        └── endpoints/
            ├── summary.py
            ├── followup.py
            ├── data_quality.py
            ├── dashboard.py
            ├── ai.py
            ├── chat.py
            └── health.py
```

---

## Layer Diagram

```
┌─────────────────────────────────────────────────────────┐
│                      HTTP Clients                        │
│              (Browsers / API consumers)                  │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                   FastAPI (main.py)                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  Middleware  │  │  Exception   │  │    Lifespan    │  │
│  │ (Request ID, │  │  Handlers    │  │ (init cache,   │  │
│  │  security)   │  │              │  │  register AI   │  │
│  └─────────────┘  └──────────────┘  │  modules)      │  │
│                                     └────────────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                   API Layer (api/)                       │
│  deps.py (auth, DI)  │  v1/endpoints/*                  │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│             Module Layer (modules/crm/)                  │
│  ┌──────────┐ ┌──────────────────┐ ┌──────────────────┐ │
│  │ service  │ │  ai/prioritizer  │ │  ai/chat/*       │ │
│  │(business │ │  (lead scoring)  │ │  (NL chat engine)│ │
│  │ logic)   │ │                  │ │                  │ │
│  └──────────┘ └──────────────────┘ └──────────────────┘ │
│  ┌──────────┐ ┌──────────┐                              │
│  │  domain  │ │ schemas  │                              │
│  └──────────┘ └──────────┘                              │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│             Shared Layer (shared/)                       │
│  ┌──────────────────────┐  ┌──────────────────────────┐ │
│  │  shared/odoo/client  │  │  shared/ai/*             │ │
│  │  (read-only Odoo RPC)│  │  (OpenAI, budget, cache) │ │
│  └──────────────────────┘  └──────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────┐│
│  │  shared/ai/module_registry (AIModuleSpec, registry)  ││
│  └──────────────────────────────────────────────────────┘│
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│               Core Layer (core/)                         │
│  config │ LaVerdeERPError │ security │ cache │ logging   │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                     Odoo JSON-RPC                        │
│  POST /jsonrpc  →  common.authenticate                   │
│                 →  object.execute_kw (read-only only)    │
└─────────────────────────────────────────────────────────┘
```

---

## Read-Only Enforcement

This is a **hard constraint**, not a configuration option.

```python
# backend/shared/odoo/client.py

ALLOWED_METHODS: frozenset[str] = frozenset({
    "search_read", "read_group", "search_count",
    "search", "read", "fields_get", "name_search", "name_get",
})

def _ensure_read_only(method: str) -> None:
    if method not in ALLOWED_METHODS:
        raise ReadOnlyViolationError(...)
```

`_ensure_read_only()` is called at the top of `execute_kw()` **before** any network
activity or authentication. It is unit-tested for `create`, `write`, and `unlink`.

---

## AI Module Registry Pattern

Each ERP module registers itself with `AIModuleRegistry` at startup:

```python
# backend/modules/crm/ai/registry.py
CRM_MODULE = AIModuleSpec(
    name="crm",
    display_name_en="CRM",
    display_name_ar="إدارة علاقات العملاء",
    intents=["overdue_summary", "critical_leads", ...],
    suggested_questions=[...],
    chat_endpoint="/api/v1/chat",
)

def register() -> None:
    AIModuleRegistry.register(CRM_MODULE)
```

Future modules register the same way. The registry enables intent routing
across multiple modules without hardcoding module names in the API layer.

---

## Caching Strategy

| Key | Content | TTL |
|-----|---------|-----|
| `crm:summary` | Full summary (8 concurrent Odoo calls) | `CACHE_TTL_SECONDS` (default 60s) |
| `crm:followup_risk` | Overdue breakdowns | `CACHE_TTL_SECONDS` |
| `crm:missing_contact` | Missing contact list | `CACHE_TTL_SECONDS` |
| AI lead scores | Per-lead priority + reasoning | `AI_CACHE_TTL_SECONDS` (default 6h) |
| Chat intent cache | Parsed intent per (session, message) | 30 min |
| Stage names | `crm.stage` id→name mapping | 1 hour |

---

## Stage ID Configuration

Stage IDs are Odoo database IDs that may differ between environments.
Configurable via environment variables:

```
CRM_CRITICAL_STAGE_IDS=28,34,35,37,41
CRM_CLOSED_EXCLUDED_STAGE_IDS=26,30,31,32,38,42,46
CRM_DATA_QUALITY_STAGE_IDS=44
```

Run `python scripts/diag_stages.py` to re-verify against your Odoo instance.

---

## Adding a New Module

The architecture supports new modules without touching existing code:

```
backend/modules/
└── inventory/               # new module
    ├── __init__.py
    ├── ai/
    │   ├── __init__.py
    │   ├── registry.py      # register with AIModuleRegistry
    │   └── prompts.py       # module-specific prompts
    ├── domain.py
    ├── schemas.py
    └── service.py           # uses shared/odoo/client.py
```

Then in `backend/api/v1/router.py`:
```python
from backend.api.v1.endpoints import inventory
api_v1_router.include_router(inventory.router)
```

And in `backend/main.py` lifespan:
```python
from backend.modules.inventory.ai.registry import register as register_inventory
register_inventory()
```

No changes to shared or core layers required.
