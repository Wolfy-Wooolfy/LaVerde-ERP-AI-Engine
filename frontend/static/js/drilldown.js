/**
 * drilldown.js — Drill-down side panel controller (Stage 6).
 *
 * Public API: window.drilldownController.open(target, presetFilters, triggerEl)
 *             window.drilldownController.close()
 *             window.drilldownController.state  (read-only snapshot)
 *
 * READ-ONLY: never POSTs or modifies Odoo data.
 * D2 — core open/close/fetch/render (no filter UI, no hash, no keyboard nav).
 */
(function () {
  'use strict';

  var S = window.COLLECTIONS_STRINGS || {};
  var F = window.CollectionsFormatters || {};

  // ── State ──────────────────────────────────────────────────────────────────
  var _state = {
    target:    null,
    endpoint:  null,
    filters:   {},     // {payment_state, has_pending_cheque, sort_by, sort_dir, project_id}
    cursor:    null,
    hasNext:   false,
    isLoading: false,
    triggerEl: null,
  };

  // ── DOM refs ───────────────────────────────────────────────────────────────
  var _panel, _backdrop, _panelInner, _title, _filterBar,
      _listBody, _emptyMsg, _errorMsg,
      _loadMoreBtn, _loadingSentinel, _dataQualityNote, _closeBtn;

  function _initRefs() {
    _panel            = document.getElementById('dd-panel');
    _backdrop         = document.getElementById('dd-backdrop');
    _panelInner       = document.getElementById('dd-panel-inner');
    _title            = document.getElementById('dd-title');
    _filterBar        = document.getElementById('dd-filter-bar');
    _listBody         = document.getElementById('dd-list-body');
    _emptyMsg         = document.getElementById('dd-empty-msg');
    _errorMsg         = document.getElementById('dd-error-msg');
    _loadMoreBtn      = document.getElementById('dd-load-more-btn');
    _loadingSentinel  = document.getElementById('dd-loading-sentinel');
    _dataQualityNote  = document.getElementById('dd-data-quality-note');
    _closeBtn         = document.getElementById('dd-close-btn');
    return !!_panel;
  }

  // ── Endpoint resolver ──────────────────────────────────────────────────────
  function _resolveEndpoint(target) {
    if (!target) return null;
    var base = '/api/v1/collections/drilldown/';
    if (target === 'kpi1')              return base + 'portfolio';
    if (target === 'kpi2')              return base + 'late';
    if (target === 'kpi2-cheques')      return base + 'late';
    if (target === 'forecast-this_month')   return base + 'forecast/month';
    if (target === 'forecast-this_quarter') return base + 'forecast/quarter';
    if (target === 'forecast-this_half')    return base + 'forecast/half';
    if (target === 'forecast-this_year')    return base + 'forecast/year';
    if (target.indexOf('kpi5-proj-') === 0) {
      var pid = target.slice('kpi5-proj-'.length);
      return base + 'project/' + pid;
    }
    if (target.indexOf('trend-') === 0) {
      var month = target.slice('trend-'.length);
      return base + 'trend/' + month;
    }
    return null;
  }

  // ── Title resolver ─────────────────────────────────────────────────────────
  function _resolveTitle(target) {
    if (target === 'kpi1')         return S.dd_title_portfolio || 'Portfolio — Customer Breakdown';
    if (target === 'kpi2')         return S.dd_title_late      || 'Late Uncollected — Detail';
    if (target === 'kpi2-cheques') return S.dd_title_cheques   || 'Late — Received Cheques';
    if (target.indexOf('forecast-') === 0) {
      var bucketLabel = {
        'forecast-this_month':   S.this_month   || 'This Month',
        'forecast-this_quarter': S.this_quarter || 'This Quarter',
        'forecast-this_half':    S.this_half    || 'This Half',
        'forecast-this_year':    S.this_year    || 'This Year',
      }[target] || target;
      return (S.dd_title_forecast || 'Expected Collections') + ' — ' + bucketLabel;
    }
    if (target.indexOf('kpi5-proj-') === 0) {
      return S.dd_title_project || 'Late Detail';
    }
    if (target.indexOf('trend-') === 0) {
      var m = target.slice('trend-'.length);
      return (S.dd_title_trend || 'Trend') + ' — ' + m;
    }
    return target;
  }

  // ── Preset filter resolver ─────────────────────────────────────────────────
  function _resolvePresets(target) {
    if (target === 'kpi2-cheques') return { has_pending_cheque: true };
    return {};
  }

  // ── Panel open ─────────────────────────────────────────────────────────────
  function open(target, presetFilters, triggerEl) {
    if (!_panel && !_initRefs()) {
      console.warn('[drilldown] panel DOM not ready');
      return;
    }
    var endpoint = _resolveEndpoint(target);
    if (!endpoint) {
      console.warn('[drilldown] no endpoint for target:', target);
      return;
    }

    _state.target    = target;
    _state.endpoint  = endpoint;
    _state.cursor    = null;
    _state.hasNext   = false;
    _state.isLoading = false;
    _state.triggerEl = triggerEl || document.activeElement;
    _state.filters   = Object.assign({}, _resolvePresets(target), presetFilters || {});

    _title.textContent = _resolveTitle(target);

    _listBody.innerHTML = '';
    _hide(_emptyMsg);
    _hide(_errorMsg);
    _hide(_loadMoreBtn);
    _hide(_dataQualityNote);
    _loadingSentinel.classList.remove('hidden');

    // Show backdrop + animate panel in
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

    if (_state.triggerEl && _state.triggerEl.focus) {
      _state.triggerEl.focus();
    }
    _state.target    = null;
    _state.triggerEl = null;
  }

  // ── URL builder ────────────────────────────────────────────────────────────
  function _buildUrl() {
    var params = new URLSearchParams({ page_size: 25 });
    if (_state.cursor) params.set('cursor', _state.cursor);

    var f = _state.filters;
    var isPortfolio = _state.target === 'kpi1';

    if (!isPortfolio) {
      // Late / forecast / project / trend support these params
      if (f.payment_state && f.payment_state !== 'all') {
        params.set('payment_state', f.payment_state);
      }
      if (f.has_pending_cheque) {
        params.set('has_pending_cheque', 'true');
      }
      if (f.sort_by)  params.set('sort_by',  f.sort_by);
      if (f.sort_dir) params.set('sort_dir', f.sort_dir);
    } else {
      // Portfolio supports only project_id filter
      if (f.project_id) params.set('project_id', String(f.project_id));
    }

    return _state.endpoint + '?' + params.toString();
  }

  // ── Fetch page ─────────────────────────────────────────────────────────────
  function _fetchPage() {
    if (_state.isLoading) return;
    _state.isLoading = true;
    _loadingSentinel.classList.remove('hidden');
    _hide(_loadMoreBtn);

    var url = _buildUrl();
    window.crmApi.get(url).then(function (envelope) {
      _state.isLoading = false;
      _loadingSentinel.classList.add('hidden');

      var meta = envelope.meta;
      _state.cursor  = meta.cursor_next || null;
      _state.hasNext = meta.has_next    || false;

      _renderData(envelope);

      if (_state.hasNext) {
        _loadMoreBtn.classList.remove('hidden');
      }
      if (meta.data_quality) {
        _dataQualityNote.textContent = S.dd_dq_unassigned || 'Some records have no project assigned.';
        _dataQualityNote.classList.remove('hidden');
      }
    }).catch(function (err) {
      _state.isLoading = false;
      _loadingSentinel.classList.add('hidden');
      _errorMsg.textContent = (S.dd_error_fetch || 'Failed to load. Please try again.');
      _errorMsg.classList.remove('hidden');
    });
  }

  // ── Render dispatch ────────────────────────────────────────────────────────
  function _renderData(envelope) {
    if (_state.target === 'kpi1') {
      _renderPortfolio(envelope.data);
    } else {
      _renderInstallments(envelope.data);
    }
  }

  // ── Installment row renderer ───────────────────────────────────────────────
  function _renderInstallments(data) {
    var items = (data && data.items) || [];
    var isFirstPage = _listBody.children.length === 0;

    if (!items.length) {
      if (isFirstPage) _emptyMsg.classList.remove('hidden');
      return;
    }

    var lang = S.lang || 'en';

    // Update title for project drilldown (use real project name once data arrives)
    if (_state.target && _state.target.indexOf('kpi5-proj-') === 0 && isFirstPage) {
      var r0 = items[0];
      var pName = lang === 'ar' ? r0.project_name_ar : r0.project_name_en;
      if (pName) {
        _title.textContent = _escHtml(pName) + ' — ' + (S.dd_title_project || 'Late Detail');
      }
    }
    // Update title for trend (use returned month)
    if (_state.target && _state.target.indexOf('trend-') === 0 && isFirstPage && data.month) {
      _title.textContent = (S.dd_title_trend || 'Trend') + ' — ' + data.month;
    }

    var frag = document.createDocumentFragment();
    for (var i = 0; i < items.length; i++) {
      frag.appendChild(_makeInstallmentRow(items[i], lang));
    }
    _listBody.appendChild(frag);
  }

  function _makeInstallmentRow(row, lang) {
    var li = document.createElement('li');
    li.className = 'dd-row px-5 py-4';

    var projName = lang === 'ar'
      ? (row.project_name_ar || S.dd_no_project || 'No Project')
      : (row.project_name_en || S.dd_no_project || 'No Project');
    var hasCheque = row.pending_cheque > 0;
    var amtStr = F.formatEGP
      ? F.formatEGP(row.amount, lang, { fullValue: true })
      : _fmtEgp(row.amount);

    var chequeNote = hasCheque
      ? '<p class="text-[10px] text-amber-600 dark:text-amber-400 mt-0.5">'
          + _esc(S.dd_pending_cheque_note || 'incl. cheque')
          + '</p>'
      : '';

    li.innerHTML = ''
      + '<div class="flex items-start justify-between gap-3">'
        + '<div class="min-w-0 flex-1">'
          + '<p class="text-sm font-medium text-neutral-900 dark:text-neutral-100 truncate">'
            + _esc(row.customer_name)
          + '</p>'
          + '<p class="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5 truncate">'
            + _esc(projName)
          + '</p>'
          + '<p class="text-xs text-neutral-400 dark:text-neutral-500 mt-0.5 font-mono">'
            + _esc(row.date)
          + '</p>'
        + '</div>'
        + '<div class="shrink-0 text-end space-y-1">'
          + '<p class="text-sm font-semibold tabular text-neutral-900 dark:text-neutral-100">'
            + _esc(amtStr)
          + '</p>'
          + '<span class="' + _badgeClass(row.payment_state) + '">'
            + _esc(_stateLabel(row.payment_state))
          + '</span>'
          + chequeNote
        + '</div>'
      + '</div>';
    return li;
  }

  // ── Portfolio renderer (flat) ───────────────────────────────────────────────
  function _renderPortfolio(data) {
    var customers = (data && data.customers) || [];
    var isFirstPage = _listBody.children.length === 0;

    if (!customers.length) {
      if (isFirstPage) _emptyMsg.classList.remove('hidden');
      return;
    }

    var lang = S.lang || 'en';
    var frag = document.createDocumentFragment();
    for (var i = 0; i < customers.length; i++) {
      frag.appendChild(_makePortfolioRow(customers[i], lang));
    }
    _listBody.appendChild(frag);
  }

  function _makePortfolioRow(row, lang) {
    var li = document.createElement('li');
    li.className = 'dd-row px-5 py-4';

    var totalStr = F.formatEGP
      ? F.formatEGP(row.total_amount, lang, { fullValue: true })
      : _fmtEgp(row.total_amount);
    var dueStr = F.formatEGP
      ? F.formatEGP(row.total_due, lang, { fullValue: true })
      : _fmtEgp(row.total_due);

    var breakdownHtml = '';
    var breakdown = row.project_breakdown || [];
    for (var j = 0; j < breakdown.length; j++) {
      var pb = breakdown[j];
      var pName = lang === 'ar'
        ? (pb.project_name_ar || S.dd_no_project || 'No Project')
        : (pb.project_name_en || S.dd_no_project || 'No Project');
      var pbAmt = F.formatEGP
        ? F.formatEGP(pb.amount, lang, { fullValue: true })
        : _fmtEgp(pb.amount);
      breakdownHtml += ''
        + '<li class="dd-portfolio-breakdown-row">'
          + '<span class="truncate">' + _esc(pName) + '</span>'
          + '<span class="shrink-0 tabular">' + _esc(pbAmt) + '</span>'
        + '</li>';
    }

    li.innerHTML = ''
      + '<p class="text-sm font-semibold text-neutral-900 dark:text-neutral-100 truncate mb-1">'
        + _esc(row.customer_name)
      + '</p>'
      + '<div class="flex flex-wrap items-center gap-4 text-xs text-neutral-500 dark:text-neutral-400 mb-2">'
        + '<span>' + _esc(S.dd_total || 'Total') + ': '
          + '<span class="tabular text-neutral-700 dark:text-neutral-200 font-medium">'
            + _esc(totalStr)
          + '</span></span>'
        + '<span>' + _esc(S.dd_portfolio_due || 'Due') + ': '
          + '<span class="tabular text-neutral-700 dark:text-neutral-200 font-medium">'
            + _esc(dueStr)
          + '</span></span>'
        + '<span class="text-neutral-400 dark:text-neutral-600">'
          + _esc(String(row.record_count)) + ' ' + _esc(S.records || 'records')
        + '</span>'
      + '</div>'
      + (breakdownHtml ? '<ul class="space-y-0">' + breakdownHtml + '</ul>' : '');
    return li;
  }

  // ── Helpers ────────────────────────────────────────────────────────────────
  function _badgeClass(state) {
    return 'dd-payment-badge dd-payment-badge--' + (state || 'not_paid');
  }

  function _stateLabel(state) {
    var map = {
      not_paid: S.dd_state_not_paid || 'Not Paid',
      partial:  S.dd_state_partial  || 'Partial',
      paid:     S.dd_state_paid     || 'Paid',
    };
    return map[state] || state || '';
  }

  function _fmtEgp(val) {
    if (val == null) return '—';
    var n = Math.round(val);
    return n.toLocaleString('en-US') + ' EGP';
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

  // ── Event wiring ───────────────────────────────────────────────────────────
  function _wire() {
    if (!_panel) return;
    _closeBtn.addEventListener('click', close);
    _backdrop.addEventListener('click', close);
    _loadMoreBtn.addEventListener('click', function () {
      if (_state.hasNext && !_state.isLoading) _fetchPage();
    });

    // Delegated click handler for all [data-drilldown-target] cards.
    // Also fires on keyboard Enter/Space for accessibility (D7 adds full focus trap).
    document.addEventListener('click', function (e) {
      var el = e.target.closest('[data-drilldown-target]');
      if (!el) return;
      var target = el.getAttribute('data-drilldown-target');
      if (!target) return;
      open(target, {}, el);
    });

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var el = e.target.closest('[data-drilldown-target]');
      if (!el) return;
      var target = el.getAttribute('data-drilldown-target');
      if (!target) return;
      e.preventDefault();
      open(target, {}, el);
    });
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.drilldownController = {
    open:  open,
    close: close,
    get state() { return Object.assign({}, _state); },
    // Exposed for filter bar (D4) to call after filter change
    _refetch: function () {
      if (!_state.target) return;
      _state.cursor  = null;
      _state.hasNext = false;
      _listBody.innerHTML = '';
      _hide(_emptyMsg);
      _hide(_errorMsg);
      _hide(_loadMoreBtn);
      _fetchPage();
    },
  };

  // Export _resolveEndpoint for unit tests (Node.js require())
  if (typeof module !== 'undefined') {
    module.exports = { _resolveEndpoint: _resolveEndpoint };
  }

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
