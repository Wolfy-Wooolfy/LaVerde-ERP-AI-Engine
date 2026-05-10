# UI Design Guide — CRM AI Engine Phase 3

## Design Philosophy

The Phase 3 dashboard follows a **data-first, low-noise** aesthetic modelled on Linear, Vercel, and Stripe Dashboard:

- **Less is more** — every element earns its place
- **Information density** — KPIs, charts, and tables packed efficiently without feeling cramped
- **Micro-interactions** — hover lifts on cards, number count-ups, sparkline transitions; never bouncy
- **Dual theme** — dark mode is not an afterthought; every component is designed in both

---

## Color System

| Role | Light | Dark | Token |
|---|---|---|---|
| Background | `neutral-50` (#fafafa) | `neutral-950` (#0a0a0a) | `bg-neutral-50 dark:bg-neutral-950` |
| Card surface | `white` | `neutral-800` (#262626) | `bg-white dark:bg-neutral-800` |
| Sidebar/Topbar | `white` | `neutral-900` (#171717) | `bg-white dark:bg-neutral-900` |
| Border | `neutral-200` | `neutral-700` | `border-neutral-200 dark:border-neutral-700` |
| Text primary | `neutral-900` | `neutral-100` | `text-neutral-900 dark:text-neutral-100` |
| Text muted | `neutral-500` | `neutral-400` | `text-neutral-500 dark:text-neutral-400` |
| Accent | `primary-600` (#4f46e5) | `primary-400` (#818cf8) | `text-primary-600 dark:text-primary-400` |
| Danger | `danger-600` (#e11d48) | `danger-400` (#fb7185) | `text-danger-600 dark:text-danger-400` |
| Warning | `warning-600` (#d97706) | `warning-400` (#fbbf24) | `text-warning-600 dark:text-warning-400` |
| Success | `success-600` (#059669) | `success-400` (#34d399) | `text-success-600 dark:text-success-400` |

### Status color rules for KPI cards

| Condition | Variant | Example |
|---|---|---|
| Risk / critical | `danger` | Critical Overdue ≥ 1 |
| Caution / action needed | `warning` | Overdue Follow-ups, Missing Contact |
| Healthy / positive | `success` | Follow-ups Today |
| Informational | `default` or `info` | Total Leads |

---

## Typography

| Usage | Class |
|---|---|
| Page title (h1) | `text-2xl font-bold` |
| Section title | `text-sm font-semibold` |
| Card label | `text-xs font-medium uppercase tracking-wide` |
| KPI value | `text-3xl font-bold tabular` |
| Body text | `text-sm` |
| Caption / metadata | `text-xs text-neutral-500` |

- **Primary font**: Inter (loaded from Google Fonts, self-hosted fallback available)
- **Numeric font**: JetBrains Mono for counts, IDs — `font-mono tabular`

---

## Layout

```
┌─────────────────────────────────────────────────────────┐
│  Sidebar (240px / 64px collapsed)  │  Main area         │
│  ┌──────────────────────────────┐  │  ┌──────────────┐  │
│  │ Logo                         │  │  │ Topbar (64px)│  │
│  │ Nav items                    │  │  ├──────────────┤  │
│  │ ...                          │  │  │              │  │
│  │ User / Logout                │  │  │  Content     │  │
│  └──────────────────────────────┘  │  │              │  │
└─────────────────────────────────────────────────────────┘
```

### Sidebar states
- **Desktop expanded**: 240px, icons + text
- **Desktop collapsed**: 64px, icons only (toggle via collapse button)
- **Mobile**: hidden, full-width drawer overlay on hamburger tap

### Breakpoints
| Breakpoint | Width | Behavior |
|---|---|---|
| Mobile | < 640px | 1-column KPIs, stacked charts |
| Tablet | 640–1023px | 2-column KPIs, 2-column charts |
| Desktop | 1024–1279px | Sidebar visible, 3-col charts |
| Wide | 1280px+ | 4-col KPIs, full matrix table |
| Ultra-wide | 1536px+ | 7-col KPI grid |

---

## Spacing & Radius

| Element | Radius | Padding |
|---|---|---|
| Cards | `rounded-xl` (12px) | `p-5` |
| Badges | `rounded-full` | `px-2 py-0.5` |
| Buttons | `rounded-lg` (8px) | `px-4 py-2` |
| Input fields | `rounded-lg` | `px-3 py-2` |
| Modals | `rounded-2xl` (16px) | `px-6 py-5` |

---

## Animation Principles

- **Page load**: `animate-fade-in` (200ms ease-out) on main content wrapper
- **Card hover**: `hover:-translate-y-0.5 hover:shadow-card-hover` (200ms)
- **Button press**: `active:scale-[0.98]` (immediate)
- **Toast**: `animate-slide-in-right` (250ms ease-out)
- **Skeleton shimmer**: `shimmer` 1.5s infinite gradient sweep
- **Number count-up**: 600ms ease-out-cubic on KPI refresh

**Never**: bouncing, glow effects, carousels, heavy entrance animations.

---

## Dark Mode Implementation

Dark mode uses Tailwind's `class` strategy. The `dark` class is toggled on `<html>` by JavaScript:

```js
document.documentElement.classList.toggle('dark', isDark);
```

Three modes:
- `light` — always light
- `dark` — always dark
- `system` — follows `prefers-color-scheme`

Persisted in `localStorage.crmTheme`. Applied before first paint (inline script in `<head>`) to prevent FOUC.

---

## RTL Support

When language = Arabic:
- `dir="rtl"` is set on `<html>` (server-side via Jinja2 context)
- Tailwind `rtl:` variants handle mirror-flips (arrows, padding, margins)
- Chart.js legends automatically reflow

Toggle via the language switcher in the topbar. Language is stored in both `localStorage.crmLang` and a `lang` cookie (for server-side detection).
