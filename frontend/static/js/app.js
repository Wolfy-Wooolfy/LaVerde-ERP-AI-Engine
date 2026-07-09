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
window.crmRefresh = async function() {
  const refreshIcon = document.getElementById('refresh-icon');
  if (refreshIcon) refreshIcon.classList.add('animate-spin');
  loadingBar.show();

  try {
    const res = await crmApi.get('/api/v1/dashboard/kpis');
    if (res.ok && res.kpis) {
      // Update KPI values in DOM
      for (const [metric, value] of Object.entries(res.kpis)) {
        const el = document.querySelector(`[data-kpi-value="${metric}"]`);
        if (el) animateNumber(el, parseInt(el.textContent.replace(/,/g, '')) || 0, value);
      }
      // Update last-updated time
      const lu = document.getElementById('last-updated-time');
      if (lu) lu.textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
      toast.show('Data refreshed', 'success', 2000);
    }
  } catch (e) {
    toast.show('Failed to refresh data', 'danger');
  } finally {
    loadingBar.complete();
    if (refreshIcon) refreshIcon.classList.remove('animate-spin');
  }
};

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
  // Cmd/Ctrl+Shift+R → refresh
  if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'R') {
    e.preventDefault();
    crmRefresh();
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
