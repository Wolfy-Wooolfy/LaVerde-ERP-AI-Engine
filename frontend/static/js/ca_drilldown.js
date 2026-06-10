/**
 * ca_drilldown.js — Customer Accounts drill-down panel controller (M3-S7).
 *
 * Public API: window.caCustomerPanel.open(partnerId, triggerEl)
 *             window.caCustomerPanel.close()
 *
 * READ-ONLY: never POSTs or modifies Odoo data.
 * Mirrors the Collections drilldown.js pattern (separate IDs: ca-dd-*).
 * Endpoint: GET /api/v1/customer-accounts/customer/{partner_id}
 *           ?cursor=...&page_size=50&sort_by=date&sort_dir=asc
 *
 * Number formatting: fullValue=true throughout (drill-down precision, matching
 * Collections drilldown row pattern).
 */
(function () {
  'use strict';

  var S = window.CA_DD_STRINGS || {};
  var F = window.CollectionsFormatters || {};

  // ── State ──────────────────────────────────────────────────────────────────
  var _state = {
    partnerId: null,
    cursor:    null,
    hasNext:   false,
    isLoading: false,
    triggerEl: null,
  };

  // ── DOM refs ───────────────────────────────────────────────────────────────
  var _panel, _backdrop, _title, _subtitle, _closeBtn,
      _loadingSentinel, _summary,
      _totalDue, _lateDue, _futureDue, _paidCash, _contractLine,
      _overpaidCard, _overpaidCredit, _qualityNote,
      _paymentRatio, _walletBalance, _instCount,
      _listBody, _emptyMsg, _errorMsg, _loadMoreBtn;

  function _initRefs() {
    _panel            = document.getElementById('ca-dd-panel');
    _backdrop         = document.getElementById('ca-dd-backdrop');
    _title            = document.getElementById('ca-dd-title');
    _subtitle         = document.getElementById('ca-dd-subtitle');
    _closeBtn         = document.getElementById('ca-dd-close-btn');
    _loadingSentinel  = document.getElementById('ca-dd-loading-sentinel');
    _summary          = document.getElementById('ca-dd-summary');
    _totalDue         = document.getElementById('ca-dd-total-due');
    _lateDue          = document.getElementById('ca-dd-late-due');
    _futureDue        = document.getElementById('ca-dd-future-due');
    _paidCash         = document.getElementById('ca-dd-paid-cash');
    _contractLine     = document.getElementById('ca-dd-contract-line');
    _overpaidCard     = document.getElementById('ca-dd-overpaid-card');
    _overpaidCredit   = document.getElementById('ca-dd-overpaid-credit');
    _qualityNote      = document.getElementById('ca-dd-quality-note');
    _paymentRatio     = document.getElementById('ca-dd-payment-ratio');
    _walletBalance    = document.getElementById('ca-dd-wallet-balance');
    _instCount        = document.getElementById('ca-dd-inst-count');
    _listBody         = document.getElementById('ca-dd-list-body');
    _emptyMsg         = document.getElementById('ca-dd-empty-msg');
    _errorMsg         = document.getElementById('ca-dd-error-msg');
    _loadMoreBtn      = document.getElementById('ca-dd-load-more-btn');
    return !!_panel;
  }

  // ── Panel open ─────────────────────────────────────────────────────────────
  function open(partnerId, triggerEl) {
    if (!_panel && !_initRefs()) {
      console.warn('[caCustomerPanel] panel DOM not ready');
      return;
    }

    _state.partnerId = partnerId;
    _state.cursor    = null;
    _state.hasNext   = false;
    _state.isLoading = false;
    _state.triggerEl = triggerEl || document.activeElement;

    // Reset panel content
    _title.textContent     = '—';
    _subtitle.textContent  = '';
    _subtitle.classList.add('hidden');
    _summary.classList.add('hidden');
    _listBody.innerHTML    = '';
    _hide(_emptyMsg);
    _hide(_errorMsg);
    _hide(_loadMoreBtn);
    _hide(_overpaidCard);
    _hide(_qualityNote);
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

    _fetchPage();
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
    _state.partnerId = null;
    _state.triggerEl = null;
  }

  // ── URL builder ────────────────────────────────────────────────────────────
  function _buildUrl() {
    var base = '/api/v1/customer-accounts/customer/' + _state.partnerId;
    var params = new URLSearchParams({ page_size: 50, sort_by: 'date', sort_dir: 'asc' });
    if (_state.cursor) params.set('cursor', _state.cursor);
    return base + '?' + params.toString();
  }

  // ── Fetch page ─────────────────────────────────────────────────────────────
  function _fetchPage() {
    if (_state.isLoading) return;
    _state.isLoading = true;
    _loadingSentinel.classList.remove('hidden');
    _hide(_loadMoreBtn);

    var isFirst = (_state.cursor === null);

    window.crmApi.get(_buildUrl()).then(function (resp) {
      _state.isLoading = false;
      _loadingSentinel.classList.add('hidden');

      var data = resp.data;
      var meta = resp.meta;

      if (isFirst) {
        _renderSummary(data.header, data.exposure, data.behavior, meta);
        _summary.classList.remove('hidden');
      }

      _renderInstallments(data.installments, meta);

    }).catch(function () {
      _state.isLoading = false;
      _loadingSentinel.classList.add('hidden');
      _errorMsg.innerHTML = ''
        + '<span>' + _esc(S.ca_dd_error_fetch || 'Failed to load. Please try again.') + '</span>'
        + ' <button type="button" class="underline ms-1 focus-visible:ring-2 focus-visible:ring-danger-500"'
        + ' data-ca-dd-retry="1">' + _esc(S.ca_dd_try_again || 'Try again') + '</button>';
      _errorMsg.classList.remove('hidden');
    });
  }

  // ── Render summary (header + exposure + behavior) ─────────────────────────
  function _renderSummary(header, exposure, behavior, meta) {
    var lang = S.lang || 'en';

    // Header
    _title.textContent = header.customer_name || ('ID ' + header.partner_id);

    // Subtitle: customer ID
    var idLabel = S.ca_dd_customer_id_label || 'Customer ID';
    _subtitle.textContent = idLabel + ': ' + header.partner_id;
    _subtitle.classList.remove('hidden');

    // Exposure — 4 numbers, all fullValue
    _totalDue.textContent  = _fmtFull(exposure.total_due_egp,  lang);
    _lateDue.textContent   = _fmtFull(exposure.late_due_egp,   lang);
    _futureDue.textContent = _fmtFull(exposure.future_due_egp, lang);
    _paidCash.textContent  = _fmtFull(exposure.paid_cash_egp,  lang);

    // Overpaid credit (رصيد مدفوع بالزيادة) — only when > 0 (Decision 18.2)
    if (exposure.overpaid_credit_egp > 0) {
      _overpaidCredit.textContent = _fmtFull(exposure.overpaid_credit_egp, lang);
      _overpaidCard.classList.remove('hidden');
    } else {
      _hide(_overpaidCard);
    }

    // Contract total secondary line
    var contractLabel = S.ca_dd_total_original || 'Contract Total';
    _contractLine.textContent = contractLabel + ': '
      + _fmtFull(exposure.total_original_egp, lang)
      + ' · ' + (exposure.total_installments || 0) + ' '
      + (S.ca_installments || 'installments');

    // Non-blocking data-quality note (meta.data_quality_warning, Decision 18.2)
    if (meta && meta.data_quality_warning) {
      _qualityNote.textContent = (S.ca_dd_quality_note || 'Data quality note')
        + ' (' + meta.data_quality_warning + ')';
      _qualityNote.classList.remove('hidden');
    } else {
      _hide(_qualityNote);
    }

    // Behavior
    _paymentRatio.textContent  = (behavior.payment_ratio_pct != null)
      ? _fmtPct(behavior.payment_ratio_pct, lang)
      : '—';
    _walletBalance.textContent = _fmtFull(behavior.wallet_balance_egp, lang);

    // Installments count header
    var unpaid = exposure.unpaid_installment_count || 0;
    _instCount.textContent = unpaid
      ? (S.ca_installments || 'installments') + ': ' + unpaid
      : '';
  }

  // ── Render installment rows ────────────────────────────────────────────────
  function _renderInstallments(inst, meta) {
    var items    = (inst && inst.items)    || [];
    var isFirst  = _listBody.children.length === 0;

    if (!items.length) {
      if (isFirst) _emptyMsg.classList.remove('hidden');
      return;
    }

    var lang = S.lang || 'en';
    var frag = document.createDocumentFragment();
    for (var i = 0; i < items.length; i++) {
      frag.appendChild(_makeRow(items[i], lang));
    }
    _listBody.appendChild(frag);

    // Update cursor + load-more
    _state.cursor  = inst.cursor_next || null;
    _state.hasNext = inst.has_next    || false;

    if (_state.hasNext) {
      var shown = _listBody.children.length;
      var total = inst.total_count || 0;
      _loadMoreBtn.textContent = (S.ca_dd_load_more || 'Load more')
        + (total ? ' (' + shown + ' / ' + total + ')' : '');
      _loadMoreBtn.classList.remove('hidden');
    }
  }

  // ── Build one installment row ──────────────────────────────────────────────
  function _makeRow(row, lang) {
    var li = document.createElement('li');
    li.className = 'ca-dd-row px-5 py-4';

    var amtStr = _fmtFull(row.amount,     lang);
    var dueStr = _fmtFull(row.due_amount, lang);

    var timingLabel = row.timing === 'late'
      ? (S.ca_dd_timing_late   || 'Late')
      : (S.ca_dd_timing_future || 'Future');
    var timingClass = row.timing === 'late'
      ? 'ca-dd-badge ca-dd-badge--late'
      : 'ca-dd-badge ca-dd-badge--future';

    var stateLabel = _stateLabel(row.payment_state);
    var stateClass = 'ca-dd-badge ca-dd-badge--' + (row.payment_state || 'unpaid');

    var typeNote = row.installment_type_name_ar
      ? '<span class="inline-block mt-1 px-1.5 py-0.5 rounded text-[10px]'
          + ' bg-neutral-100 dark:bg-neutral-800'
          + ' text-neutral-500 dark:text-neutral-400" dir="rtl">'
          + _esc(row.installment_type_name_ar)
        + '</span>'
      : '';

    li.innerHTML = ''
      + '<div class="flex items-start justify-between gap-3">'
        + '<div class="min-w-0 flex-1">'
          + '<p class="text-xs font-mono text-neutral-500 dark:text-neutral-400">'
            + _esc(row.date)
          + '</p>'
          + typeNote
        + '</div>'
        + '<div class="shrink-0 text-end space-y-1">'
          + '<p class="text-sm font-semibold tabular text-neutral-900 dark:text-neutral-100">'
            + _esc(amtStr)
          + '</p>'
          + '<p class="text-xs text-neutral-500 dark:text-neutral-400 tabular">'
            + _esc(S.ca_dd_due || 'Due') + ': '
            + '<span class="font-medium text-neutral-700 dark:text-neutral-300">'
              + _esc(dueStr)
            + '</span>'
          + '</p>'
          + '<div class="flex items-center gap-1 justify-end">'
            + '<span class="' + timingClass + '">' + _esc(timingLabel) + '</span>'
            + '<span class="' + stateClass  + '">' + _esc(stateLabel)  + '</span>'
          + '</div>'
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

  function _fmtPct(val, lang) {
    if (val == null) return '—';
    var locale = lang === 'ar' ? 'ar-EG' : 'en-EG';
    return val.toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '%';
  }

  function _stateLabel(state) {
    var map = {
      unpaid:  S.ca_dd_state_unpaid  || 'Unpaid',
      partial: S.ca_dd_state_partial || 'Partial',
    };
    return map[state] || state || '';
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

  // ── Focus management (mirrors Collections drilldown D7) ───────────────────

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

    _loadMoreBtn.addEventListener('click', function () {
      if (_state.hasNext && !_state.isLoading) _fetchPage();
    });

    _errorMsg.addEventListener('click', function (e) {
      if (e.target.closest('[data-ca-dd-retry]')) {
        _hide(_errorMsg);
        _loadingSentinel.classList.remove('hidden');
        _fetchPage();
      }
    });

    _panel.addEventListener('keydown', _handleKeydown);
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.caCustomerPanel = {
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
