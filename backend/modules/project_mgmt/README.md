# Project Management Module

**Status:** 🚧 Coming Soon

## Planned Scope

This module will deliver a read-only AI intelligence layer over Odoo's project and task management data. It will flag overdue tasks, identify blocked projects, surface team workload imbalances, and highlight projects at risk of missing deadlines — enabling project managers to intervene before delays cascade.

All data access is strictly read-only. No projects, tasks, or timesheets are ever created or modified through this engine.

## Intended Odoo Data Sources

- `project.project` — project status, deadline, manager, stage
- `project.task` — task assignments, deadlines, state
- `project.task.type` — Kanban stage definitions
- `account.analytic.line` — timesheet entries and hours logged
- `mail.message` — task-level communication history

## Sample AI Queries

1. "Which projects are behind schedule this week?"
2. "Show me tasks overdue by more than 3 days with no recent activity"
3. "Which team members have the highest open task count right now?"
4. "What percentage of this quarter's milestones are on track?"
5. "Which projects have tasks blocked with no assignee?"
