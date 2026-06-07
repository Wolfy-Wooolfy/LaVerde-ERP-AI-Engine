/**
 * hr_drilldown.js — HR department staff drill-down panel controller (F2).
 *
 * Public API: window.hrDeptPanel.open(deptId, triggerEl)
 *             window.hrDeptPanel.close()
 *
 * READ-ONLY: never POSTs or modifies Odoo data.
 * Endpoint: GET /api/v1/hr/department/{department_id}
 *           Requires HTTP Basic auth — MUST use credentials: 'same-origin'.
 *           The /hr/dashboard page is behind the same Basic-auth realm, so the
 *           browser has the credentials cached and a same-origin fetch carries them.
 *           A 401 is surfaced as a readable, recoverable error state.
 */
(function () {
  'use strict';

  // ── State ──────────────────────────────────────────────────────────────────
  var _state = {
    deptId:    null,
    isLoading: false,
    triggerEl: null,
  };

  // ── DOM refs ───────────────────────────────────────────────────────────────
  var _panel, _backdrop, _title, _subtitle, _closeBtn,
      _loadingSentinel, _summary,
      _statHeadcount, _statTotalCost, _statPctPayroll, _statAvgHead,
      _listBody, _errorMsg;

  function _initRefs() {
    _panel           = document.getElementById('hr-dd-panel');
    _backdrop        = document.getElementById('hr-dd-backdrop');
    _title           = document.getElementById('hr-dd-title');
    _subtitle        = document.getElementById('hr-dd-subtitle');
    _closeBtn        = document.getElementById('hr-dd-close-btn');
    _loadingSentinel = document.getElementById('hr-dd-loading-sentinel');
    _summary         = document.getElementById('hr-dd-summary');
    _statHeadcount   = document.getElementById('hr-dd-stat-headcount');
    _statTotalCost   = document.getElementById('hr-dd-stat-total-cost');
    _statPctPayroll  = document.getElementById('hr-dd-stat-pct-payroll');
    _statAvgHead     = document.getElementById('hr-dd-stat-avg-head');
    _listBody        = document.getElementById('hr-dd-list-body');
    _errorMsg        = document.getElementById('hr-dd-error-msg');
    return !!_panel;
  }

  // ── Panel open ─────────────────────────────────────────────────────────────
  function open(deptId, triggerEl) {
    if (!_panel && !_initRefs()) {
      console.warn('[hrDeptPanel] panel DOM not ready');
      return;
    }

    _state.deptId    = deptId;
    _state.isLoading = false;
    _state.triggerEl = triggerEl || document.activeElement;

    // Reset panel content
    _title.textContent = '—';
    _subtitle.textContent = '';
    _subtitle.classList.add('hidden');
    _summary.classList.add('hidden');
    _listBody.innerHTML = '';
    _hide(_errorMsg);
    _loadingSentinel.classList.remove('hidden');

    // Animate panel in (RTL-aware — mirrors ca_drilldown.js exactly)
    _backdrop.classList.remove('hidden');
    _panel.removeAttribute('hidden');
    var rtl = document.documentElement.dir === 'rtl';
    _panel.classList.add(rtl ? '-translate-x-full' : 'translate-x-full');
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        _panel.classList.remove('-translate-x-full', 'translate-x-full');
        _panel.classList.add('translate-x-0');
      });
    });
    _setMainInert(true);
    _panel.focus();

    _fetch();
  }

  // ── Panel close ────────────────────────────────────────────────────────────
  function close() {
    if (!_panel) return;
    var rtl = document.documentElement.dir === 'rtl';
    _panel.classList.remove('translate-x-0');
    _panel.classList.add(rtl ? '-translate-x-full' : 'translate-x-full');

    function _onEnd() {
      _panel.removeEventListener('transitionend', _onEnd);
      _panel.setAttribute('hidden', '');
      _backdrop.classList.add('hidden');
      _panel.classList.remove('-translate-x-full', 'translate-x-full');
    }
    _panel.addEventListener('transitionend', _onEnd);

    _setMainInert(false);
    if (_state.triggerEl && _state.triggerEl.focus) {
      _state.triggerEl.focus();
    }
    _state.deptId    = null;
    _state.triggerEl = null;
  }

  // ── Authenticated fetch ────────────────────────────────────────────────────
  function _fetch() {
    if (_state.isLoading || !_state.deptId) return;
    _state.isLoading = true;

    // credentials: 'same-origin' is explicit and required.
    // This endpoint uses HTTP Basic auth (Depends(get_current_user)).
    // The /hr/dashboard page is behind the same realm, so the browser has
    // cached Basic credentials for this origin and will include them here.
    fetch('/api/v1/hr/department/' + _state.deptId, { credentials: 'same-origin' })
      .then(function (resp) {
        _state.isLoading = false;

        if (resp.status === 401) {
          _showError('Session expired — please refresh the page.');
          return null;
        }
        if (resp.status === 404) {
          _showError('No active staff found for this department.');
          return null;
        }
        if (resp.status === 503) {
          _showError('HR service unavailable — try again shortly.');
          return null;
        }
        if (!resp.ok) {
          _showError('Error loading department (HTTP ' + resp.status + ').');
          return null;
        }

        return resp.json();
      })
      .then(function (data) {
        if (!data) return;
        // Stale-response guard: user may have opened a different dept while
        // this request was in flight; discard if dept no longer matches.
        if (data.department_id !== _state.deptId) return;
        _render(data);
      })
      .catch(function () {
        _state.isLoading = false;
        _showError('Network error — check your connection and try again.');
      });
  }

  // ── Render department data ─────────────────────────────────────────────────
  function _render(data) {
    // Header: leaf name (drop parent path segments, same transform as F1)
    var leaf = data.department_name.split('/').pop().trim();
    _title.textContent = leaf;
    _subtitle.textContent = data.headcount + ' staff · Running contracts';
    _subtitle.classList.remove('hidden');

    // Stat tiles — null-guard each: render '—' if value is null
    _statHeadcount.textContent  = data.headcount;
    _statTotalCost.textContent  = _fmtEGP(data.total_wage);
    _statPctPayroll.textContent = _fmtPct(data.pct_of_total_payroll);
    _statAvgHead.textContent    = _fmtEGP(data.avg_cost_per_head);

    // Staff rows
    var staff = data.staff || [];
    var frag  = document.createDocumentFragment();
    for (var i = 0; i < staff.length; i++) {
      frag.appendChild(_makeRow(staff[i]));
    }
    _listBody.appendChild(frag);

    // Show summary, hide loading
    _loadingSentinel.classList.add('hidden');
    _summary.classList.remove('hidden');
  }

  // ── Build one staff row ────────────────────────────────────────────────────
  function _makeRow(member) {
    var li = document.createElement('li');
    li.className = 'px-5 py-3.5 hover:bg-neutral-50 dark:hover:bg-neutral-800/60 transition-colors duration-150';

    // tenure_years is null when date_start is null (backend computes it)
    var tenureStr = (member.tenure_years != null)
      ? member.tenure_years.toFixed(1) + ' yrs · since hire'
      : '— · no start date';

    // No per-employee wage: only name, job, tenure, and Running state badge.
    li.innerHTML = ''
      + '<div class="flex items-start justify-between gap-3">'
        + '<div class="min-w-0 flex-1">'
          + '<p class="text-sm font-medium text-neutral-900 dark:text-neutral-100 truncate">'
            + _esc(member.employee_name)
          + '</p>'
          + '<p class="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5 truncate">'
            + _esc(member.job_title)
          + '</p>'
          + '<p class="text-xs font-mono text-neutral-400 dark:text-neutral-500 mt-0.5">'
            + _esc(tenureStr)
          + '</p>'
        + '</div>'
        + '<div class="shrink-0 pt-0.5">'
          + '<span class="badge badge-success">Running</span>'
        + '</div>'
      + '</div>';

    return li;
  }

  // ── Show error state ───────────────────────────────────────────────────────
  function _showError(msg) {
    _loadingSentinel.classList.add('hidden');
    _summary.classList.add('hidden');
    _errorMsg.innerHTML = _esc(msg)
      + ' <button type="button"'
      + ' class="underline ms-1 focus-visible:ring-2 focus-visible:ring-danger-500"'
      + ' data-hr-dd-retry="1">Try again</button>';
    _errorMsg.classList.remove('hidden');
  }

  // ── Number formatters ──────────────────────────────────────────────────────
  function _fmtEGP(val) {
    if (val == null) return '—';
    return Math.round(val).toLocaleString('en-EG') + ' EGP';
  }

  function _fmtPct(val) {
    if (val == null) return '—';
    return val.toFixed(1) + '%';
  }

  // ── Escape HTML to prevent XSS in server-supplied strings ─────────────────
  function _esc(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _hide(el) {
    if (el) el.classList.add('hidden');
  }

  // ── Focus management (mirrors ca_drilldown.js exactly) ────────────────────

  // Sets #app inert to prevent focus reaching content behind the panel.
  // #app is the root application div in base.html (id="app").
  function _setMainInert(active) {
    var app = document.getElementById('app');
    if (!app) return;
    if (active) app.setAttribute('inert', '');
    else        app.removeAttribute('inert');
  }

  function _getFocusable() {
    if (!_panel) return [];
    var sel = 'button:not([disabled]), [tabindex]:not([tabindex="-1"]), a[href], input, select, textarea';
    return Array.prototype.filter.call(
      _panel.querySelectorAll(sel),
      function (el) {
        return !el.closest('[hidden]') && getComputedStyle(el).display !== 'none';
      }
    );
  }

  function _handleKeydown(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
      return;
    }
    if (e.key !== 'Tab') return;
    var focusable = _getFocusable();
    if (!focusable.length) return;
    var first = focusable[0];
    var last  = focusable[focusable.length - 1];
    if (e.shiftKey) {
      if (document.activeElement === first) { e.preventDefault(); last.focus(); }
    } else {
      if (document.activeElement === last)  { e.preventDefault(); first.focus(); }
    }
  }

  // ── Event wiring ───────────────────────────────────────────────────────────
  function _wire() {
    if (!_panel) return;

    _closeBtn.addEventListener('click', close);
    _backdrop.addEventListener('click', close);

    _errorMsg.addEventListener('click', function (e) {
      if (e.target.closest('[data-hr-dd-retry]')) {
        _hide(_errorMsg);
        _loadingSentinel.classList.remove('hidden');
        _state.isLoading = false;
        _fetch();
      }
    });

    _panel.addEventListener('keydown', _handleKeydown);
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.hrDeptPanel = {
    open:  open,
    close: close,
  };

  // Init on DOMContentLoaded (mirrors ca_drilldown.js)
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      _initRefs();
      _wire();
    });
  } else {
    _initRefs();
    _wire();
  }

}());
