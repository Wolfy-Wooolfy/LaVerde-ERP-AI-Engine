# HR Module

**Status:** 🚧 Coming Soon

## Planned Scope

This module will deliver a read-only AI intelligence layer over Odoo's HR data — including employees, contracts, leaves, and attendance. It will help HR managers identify turnover risk, track leave patterns, flag employees approaching contract expiry, and surface workforce insights without ever modifying HR records.

All data access is strictly read-only. No employee records, leave requests, or contracts are ever created or modified through this engine.

## Intended Odoo Data Sources

- `hr.employee` — employee profiles, department, manager, join date
- `hr.leave` — leave requests and approvals
- `hr.leave.allocation` — leave balance tracking
- `hr.attendance` — check-in / check-out records
- `hr.contract` — contract type, salary grade, expiry date

## Sample AI Queries

1. "Which employees have contracts expiring in the next 60 days?"
2. "Show me departments with the highest leave usage this quarter"
3. "Who has been absent more than 5 days this month?"
4. "Which new hires from the last 6 months have not completed onboarding activities?"
5. "What is the current headcount by department?"
