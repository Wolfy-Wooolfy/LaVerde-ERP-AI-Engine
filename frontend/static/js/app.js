/**
 * app.js — Core Alpine.js application + theme/lang/sidebar/refresh managers
 */

'use strict';

// ── Alpine data component ─────────────────────────────────────────────────────
document.addEventListener('alpine:init', () => {
  Alpine.data('crmApp', () => ({
    sidebarCollapsed: localStorage.getItem('crmSidebarCollapsed') === 'true',
    mobileOpen: false,
    theme: localStorage.getItem('crmTheme') || 'system',
    lang: localStorage.getItem('crmLang') || document.documentElement.getAttribute('data-lang') || 'en',
    refreshing: false,

    init() {
      this.applyTheme();
      this.applyLang();

      // Listen for system theme changes
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if (this.theme === 'system') this.applyTheme();
      });

      // Auto-refresh every 1h
      setInterval(() => this.autoRefresh(), 3_600_000);
    },

    applyTheme() {
      const isDark =
        this.theme === 'dark' ||
        (this.theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
      document.documentElement.classList.toggle('dark', isDark);
    },

    setTheme(t) {
      this.theme = t;
      localStorage.setItem('crmTheme', t);
      this.applyTheme();
      // Redraw charts on theme change
      if (typeof reinitCharts === 'function') reinitCharts();
    },

    applyLang() {
      document.documentElement.lang = this.lang;
      document.documentElement.dir = this.lang === 'ar' ? 'rtl' : 'ltr';
    },

    setLang(l) {
      this.lang = l;
      localStorage.setItem('crmLang', l);
      // Set cookie for server-side detection
      document.cookie = `lang=${l};path=/;max-age=${60 * 60 * 24 * 365}`;
      this.applyLang();
      window.location.reload();
    },

    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed;
      localStorage.setItem('crmSidebarCollapsed', this.sidebarCollapsed);
    },

    async autoRefresh() {
      if (document.hidden) return;
      if (typeof crmRefresh === 'function') await crmRefresh();
    },
  }));
});

// ── Loading bar ───────────────────────────────────────────────────────────────
const loadingBar = {
  show() {
    const el = document.getElementById('loading-bar');
    if (!el) return;
    el.style.transform = 'scaleX(0.7)';
    el.style.opacity = '1';
  },
  complete() {
    const el = document.getElementById('loading-bar');
    if (!el) return;
    el.style.transform = 'scaleX(1)';
    setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'scaleX(0)'; }, 300);
  },
};

// ── Manual-refresh affordance ─────────────────────────────────────────────────
// One implementation of "a refresh is running", shared by crmRefresh and by the
// two dashboard bundles that fetch their own KPIs.
//
// It exists because a manual refresh is now SLOW BY DESIGN: it carries
// ?refresh=1, so the server skips its cache and goes to Odoo. Measured on
// /collections/dashboard, a cached fetch takes ~20ms and the bypassed one took
// 3401ms — 3.4 seconds during which the page was completely static and told the
// user nothing.
//
// Exposed on `window` because collections.js and customer_accounts.js are
// separate classic scripts. `loadingBar` deliberately is NOT: it is a top-level
// `const`, a script-scoped lexical binding that this object closes over. There
// is no `window.loadingBar` and nothing outside app.js can reach it directly.
//
// Every DOM lookup is guarded and re-done per call — these run on pages where
// #loading-bar or #refresh-icon may be absent, and a node captured once could
// outlive the element actually on screen.
window.crmRefreshFeedback = {
  start() {
    loadingBar.show();
    const icon = document.getElementById('refresh-icon');
    if (icon) icon.classList.add('animate-spin');
  },
  stop() {
    loadingBar.complete();
    const icon = document.getElementById('refresh-icon');
    if (icon) icon.classList.remove('animate-spin');
  },
};

// ── Toast manager ─────────────────────────────────────────────────────────────
window.toast = {
  _id: 0,
  show(message, type = 'info', duration = 4000) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const id = `toast-${++this._id}`;
    const colors = {
      success: 'text-emerald-500',
      warning: 'text-amber-500',
      danger:  'text-rose-500',
      info:    'text-indigo-500',
    };
    const icons = {
      success: '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>',
      warning: '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>',
      danger:  '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>',
      info:    '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>',
    };

    const el = document.createElement('div');
    el.id = id;
    el.className = 'toast pointer-events-auto animate-slide-in-right';
    el.setAttribute('role', 'alert');
    el.innerHTML = `
      <div class="shrink-0 mt-0.5 ${colors[type] || colors.info}">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">${icons[type] || icons.info}</svg>
      </div>
      <p class="flex-1 text-sm text-neutral-700 dark:text-neutral-300">${message}</p>
      <button onclick="this.closest('.toast').remove()"
        class="shrink-0 text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-200 transition-colors">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
        </svg>
      </button>`;
    container.appendChild(el);

    if (duration > 0) {
      setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateX(100%)'; el.style.transition = 'all 0.2s'; setTimeout(() => el.remove(), 200); }, duration);
    }
  },
};

