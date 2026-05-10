# CRM AI Engine

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)
![Tests](https://img.shields.io/badge/tests-60%20passed-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-85%25-green)

Read-only intelligence dashboard for Odoo CRM. Connects to Odoo via JSON-RPC and surfaces
follow-up risk, data quality issues, and pipeline summaries for Sales Managers and Top Management.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and fill in environment variables
cp .env.example .env
# Edit .env with your Odoo URL, credentials, and Basic Auth password

# 3. Run the application
uvicorn backend.main:app --reload
```

Open [http://localhost:8000/dashboard](http://localhost:8000/dashboard) and sign in with the
`BASIC_AUTH_USERNAME` / `BASIC_AUTH_PASSWORD` you set in `.env`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      HTTP Clients                        │
│              (Browsers / API consumers)                  │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                   FastAPI (main.py)                      │
│   Middleware (Request ID) │ Exception Handlers           │
│   Lifespan (init cache, create service)                  │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│               API Layer  /api/v1/                        │
│   deps.py (BasicAuth, DI)                                │
│   health │ summary │ followup │ data_quality │ dashboard │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│          modules/crm/  (business logic)                  │
│   service │ client (httpx+tenacity) │ stage_resolver     │
│   domain  │ schemas (Pydantic v2)                        │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│          core/  config │ exceptions │ security           │
│                 cache  │ logging                         │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                  Odoo JSON-RPC (read-only)                │
└─────────────────────────────────────────────────────────┘
```

Full architecture details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Web framework | FastAPI 0.115 |
| HTTP client | httpx 0.27 (sync, connection pool) |
| Retry | tenacity (exponential backoff) |
| Config | pydantic-settings 2.x |
| Validation | Pydantic v2 |
| Caching | cachetools TTLCache (in-memory) |
| Logging | Loguru (JSON prod / pretty dev) |
| Templates | Jinja2 |
| Tests | pytest + pytest-cov |
| Linting | Ruff |

---

## Development Setup

**Requirements:** Python 3.11+

```bash
# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# Install all dependencies (app + dev)
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Copy environment file
cp .env.example .env
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ODOO_URL` | Yes | — | Odoo instance URL |
| `ODOO_DB` | Yes | — | Database name |
| `ODOO_USERNAME` | Yes | — | Odoo login email |
| `ODOO_API_KEY` | Yes | — | Odoo API key |
| `BASIC_AUTH_USERNAME` | Yes | — | Dashboard login user |
| `BASIC_AUTH_PASSWORD` | Yes | — | Dashboard login password |
| `CACHE_TTL_SECONDS` | No | `60` | API response cache TTL |
| `ENVIRONMENT` | No | `development` | `development` or `production` |
| `CRM_CRITICAL_STAGE_IDS` | No | `28,34,35,37,41` | Comma-separated stage IDs |
| `CRM_CLOSED_EXCLUDED_STAGE_IDS` | No | `26,30,31,32,38,42,46` | Stages excluded from counts |
| `CRM_DATA_QUALITY_STAGE_IDS` | No | `44` | Stages flagged for data quality |

---

## Running Tests

```bash
# All tests with coverage report
pytest tests/ -v --cov=backend --cov-report=term-missing

# Unit tests only
pytest tests/unit/ -v

# Integration tests only
pytest tests/integration/ -v

# Single test file
pytest tests/unit/modules/crm/test_client.py -v
```

### Test Suite Summary

```
tests/unit/core/test_config.py          — Settings validation
tests/unit/core/test_cache.py           — TTLCache wrapper
tests/unit/core/test_security.py        — Basic Auth verify
tests/unit/modules/crm/test_client.py   — Read-only enforcement, retry
tests/unit/modules/crm/test_service.py  — Business logic, caching
tests/unit/modules/crm/test_stage_resolver.py — Stage name cache
tests/integration/test_api_v1.py        — Full API endpoint tests
```

---

## Running the Mock Odoo Server

No real Odoo instance needed for development:

```bash
# Windows
.\scripts\run_mock_odoo.ps1

# Linux/macOS
bash scripts/run_mock_odoo.sh
```

The mock server starts at `http://localhost:8069` and responds to JSON-RPC calls with
50 synthetic leads, 5 teams, and 8 salespeople.

Then point your `.env` at it:

```
ODOO_URL=http://localhost:8069
ODOO_DB=mock
ODOO_USERNAME=admin
ODOO_API_KEY=mock-key
```

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Liveness probe |
| GET | `/api/v1/health` | Yes | Authenticated health |
| GET | `/api/v1/health/odoo` | Yes | Odoo connectivity check |
| GET | `/api/v1/summary` | Yes | Pipeline summary |
| GET | `/api/v1/followup-risk` | Yes | Overdue leads |
| GET | `/api/v1/data-quality/missing-contact` | Yes | Leads missing contact info |
| GET | `/dashboard` | Yes | HTML management dashboard |
| GET | `/data-quality/missing-contact` | Yes | HTML data quality view |

Legacy paths (`/crm/*`) redirect 301 to their `/api/v1/` equivalents.

---

## Read-Only Guarantee

Write operations are blocked at the client layer — before any authentication or network call:

```python
ALLOWED_METHODS = frozenset({
    "search_read", "read_group", "search_count",
    "search", "read", "fields_get", "name_search", "name_get",
})

def _ensure_read_only(method: str) -> None:
    if method not in ALLOWED_METHODS:
        raise ReadOnlyViolationError(...)
```

`create`, `write`, and `unlink` raise `ReadOnlyViolationError` (HTTP 403). This is unit-tested
and cannot be bypassed by configuration.

---

## Project Structure

```
CRM-AI-Engine/
├── backend/
│   ├── main.py                  ← FastAPI app, middleware, lifespan
│   ├── api/
│   │   ├── deps.py              ← Auth + DI dependencies
│   │   └── v1/endpoints/        ← Route handlers
│   ├── core/                    ← Config, cache, security, logging, exceptions
│   ├── modules/crm/             ← OdooClient, CrmService, schemas, domain
│   └── shared/audit.py          ← Audit log writer
├── frontend/templates/          ← Jinja2 HTML templates
├── tests/
│   ├── unit/                    ← Fast, isolated unit tests
│   ├── integration/             ← API-level tests (no real Odoo)
│   └── mock_odoo/               ← Mock JSON-RPC server for dev
├── docs/                        ← ARCHITECTURE.md, PHASE_1_REPORT.md
├── scripts/                     ← Helper scripts
├── .env.example
├── requirements.txt
├── requirements-dev.txt
└── pyproject.toml
```

---

## Contributing

1. Branch from `main`
2. Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `refactor:`, `test:`, `docs:`
3. Run `ruff check .` and `pytest tests/` before pushing
4. Keep the read-only invariant — never add write-capable Odoo methods

---

## Roadmap

- **Phase 2:** AI-powered insights (lead scoring, churn prediction)
- **Phase 3:** Inventory module integration
- **Phase 4:** Finance & Marketing modules
- **Phase 5:** Render deployment + CI/CD pipeline
