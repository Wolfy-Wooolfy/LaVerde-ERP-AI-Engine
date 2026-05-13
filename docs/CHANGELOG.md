# Changelog — LaVerde ERP AI Engine

All notable changes to this project are documented in this file.

---

## v6.0.0 — LaVerde ERP AI Engine Rebrand (2026-05-13)

**Theme:** Multi-module architecture + rebrand

### Renamed
- Project: CRM AI Engine → LaVerde ERP AI Engine
- Base exception: `CRMAIEngineError` → `LaVerdeERPError`
- App version: 2.0.0 → 6.0.0
- Frontend chat labels: "Ask CRM AI" → "Ask AI", "CRM AI" → "LaVerde AI"

### Architecture
- Created `backend/shared/odoo/` — shared Odoo JSON-RPC client
- Created `backend/shared/ai/` — shared AI services (client, budget tracker, cache, exceptions)
- Created `backend/modules/crm/ai/` — CRM-specific AI (prioritizer, prompts, chatter, chat)
- Added `AIModuleRegistry` in `backend/shared/ai/module_registry.py`
- Added CRM module registration in `backend/modules/crm/ai/registry.py`

### New Modules (stubs)
- `backend/modules/customer_service/`
- `backend/modules/hr/`
- `backend/modules/contracts/`
- `backend/modules/collections/`
- `backend/modules/accounting/`
- `backend/modules/project_mgmt/`

### UI
- Sidebar module switcher — CRM (active) + 6 Coming Soon entries
- Bilingual: "Modules" / "الوحدات"

---

## v5.0.0 — Phase 5: Bug Hunt + Chat Hardening (2025)

**Theme:** Comprehensive verification + 14 hidden bugs fixed

### Highlights
- 350+ tests passing
- 92/100 comprehensive verification score
- 40/44 user journey scenarios passing
- AI cost < $1 across all Phase 5 testing
- Chat session management hardened
- Follow-up suggestions validated against live Odoo data
- Arabic locale bugs fixed ("موظف مبيعات" enforced, never "مندوب")

---

## v4.0.0 — Phase 4: AI Chat

**Theme:** Natural language chat over CRM data

### Highlights
- Bilingual chat drawer (AR + EN)
- 11 structured intents + conversational fallback
- Intent caching (6-hour TTL)
- Session management with auto-expiry
- Follow-up suggestion generation
- Budget-aware: chat pauses at AI spend limit

---

## v3.0.0 — Phase 3: Enterprise Frontend

**Theme:** Production-grade UI

### Highlights
- Tailwind CSS design system
- Alpine.js reactive components
- Dark mode / light mode / system preference
- RTL (Arabic) + LTR (English) layouts
- Self-hosted fonts, vendor libs (no CDN)
- DataTables with server-side pagination
- Overdue heatmap (salesperson × stage)

---

## v2.0.0 — Phase 2: AI Priority Queue

**Theme:** GPT-powered lead prioritization

### Highlights
- OpenAI integration (gpt-4o-mini)
- Lead scoring with tier classification (critical/high/medium/low/dead)
- Per-lead result caching (6-hour TTL)
- Monthly budget hard stop
- Chatter context fed to AI prompts

---

## v1.0.0 — Phase 1: Core Dashboard

**Theme:** Read-only Odoo integration

### Highlights
- FastAPI application with Basic Auth
- Odoo JSON-RPC client with read-only enforcement
- 8 KPI cards (total leads, overdue, follow-ups, data quality)
- Charts (activity donut, salesperson bar, stage bar)
- Stage resolution with 1-hour cache
- Rate limiting, security headers, CORS
