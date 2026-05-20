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

---

## Collections Dashboard — Premium Visual Identity (Stage 4)

Stage 4 (Session 13, 2026-05-20) delivered a full visual identity upgrade to the Collections Dashboard. The page now renders on a near-black `#050505` canvas by default (respecting an explicit Light theme choice), with tonal accent colors, animated live-status indicators, and a premium cheques annotation pill — a 6-pillar system built into `frontend/src/css/input.css` and driven by `activateDarkCanvas()` in `collections.js`. The upgrade targets a Chairman/Board audience and covers all 4 dashboard sections uniformly. Detailed rationale for every design decision is in `docs/MODULE_2_IMPLEMENTATION_DECISIONS.md` Session 13 (Decisions 13.1–13.6).

### 6 Pillars

| Pillar | Description |
|---|---|
| 1 — Dark Canvas | `#050505` background with a subtle danger-red radial gradient; activated at JS runtime via `collections-canvas-dark` class + inline style on `<main>` |
| 2 — KPI Headline Typography | `.kpi-headline` class: `tabular-nums`, `letter-spacing: -0.02em`, `font-weight: 500`; on dark canvas, value color overrides to per-section `-300` accent tone via CSS custom property `--accent-300-color` |
| 3 — Gradient Stroke Top Accent | `.gradient-stroke::before` pseudo-element: 1px top edge gradient fading transparent → section-accent → transparent, at 0.5 opacity |
| 4 — Live Status Indicators | `.live-dot` (6px circle) with `pulse-glow` keyframe box-shadow animation; one per card section with section-accent background color; heartbeat relative-time counter in the page header updates every 10s |
| 5 — Premium Cheques Annotation Pill | `.cheques-pill`: amber left-border pill with `breathe-subtle` opacity animation (0.85→1, 4s); shown only when `cheques_in_pipeline > 0` |
| 6 — D2.9 Auto-refresh Fix | `startAutoRefresh()` and `startHeartbeat()` both guard against interval stacking; `visibilitychange` handler stops both intervals before restarting; verified: exactly 7 fetches on load, no stacking across DevTools toggles |

### Dark Canvas Activation

`activateDarkCanvas()` is called as the first action in `init()` inside `collections.js`. Logic:

```
if localStorage.crmTheme === 'light'
  → remove collections-canvas-dark class; clear inline styles (standard light mode)
else (null / 'system' / 'dark')
  → add collections-canvas-dark class; set inline backgroundColor + backgroundImage
```

Two change listeners keep it reactive:
- **Cross-tab:** `window.storage` event fires when another tab calls `setTheme()`.
- **Same-tab:** `MutationObserver` on `document.documentElement` (watches `class`
  attribute) fires when the global theme toggle calls `applyTheme()`.

The inline style is required because `bg-neutral-50` (a Tailwind utility on `<main>`)
has higher effective specificity than `.collections-canvas-dark` (a component-layer
class) in the generated stylesheet. See Pitfalls below.

### CSS Custom Properties Per Section

Each card's root element carries three inline CSS variables:

| Variable | Purpose |
|---|---|
| `--accent-300-color` | KPI headline color on dark canvas (consumed by `.collections-canvas-dark .kpi-card .kpi-headline`) |
| `--pulse-color` | Glow ring color for `.live-dot` `pulse-glow` keyframe |
| `--stroke-gradient` | Gradient definition for the `::before` top-edge stroke |

Section accent colors: emerald (#6ee7b7 / #5DCAA5) for Portfolio, danger (#fda4af / #E24B4A) for Risk, info (#85b7eb / #378ADD) for Expected Collections, neutral (#5F5E5A) for Performance.

### Pitfalls

**Tailwind v3 dark-mode specificity tie.** Tailwind v3 compiles `dark:text-X` to
`.dark\:text-X:is(.dark *)`. The `:is()` pseudo-class contributes the specificity
of its most-specific argument — `.dark *` yields **(0,1,0)** — giving the total
selector **(0,2,0)**. A simple 2-class descendant selector like
`.collections-canvas-dark .kpi-headline` also has **(0,2,0)**. Since Tailwind
utilities are emitted later in the stylesheet, source order hands the win to the
utility and the custom color override is silently ignored.

**Fix pattern:** Always chain through a guaranteed structural ancestor to reach
**(0,3,0)**:

```css
/* Wrong — ties with dark:text-*-400 at (0,2,0); utility wins by source order */
.collections-canvas-dark .kpi-headline { color: var(--accent-300-color); }

/* Correct — (0,3,0) beats (0,2,0) regardless of source order */
.collections-canvas-dark .kpi-card .kpi-headline { color: var(--accent-300-color); }
```

Any future override of a Tailwind dark-mode color utility via a descendant selector
must include at least one structural ancestor class (`.kpi-card`, `.chart-panel`,
etc.) as the third class in the chain. A 2-class selector is never sufficient.

**`bg-neutral-50` vs dark canvas background.** The `<main class="main-content">` element
carries `bg-neutral-50` (a Tailwind utility). Utility layer rules beat component layer
rules at equal specificity. Solution: set `main.style.backgroundColor` and
`main.style.backgroundImage` directly in JS — inline styles override all stylesheet
rules regardless of specificity.
