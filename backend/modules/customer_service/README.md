# Customer Service Module

**Status:** 🚧 Coming Soon

## Planned Scope

This module will provide a read-only AI intelligence layer over Odoo's helpdesk and customer service data. It will surface ticket backlogs, response-time trends, unresolved escalations, and customer satisfaction signals — enabling service managers to act before issues become complaints.

All data access is strictly read-only. No tickets, messages, or records are ever created or modified through this engine.

## Intended Odoo Data Sources

- `helpdesk.ticket` — ticket status, priority, SLA state, assigned agent
- `helpdesk.team` — team load and escalation rules
- `mail.message` — customer communication history per ticket
- `helpdesk.sla.status` — SLA breach tracking

## Sample AI Queries

1. "Which tickets are about to breach SLA today?"
2. "Show me our top 5 unresolved escalations this week"
3. "Which support agent has the highest open ticket count?"
4. "What are the most common complaint categories this month?"
5. "Which customers have had 3 or more tickets in the last 30 days?"