// ── Data refresh ──────────────────────────────────────────────────────────────
// `manual` is true ONLY for a user-initiated refresh. It is threaded to
// crmWithRefresh (api.js) and decides whether this request carries ?refresh=1
// and so bypasses the server-side cache. The hourly autoRefresh above calls
// crmRefresh() with no argument on purpose — see the note in api.js.
window.crmRefresh = async function(manual) {
  // Unconditional, including on the hourly automatic tick. That is the
  // behaviour this page has always had; it is inconsistent with the two
  // dashboards below, which show the affordance only when `manual`. Logged in
  // docs/OPEN_BACKLOG.md rather than changed here — it is a third page, and
  // altering it would need browser verification this change has not had.
  crmRefreshFeedback.start();

  try {
    const res = await crmApi.get(crmWithRefresh('/api/v1/dashboard/kpis', manual));
    if (res.ok && res.kpis) {
      // Update KPI values in DOM, counting the selectors that actually matched.
      // A matched element counts even when the incoming value equals the one on
      // screen: that metric WAS refreshed, it merely did not move. A selector
      // that matches nothing means the payload was discarded — no pixel on this
      // page corresponds to it.
      let matchedKpis = 0;
      for (const [metric, value] of Object.entries(res.kpis)) {
        const el = document.querySelector(`[data-kpi-value="${metric}"]`);
        if (el) {
          matchedKpis++;
          animateNumber(el, parseInt(el.textContent.replace(/,/g, '')) || 0, value);
        }
      }

      // Tracked separately from the KPI count: a clock advancing only proves
      // time passed, not that data was refreshed, so it never justifies a
      // success message on its own — and it must not tick on a page that did
      // not refresh.
      const lu = document.getElementById('last-updated-time');
      const timestampFound = lu !== null;

      // Gate the success toast on selectors that actually matched. This endpoint
      // feeds the CRM dashboard only, but the button that calls it lives in the
      // shared top bar, so on every other page the response updates nothing. A
      // fluent "Data refreshed" on a page that did not change is worse than no
      // message at all: it teaches the user to trust a signal that is false.
      if (matchedKpis > 0) {
        if (timestampFound) lu.textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
        toast.show('Data refreshed', 'success', 2000);
      } else {
        toast.show('Nothing to refresh on this page', 'info', 2000);
      }
    }
  } catch (e) {
    toast.show('Failed to refresh data', 'danger');
  } finally {
    crmRefreshFeedback.stop();
  }
};

// ── Manual refresh entry point ────────────────────────────────────────────────
// The single place a click or keystroke enters the refresh machinery, and the
// only place that decides between the two strategies.
//
// Client-fetch pages (CRM dashboard) re-request their own data and repaint in
// place. Server-rendered pages have no client data path at all — their figures
// arrived with the page GET — so the only way to refresh them is to fetch the
// page again with the cache bypassed.
//
// Which one applies is declared by the page, never inferred: base.html renders
// data-refresh-mode from a Jinja block defaulting to "fetch", and the ten SSR
// templates override it to "reload". Sniffing the DOM instead (e.g. "is there a
// [data-kpi-value] here?") would rebuild exactly the silent coupling documented
// at dashboard_api.py:42-63, where a rename nothing could see broke five cards.
//
// Collections and Customer Accounts replace #refresh-btn.onclick with their own
// handlers, so this function is not reached on those two pages.
window.crmManualRefresh = function() {
  const declared = document.querySelector('[data-refresh-mode]');
  if (declared && declared.getAttribute('data-refresh-mode') === 'reload') {
    window.location.href = crmWithRefresh(window.location.href, true);
    return;
  }
  crmRefresh(true);
};

// ── Drop ?refresh=1 once the page has used it ─────────────────────────────────
// The reload above leaves refresh=1 in the address bar. Left there it is
// inherited by F5, by a bookmark, and by any link the user copies to a
// colleague — turning one deliberate bypass into a permanent one for everybody
// who receives it, against the cache that keeps Odoo load survivable.
//
// replaceState, never pushState: pushState would add a history entry, so Back
// would land on the refreshing URL and re-arm it. Runs at parse time rather
// than on DOMContentLoaded so the URL is already clean if the user reloads
// early, and runs on every page because a stale link can be opened anywhere.
(function stripRefreshParam() {
  if (!window.history || typeof history.replaceState !== 'function') return;
  const current = new URL(window.location.href);
  if (!current.searchParams.has('refresh')) return;
  current.searchParams.delete('refresh');
  history.replaceState(null, '', current.pathname + current.search + current.hash);
}());

// ── Number animation ──────────────────────────────────────────────────────────
function animateNumber(el, from, to) {
  const duration = 600;
  const start = performance.now();
  const diff = to - from;
  function step(now) {
    const elapsed = now - start;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3); // ease-out-cubic
    el.textContent = Math.round(from + diff * eased).toLocaleString();
    if (progress < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// ── CSV export ────────────────────────────────────────────────────────────────
window.exportTableCSV = function(tableId) {
  const table = document.getElementById(tableId);
  if (!table) return;

  const rows = Array.from(table.querySelectorAll('tr'));
  const csv = rows.map(row => {
    const cells = Array.from(row.querySelectorAll('th, td'));
    return cells.map(c => `"${c.textContent.trim().replace(/"/g, '""')}"`).join(',');
  }).join('\n');

  const blob = new Blob(['﻿' + csv], {type: 'text/csv;charset=utf-8;'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${tableId}-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  toast.show('CSV exported', 'success', 2000);
};

// ── Keyboard shortcuts ────────────────────────────────────────────────────────
document.addEventListener('keydown', (e) => {
  // Cmd/Ctrl+Shift+R → refresh (a keystroke is a human, so: manual)
  if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'R') {
    e.preventDefault();
    crmManualRefresh();
  }
});

// ── Accessibility: skip link ──────────────────────────────────────────────────
// Handled in HTML via anchor #main-content

// ── Prefers-reduced-motion ────────────────────────────────────────────────────
if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  const style = document.createElement('style');
  style.textContent = '*, *::before, *::after { animation-duration: 0.001ms !important; transition-duration: 0.001ms !important; }';
  document.head.appendChild(style);
}
