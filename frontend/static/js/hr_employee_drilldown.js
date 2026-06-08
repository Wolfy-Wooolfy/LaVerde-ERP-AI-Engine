/**
 * hr_employee_drilldown.js — HR employee profile slide-over controller (F3).
 *
 * Public API: window.hrEmployeePanel.open(employeeId, triggerEl)
 *             window.hrEmployeePanel.close()
 *
 * READ-ONLY: never POSTs or modifies Odoo data.
 * Endpoint: GET /api/v1/hr/employee/{employee_id}
 *           Requires HTTP Basic auth — MUST use credentials: 'same-origin'.
 *           The /hr/dashboard page is behind the same Basic-auth realm, so the
 *           browser has the credentials cached and a same-origin fetch carries them.
 *           A 401 is surfaced as a readable, recoverable error state.
 *
 * Z-layer: backdrop z-[55] (over dept panel z-50), panel z-[60].
 * Focus:   does NOT touch #app inert — hrDeptPanel owns it.
 *          Own focus-trap (Tab cycle within this panel) isolates keyboard focus.
 *
 * i18n: all user-facing strings read from window.HR_STRINGS (injected server-side
 *       by hr/dashboard.html). English fallbacks keep the panel functional if the
 *       object is absent (e.g., when loaded standalone in tests).
 */
