# LaVerde ERP AI Engine

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)
![Tests](https://img.shields.io/badge/tests-350%2B%20passed-brightgreen)
![Version](https://img.shields.io/badge/version-6.0.0-blue)

A **read-only AI intelligence layer** over Odoo ERP. Surfaces actionable insights across 7 business modules — CRM, Customer Service, HR, Contracts, Collections, Accounting, and Project Management — without ever writing to Odoo.

---

## What It Does

LaVerde ERP AI Engine connects to Odoo via JSON-RPC and provides:

- **Real-time dashboards** — pipeline health, overdue follow-ups, data quality issues
- **AI Priority Queue** — GPT-powered lead scoring with WhatsApp-first recommendations
- **Natural language chat** — ask questions about your CRM in Arabic or English
- **Read-only guarantee** — `ALLOWED_METHODS` enforces zero write access at the client layer

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and configure environment
cp .env.example .env
# Edit .env: ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_API_KEY,
#            BASIC_AUTH_USERNAME, BASIC_AUTH_PASSWORD, OPENAI_API_KEY

# 3. Build frontend CSS
cd frontend && npm install && npm run build:css && cd ..

# 4. Run the application
uvicorn backend.main:app --reload
```

Open [http://localhost:8000/dashboard](http://localhost:8000/dashboard) and sign in with the
`BASIC_AUTH_USERNAME` / `BASIC_AUTH_PASSWORD` you set in `.env`.

---

## Architecture

```
LaVerde ERP AI Engine
├── backend/
│   ├── core/              # Config, auth, cache, logging, metrics
│   ├── shared/
│   │   ├── odoo/          # Shared Odoo JSON-RPC client (read-only)
│   │   └── ai/            # Shared AI services (client, budget, cache, registry)
│   ├── modules/
│   │   ├── crm/           # CRM module (active)
│   │   │   └── ai/        # CRM-specific AI (prioritizer, prompts, chat)
│   │   ├── customer_service/  # 🚧 Coming Soon
│   │   ├── hr/                # 🚧 Coming Soon
│   │   ├── contracts/         # 🚧 Coming Soon
│   │   ├── collections/       # 🚧 Coming Soon
│   │   ├── accounting/        # 🚧 Coming Soon
│   │   └── project_mgmt/      # 🚧 Coming Soon
│   └── api/               # FastAPI routers and endpoints
├── frontend/
│   ├── templates/         # Jinja2 HTML (Tailwind, Alpine.js)
│   ├── translations/      # i18n (en.json, ar.json)
│   └── static/            # Self-hosted CSS, JS, vendor libs
├── tests/                 # 350+ tests (unit, integration, e2e)
└── docs/                  # Architecture, module specs, changelog
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full layer diagram and module pattern.

---

## Modules

| Module | Status | Description |
|--------|--------|-------------|
| CRM | ✅ Active | Pipeline health, overdue leads, AI prioritization, chat |
| Customer Service | 🚧 Soon | Ticket SLA, escalation risk, agent workload |
| HR | 🚧 Soon | Contract expiry, leave patterns, headcount |
| Contracts | 🚧 Soon | Renewal tracking, unsigned agreements |
| Collections | 🚧 Soon | Overdue receivables, debtor aging, payment risk |
| Accounting | 🚧 Soon | Budget variance, cash flow anomalies |
| Project Mgmt | 🚧 Soon | Task overdue, milestone risk, team workload |

See [docs/MODULES.md](docs/MODULES.md) for detailed specs.

---

## Key Constraints

- **Read-only absolute** — `ALLOWED_METHODS` in `shared/odoo/client.py` never contains create/write/unlink
- **Arabic-first UX** — fully bilingual (AR + EN), RTL layout, WhatsApp-first recommendations
- **AI cost ceiling** — `AI_MONTHLY_BUDGET_USD` hard stop with warning threshold
- **Self-hosted only** — zero CDN dependencies in production

---

## Running Tests

```bash
pytest tests/ -v --tb=short
```

Expected: 350+ tests passing, ~0 failures.

---

## Environment Variables

See `.env.example` for the full list with comments. Key variables:

| Variable | Description |
|----------|-------------|
| `ODOO_URL` | Your Odoo instance URL |
| `ODOO_API_KEY` | Odoo API key (Settings → Technical → API Keys) |
| `BASIC_AUTH_PASSWORD` | Dashboard login password |
| `OPENAI_API_KEY` | OpenAI API key for AI features |
| `AI_MONTHLY_BUDGET_USD` | Monthly AI spend ceiling (default: $10) |
| `DISPLAY_NAME` | Name shown in dashboard greeting |

---

## Changelog

See [docs/CHANGELOG.md](docs/CHANGELOG.md) for version history.

> **Note:** GitHub repo rename and local folder rename are pending user action.
> After renaming to `LaVerde-ERP-AI-Engine`, update the git remote URL.
