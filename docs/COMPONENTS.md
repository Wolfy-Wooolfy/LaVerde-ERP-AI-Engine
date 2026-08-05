# Component Library — CRM AI Engine Phase 3

All components live in `frontend/templates/components/` as Jinja2 macros.

---

## Usage Pattern

```html
{% from "components/_badge.html" import badge %}
{{ badge("Active", "success") }}
```

---

## `_badge.html` — Status badges

```html
{% from "components/_badge.html" import badge %}

{{ badge("Active",   "success") }}   <!-- green -->
{{ badge("Overdue",  "danger")  }}   <!-- red -->
{{ badge("Pending",  "warning") }}   <!-- amber -->
{{ badge("Info",     "info")    }}   <!-- indigo -->
{{ badge("Default",  "neutral") }}   <!-- gray -->
{{ badge("Live",     "success", dot=true) }}  <!-- with dot indicator -->
```

---

## `_button.html` — Buttons

```html
{% from "components/_button.html" import btn %}

{{ btn("Save",   variant="primary") }}
{{ btn("Cancel", variant="secondary") }}
{{ btn("Delete", variant="danger") }}
{{ btn("Edit",   variant="ghost") }}
{{ btn("Export", variant="secondary", size="sm") }}
{{ btn("Go",     href="/dashboard") }}         <!-- renders as <a> -->
{{ btn("Submit", type="submit", variant="primary") }}
```

Variants: `primary` `secondary` `ghost` `danger`
Sizes: `sm` (default: normal)

---

## `_skeleton.html` — Loading skeletons

```html
{% from "components/_skeleton.html" import kpi_skeleton, table_skeleton, chart_skeleton %}

{# 7 KPI placeholders while loading #}
{% for _ in range(7) %}{{ kpi_skeleton() }}{% endfor %}

{# Table with 5 rows, 4 columns #}
{{ table_skeleton(rows=5, cols=4) }}

{# Chart panel skeleton #}
{{ chart_skeleton() }}
```

---

## `_empty_state.html` — Empty states

```html
{% from "components/_empty_state.html" import empty_state %}

{{ empty_state("No records found") }}
{{ empty_state("No overdue items", "All leads are up to date.", icon="chart") }}
{{ empty_state("Connection error", "Failed to reach Odoo.", show_retry=true) }}
```

Icons: `inbox` `chart` `doc`

---

## `_toast.html` — Toast notifications

Toasts are created programmatically via JS:

```js
toast.show("Data refreshed",         "success", 3000);
toast.show("Failed to connect",      "danger");
toast.show("Refresh scheduled",      "info",    2000);
toast.show("Cache will expire soon", "warning");
```

Or inline from Jinja2 (for server-side flash messages):
```html
{% from "components/_toast.html" import toast_markup %}
{{ toast_markup("Saved!", "success") }}
```

---

## `_modal.html` — Modal dialogs

```html
{% from "components/_modal.html" import modal %}

{% call modal(id="confirmDelete", title="Confirm Delete", size="sm") %}
  <p class="text-sm text-neutral-600">Are you sure?</p>
  <div class="flex justify-end gap-2 mt-4">
    <button onclick="$dispatch('close-modal-confirmDelete')" class="btn btn-secondary">Cancel</button>
    <button class="btn btn-danger">Delete</button>
  </div>
{% endcall %}

{# Trigger from anywhere: #}
<button @click="$dispatch('open-modal-confirmDelete')">Open</button>
```

Sizes: `sm` `md` `lg` `xl`

---

## `_pagination.html` — Page navigation

```html
{% from "components/_pagination.html" import pagination %}

{{ pagination(pagination_obj, base_url="/data-quality/missing-contact") }}
{{ pagination(pagination_obj, base_url="/results", extra_params="&sort=name") }}
```

`pagination_obj` must have: `page`, `page_size`, `total`, `total_pages`, `has_prev`, `has_next`

---

## `_breadcrumb.html` — Breadcrumb trail

```html
{% from "components/_breadcrumb.html" import breadcrumb %}

{{ breadcrumb([
  ("Dashboard", "/dashboard"),
  ("Data Quality", "/data-quality"),
  ("Missing Contacts", none),
]) }}
```

Last item (href=none) is the current page.

---

## `_kpi_card.html` — KPI metric card

```html
{% from "components/_kpi_card.html" import kpi_card %}

{{ kpi_card(
  label="Total Leads",
  value=summary.total_leads,
  variant="default",          # default|danger|warning|success|info
  icon="users",               # users|alert|clock|check|phone|user-x|activity
  sparkline_metric="total_leads",   # REQUIRED — see below
  href="/dashboard"           # optional click destination
) }}
```

`sparkline_metric` no longer draws a sparkline. The mini-charts and trend badges
were removed because they were fabricated, not measured (OPEN_BACKLOG item 13).
The argument kept its name on purpose: it is the sole source of `data-kpi-value`,
the attribute `app.js:146` matches to refresh the KPI number. **Every `kpi_card()`
call must pass it** — omit it and the card renders `data-kpi-value=""`, which
matches nothing and silently never refreshes. Guarded by
`tests/unit/core/test_kpi_vocabulary_consistency.py`.

---

## `_chart_container.html` — Chart wrapper panel

```html
{% from "components/_chart_container.html" import chart_container %}

{% call chart_container("Activity Distribution", "activityChart", height="h-64") %}
{% endcall %}
{# Canvas element #activityChart is available for Chart.js init #}
```

---

## `_table.html` — DataTable wrapper

```html
{% from "components/_table.html" import data_table, th, td %}

{% call data_table(id="myTable", caption="Overdue by Team", page_length=10) %}
<thead><tr>{{ th("Team") }}{{ th("Count") }}</tr></thead>
<tbody>
  {% for row in rows %}<tr>{{ td(row.team) }}{{ td(row.count) }}</tr>{% endfor %}
</tbody>
{% endcall %}
```

---

## `_dropdown.html` — Dropdown menu

```html
{% from "components/_dropdown.html" import dropdown, dropdown_item %}

{% call dropdown(label="Actions") %}
  {{ dropdown_item("Export CSV", href="#") }}
  {{ dropdown_item("View Details", href="/details/1") }}
{% endcall %}
```
