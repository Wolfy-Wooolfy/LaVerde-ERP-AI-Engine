/**
 * ca_refunds_panel.js — Refunds detail panel controller (M3-S8).
 *
 * Public API: window.caRefundsPanel.open(triggerEl)
 *             window.caRefundsPanel.close()
 *
 * READ-ONLY: never POSTs or modifies Odoo data.
 * Endpoint: GET /api/v1/customer-accounts/refunds/detail
 * IDs prefix: ca-rd-*  (independent from ca-dd-* customer panel)
 *
 * No pagination — the refund set is small (7 records as of M3-S1).
 * The scrollable body handles any future growth gracefully.
 */
(function () {
  'use strict';

  var S = window.CA_RD_STRINGS || {};
  var F = window.CollectionsFormatters || {};

  // ── State ──────────────────────────────────────────────────────────────────
  var _state = {
    isLoading: false,
    triggerEl: null,
  };

  // ── DOM refs ───────────────────────────────────────────────────────────────
  var _panel, _backdrop, _closeBtn,
      _loadingSentinel, _summary, _total, _count,
      _listBody, _emptyMsg, _errorMsg;

  function _initRefs() {
    _panel           = document.getElementById('ca-rd-panel');
    _backdrop        = document.getElementById('ca-rd-backdrop');
    _closeBtn        = document.getElementById('ca-rd-close-btn');
    _loadingSentinel = document.getElementById('ca-rd-loading-sentinel');
    _summary         = document.getElementById('ca-rd-summary');
    _total           = document.getElementById('ca-rd-total');
    _count           = document.getElementById('ca-rd-count');
    _listBody        = document.getElementById('ca-rd-list-body');
    _emptyMsg        = document.getElementById('ca-rd-empty-msg');
    _errorMsg        = document.getElementById('ca-rd-error-msg');
    return !!_panel;
  }

  // ── Panel open ─────────────────────────────────────────────────────────────
  function open(triggerEl) {
    if (!_panel && !_initRefs()) {
      console.warn('[caRefundsPanel] panel DOM not ready');
      return;
    }

    _state.triggerEl = triggerEl || document.activeElement;
    _state.isLoading = false;

    // Reset panel content
    _summary.classList.add('hidden');
    _listBody.innerHTML = '';
    _hide(_emptyMsg);
    _hide(_errorMsg);
    _loadingSentinel.classList.remove('hidden');

    // Animate panel in
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
    _state.triggerEl = null;
  }

  // ── Fetch ──────────────────────────────────────────────────────────────────
  function _fetch() {
    if (_state.isLoading) return;
    _state.isLoading = true;

    window.crmApi.get('/api/v1/customer-accounts/refunds/detail').then(function (data) {
      _state.isLoading = false;
      _loadingSentinel.classList.add('hidden');
      _render(data);
    }).catch(function () {
      _state.isLoading = false;
      _loadingSentinel.classList.add('hidden');
      _errorMsg.innerHTML = ''
        + '<span>' + _esc(S.ca_rd_error_fetch || 'Failed to load. Please try again.') + '</span>'
        + ' <button type="button"'
        + ' class="underline ms-1 focus-visible:ring-2 focus-visible:ring-danger-500"'
        + ' data-ca-rd-retry="1">'
        + _esc(S.ca_rd_try_again || 'Try again')
        + '</button>';
      _errorMsg.classList.remove('hidden');
    });
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  function _render(data) {
    var lang  = S.lang || 'en';
    var items = data.items || [];

    // Summary bar
    var absTotal = Math.abs(data.total_amount || 0);
    _total.textContent = _fmtFull(absTotal, lang);
    var countLabel = S.ca_rd_count_label || 'records';
    _count.textContent = '(' + (data.record_count || 0) + ' ' + countLabel + ')';
    _summary.classList.remove('hidden');

    if (!items.length) {
      _emptyMsg.classList.remove('hidden');
      return;
    }

    var frag = document.createDocumentFragment();
    for (var i = 0; i < items.length; i++) {
      frag.appendChild(_makeRow(items[i], lang));
    }
    _listBody.appendChild(frag);
  }

  // ── Build one refund row ───────────────────────────────────────────────────
  function _makeRow(row, lang) {
    var li = document.createElement('li');
    li.className = 'px-5 py-4';

    var absAmt = Math.abs(row.amount);
    var amtStr = _fmtFull(absAmt, lang);

    li.innerHTML = ''
      + '<div class="flex items-start justify-between gap-3">'
        + '<div class="min-w-0 flex-1">'
          + '<p class="text-sm font-medium text-neutral-900 dark:text-neutral-100 truncate">'
            + _esc(row.customer_name || ('ID ' + row.customer_id))
          + '</p>'
          + '<p class="text-xs font-mono text-neutral-500 dark:text-neutral-400 mt-0.5">'
            + _esc(row.date || '—')
          + '</p>'
        + '</div>'
        + '<div class="shrink-0 text-end">'
          + '<p class="text-sm font-bold tabular text-warning-700 dark:text-warning-300">'
            + _esc(amtStr)
          + '</p>'
        + '</div>'
      + '</div>';

    return li;
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function _fmtFull(val, lang) {
    if (val == null) return '—';
    if (F.formatEGP) return F.formatEGP(val, lang, { fullValue: true });
    return Math.round(val).toLocaleString('en-EG') + ' EGP';
  }

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

  // ── Focus management ───────────────────────────────────────────────────────

  function _setMainInert(active) {
    var app = document.getElementById('app');
    if (!app) return;
    if (active) app.setAttribute('inert', '');
    else        app.removeAttribute('inert');
  }

  function _getFocusable() {
    if (!_panel) return [];
    var sel = 'button:not([disabled]), [tabindex]:not([tabindex="-1"]), a[href]';
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
      if (e.target.closest('[data-ca-rd-retry]')) {
        _hide(_errorMsg);
        _loadingSentinel.classList.remove('hidden');
        _fetch();
      }
    });

    _panel.addEventListener('keydown', _handleKeydown);
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.caRefundsPanel = {
    open:  open,
    close: close,
  };

  // Init
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