(function () {
  'use strict';

  // ── i18n helper ────────────────────────────────────────────────────────────
  var S = window.HR_STRINGS || {};
  function _s(key, fallback) {
    return (S[key] != null) ? S[key] : fallback;
  }

  // ── State ──────────────────────────────────────────────────────────────────
  var _state = {
    employeeId: null,
    isLoading:  false,
    triggerEl:  null,
  };

  // ── DOM refs ───────────────────────────────────────────────────────────────
  var _panel, _backdrop, _title, _subtitle, _backBtn, _closeBtn,
      _loadingSentinel, _content, _detailRows, _errorMsg;

  function _initRefs() {
    _panel           = document.getElementById('hr-pf-panel');
    _backdrop        = document.getElementById('hr-pf-backdrop');
    _title           = document.getElementById('hr-pf-title');
    _subtitle        = document.getElementById('hr-pf-subtitle');
    _backBtn         = document.getElementById('hr-pf-back-btn');
    _closeBtn        = document.getElementById('hr-pf-close-btn');
    _loadingSentinel = document.getElementById('hr-pf-loading-sentinel');
    _content         = document.getElementById('hr-pf-content');
    _detailRows      = document.getElementById('hr-pf-detail-rows');
    _errorMsg        = document.getElementById('hr-pf-error-msg');
    return !!_panel;
  }

  // ── Panel open ─────────────────────────────────────────────────────────────
  function open(employeeId, triggerEl) {
    if (!_panel && !_initRefs()) {
      console.warn('[hrEmployeePanel] panel DOM not ready');
      return;
    }

    _state.employeeId = employeeId;
    _state.isLoading  = false;
    _state.triggerEl  = triggerEl || document.activeElement;

    // Reset panel content
    _title.textContent = '—';
    _subtitle.textContent = '';
    _subtitle.classList.add('hidden');
    _content.classList.add('hidden');
    _detailRows.innerHTML = '';
    _hide(_errorMsg);
    _loadingSentinel.classList.remove('hidden');

    // Animate panel in (RTL-aware — mirrors hr_drilldown.js exactly)
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
    // Do NOT touch #app inert — hrDeptPanel owns it and must keep it while open underneath.
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

    // Do NOT touch #app inert — hrDeptPanel owns it.
    if (_state.triggerEl && _state.triggerEl.focus) {
      _state.triggerEl.focus();
    }
    _state.employeeId = null;
    _state.triggerEl  = null;
  }

  // ── Authenticated fetch ────────────────────────────────────────────────────
  function _fetch() {
    if (_state.isLoading || !_state.employeeId) return;
    _state.isLoading = true;

    // credentials: 'same-origin' is explicit and required.
    // This endpoint uses HTTP Basic auth (Depends(get_current_user)).
    // The /hr/dashboard page is behind the same realm, so the browser has
    // cached Basic credentials for this origin and will include them here.
    fetch('/api/v1/hr/employee/' + _state.employeeId, { credentials: 'same-origin' })
      .then(function (resp) {
        _state.isLoading = false;

        if (resp.status === 401) {
          _showError(_s('err_session', 'Session expired — please refresh the page.'));
          return null;
        }
        if (resp.status === 404) {
          _showError(_s('err_profile_not_found', 'Employee profile not found.'));
          return null;
        }
        if (resp.status === 503) {
          _showError(_s('err_service', 'HR service unavailable — try again shortly.'));
          return null;
        }
        if (!resp.ok) {
          _showError(_s('err_load_employee', 'Error loading employee (HTTP ') + resp.status + ').');
          return null;
        }

        return resp.json();
      })
      .then(function (data) {
        if (!data) return;
        // Stale-response guard: discard if employee changed while request was in flight.
        if (data.employee_id !== _state.employeeId) return;
        _render(data);
      })
      .catch(function () {
        _state.isLoading = false;
        _showError(_s('err_network', 'Network error — check your connection and try again.'));
      });
  }

  // ── Render employee profile ────────────────────────────────────────────────
  function _render(data) {
    // textContent assignments: raw values are safe (textContent is not HTML-parsed).
    // Never pass through _esc here — that would double-escape (& → &amp;).
    _title.textContent = data.name;

    // Subtitle: "{job_title} · {dept leaf}" — guard department_name before split.
    var jobTitle = data.job_title || '';
    var leaf = data.department_name ? data.department_name.split('/').pop().trim() : '';
    var subtitleParts = [];
    if (jobTitle) subtitleParts.push(jobTitle);
    if (leaf)     subtitleParts.push(leaf);
    var subtitleText = subtitleParts.join(' · ');
    if (subtitleText) {
      _subtitle.textContent = subtitleText;
      _subtitle.classList.remove('hidden');
    }

    // Detail rows: _esc all server-supplied strings before innerHTML insertion.
    var rows = '';

    // Reports to — omit row entirely if null
    if (data.manager_name != null) {
      rows += _detailRow(_s('reports_to', 'Reports to'), _esc(data.manager_name));
    }

    // Hired — omit row if hire_date null; guard tenure_years before toFixed
    if (data.hire_date != null) {
      var tenurePart = (data.tenure_years != null)
        ? ' &middot; ' + data.tenure_years.toFixed(1) + _s('yrs_since_hire', ' yrs · since hire')
        : '';
      rows += _detailRow(_s('hired', 'Hired'), _esc(_fmtDate(data.hire_date)) + tenurePart);
    }

    // Contract — neutral framing; always shown (contract_status always "Running").
    // is_open_ended → localized "Open-ended"; otherwise "Through {date}". No countdown, no urgency.
    var contractVal = data.is_open_ended
      ? _esc(_s('open_ended', 'Open-ended'))
      : _esc(_s('through', 'Through ')) + _esc(_fmtDate(data.contract_end));
    rows += _detailRow(_s('contract', 'Contract'), contractVal);

    // Location — omit row entirely if null
    if (data.location != null) {
      rows += _detailRow(_s('location', 'Location'), _esc(data.location));
    }

    _detailRows.innerHTML = rows;

    // Show content, hide loading
    _loadingSentinel.classList.add('hidden');
    _content.classList.remove('hidden');
  }

  // ── Build one detail row (label + value HTML) ──────────────────────────────
  function _detailRow(label, valueHtml) {
    return '<div>'
      + '<p class="text-[10px] font-semibold uppercase tracking-wide'
      + ' text-neutral-400 dark:text-neutral-500">'
      + _esc(label)
      + '</p>'
      + '<p class="text-sm text-neutral-900 dark:text-neutral-100 mt-0.5">'
      + valueHtml
      + '</p>'
      + '</div>';
  }

  // ── Show error state ───────────────────────────────────────────────────────
  function _showError(msg) {
    _loadingSentinel.classList.add('hidden');
    _content.classList.add('hidden');
    _errorMsg.innerHTML = _esc(msg)
      + ' <button type="button"'
      + ' class="underline ms-1 focus-visible:ring-2 focus-visible:ring-danger-500"'
      + ' data-hr-pf-retry="1">' + _esc(_s('try_again', 'Try again')) + '</button>';
    _errorMsg.classList.remove('hidden');
  }

  // ── Date formatter — manual parse; no Date constructor to avoid TZ shift ──
  // ISO "YYYY-MM-DD" → locale-aware "30 Jun 2026" (EN) / "30 يونيو 2026" (AR)
  function _fmtDate(iso) {
    if (!iso) return '—';
    var months = (S.months && S.months.length === 12)
      ? S.months
      : ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
         'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var parts = iso.split('-');
    return parseInt(parts[2], 10) + ' ' + months[parseInt(parts[1], 10) - 1] + ' ' + parts[0];
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

  // ── Focus management (mirrors hr_drilldown.js exactly) ────────────────────

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

    _backBtn.addEventListener('click', close);
    _closeBtn.addEventListener('click', close);
    _backdrop.addEventListener('click', close);

    _errorMsg.addEventListener('click', function (e) {
      if (e.target.closest('[data-hr-pf-retry]')) {
        _hide(_errorMsg);
        _loadingSentinel.classList.remove('hidden');
        _state.isLoading = false;
        _fetch();
      }
    });

    _panel.addEventListener('keydown', _handleKeydown);
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.hrEmployeePanel = {
    open:  open,
    close: close,
  };

  // Init on DOMContentLoaded (mirrors hr_drilldown.js)
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
