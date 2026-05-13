# Modules — LaVerde ERP AI Engine

This document describes the 7 planned ERP intelligence modules.

All modules share the same architectural principle: **read-only intelligence over Odoo**.
No module ever writes, creates, or modifies Odoo records.

---

## Module Status

| Module | Status | Version Target |
|--------|--------|---------------|
| CRM | ✅ Active (v1.0–v6.0) | Complete |
| Customer Service | 🚧 Coming Soon | v7.0 |
| HR | 🚧 Coming Soon | v8.0 |
| Contracts | 🚧 Coming Soon | v9.0 |
| Collections | 🚧 Coming Soon | v10.0 |
| Accounting | 🚧 Coming Soon | v11.0 |
| Project Mgmt | 🚧 Coming Soon | v12.0 |

---

## CRM Module (Active)

**Path:** `backend/modules/crm/`

The first and currently only active module. Provides:

- Pipeline summary (total leads, overdue counts, stage distribution)
- Follow-up risk analysis (overdue by salesperson, team, stage)
- Data quality monitoring (missing contacts, salesperson, stage)
- AI Priority Queue — GPT-powered lead scoring with recommended actions
- Natural language chat — bilingual (AR/EN) pipeline Q&A

**Odoo models used:**
- `crm.lead` — leads and opportunities
- `crm.stage` — stage definitions
- `crm.team` — sales teams
- `res.users` — salesperson assignments
- `mail.message` — chatter history for AI context

**AI chat intents (11):** overdue_summary, critical_leads, followup_risk,
salesperson_performance, stage_distribution, data_quality, missing_contact,
lead_detail, team_summary, pipeline_value, conversational

---

## Customer Service Module (Coming Soon)

**Path:** `backend/modules/customer_service/`

**Planned scope:** Helpdesk intelligence — SLA tracking, escalation risk, agent workload.

**Odoo models:** `helpdesk.ticket`, `helpdesk.team`, `helpdesk.sla.status`, `mail.message`

**Sample queries:**
- "Which tickets are about to breach SLA today?"
- "Show me our top 5 unresolved escalations"
- "Which support agent has the highest open ticket count?"

---

## HR Module (Coming Soon)

**Path:** `backend/modules/hr/`

**Planned scope:** Workforce intelligence — contract expiry, leave patterns, attendance anomalies.

**Odoo models:** `hr.employee`, `hr.contract`, `hr.leave`, `hr.attendance`

**Sample queries:**
- "Which employees have contracts expiring in the next 60 days?"
- "Show me departments with the highest leave usage this quarter"
- "What is the current headcount by department?"

---

## Contracts Module (Coming Soon)

**Path:** `backend/modules/contracts/`

**Planned scope:** Contract lifecycle intelligence — expiry alerts, unsigned agreements, renewal tracking.

**Odoo models:** `sale.order`, `purchase.order`, `account.analytic.account`

**Sample queries:**
- "Which contracts are expiring in the next 30 days?"
- "Show me unsigned contracts over 100,000 EGP"
- "Which vendors have contracts up for renewal this quarter?"

---

## Collections Module (Coming Soon)

**Path:** `backend/modules/collections/`

**Planned scope:** AR intelligence — debtor aging, payment risk, overdue tracking.

**Odoo models:** `account.move`, `account.move.line`, `res.partner`, `account.payment`

**Sample queries:**
- "Which customers owe more than 30 days past due?"
- "Show me the top 10 debtors by outstanding balance"
- "What is our total overdue receivables by aging bucket?"

---

## Accounting Module (Coming Soon)

**Path:** `backend/modules/accounting/`

**Planned scope:** Financial intelligence — budget variance, cash flow, reconciliation gaps.

**Odoo models:** `account.move`, `account.account`, `account.budget.line`, `account.bank.statement`

**Sample queries:**
- "Which cost centers are over budget this month?"
- "Show me unreconciled bank statement lines older than 7 days"
- "What are our top 5 expense categories year-to-date?"

---

## Project Management Module (Coming Soon)

**Path:** `backend/modules/project_mgmt/`

**Planned scope:** Project intelligence — overdue tasks, milestone risk, team workload.

**Odoo models:** `project.project`, `project.task`, `project.task.type`, `account.analytic.line`

**Sample queries:**
- "Which projects are behind schedule this week?"
- "Show me tasks overdue by more than 3 days with no recent activity"
- "Which team members have the highest open task count right now?"
