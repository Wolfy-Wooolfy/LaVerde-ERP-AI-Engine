(function () {
  'use strict';

  var KPI_ENDPOINTS = [
    '/api/v1/customer-accounts/kpi/total-receivables',
    '/api/v1/customer-accounts/kpi/top-overdue-customers',
    '/api/v1/customer-accounts/kpi/unallocated-wallet-balance',
    '/api/v1/customer-accounts/refunds/summary',
  ];

  var _lastFetchData       = null;
  var _autoRefreshInterval = null;
  var _heartbeatInterval   = null;
  var _lastFetchTime       = null;

  // ── Render helpers ────────────────────────────────────────────────────────

  function fadeIn(el) {
    if (!el) return;
    el.classList.remove('opacity-0');
    el.classList.add('opacity-100');
  }

  function _escHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ── KPI A — Total Customer Receivables ───────────────────────────────────

  function renderKpiA(data) {
    if (!data) return;
    var s    = window.CA_STRINGS || {};
    var lang = s.lang || 'en';
    var fmt  = window.CollectionsFormatters;

    var valueEl    = document.getElementById('ca-kpia-value');
    var subtitleEl = document.getElementById('ca-kpia-subtitle');
    var cardEl     = document.getElementById('ca-kpia-container');
    var asOfEl     = document.getElementById('ca-as-of');

    var formatted = fmt.formatEGP(data.value, lang);
    var custCount = fmt.formatCount(data.customer_count, lang);
    var instCount = fmt.formatCount(data.record_count, lang);
    var subtitle  = custCount + ' ' + (s.ca_customers || 'customers')
                  + ' · ' + instCount + ' ' + (s.ca_installments || 'installments');

    if (valueEl)    { valueEl.textContent = formatted; valueEl.title = fmt.formatEGP(data.value, lang, { fullValue: true }); fadeIn(valueEl); }
    if (subtitleEl) { subtitleEl.textContent = subtitle; fadeIn(subtitleEl); }
    if (cardEl)     cardEl.setAttribute('aria-label', (s.ca_total_receivables || 'Total Receivables') + ': ' + formatted);

    if (asOfEl && data.as_of) {
      asOfEl.textContent = new Date(data.as_of).toLocaleDateString(
        lang === 'ar' ? 'ar-EG' : 'en-GB',
        { day: 'numeric', month: 'long', year: 'numeric' }
      );
    }
  }

  // ── KPI B — Top Overdue Customers (card + table) ─────────────────────────

  function renderKpiB(data) {
    if (!data) return;
    var s    = window.CA_STRINGS || {};
    var lang = s.lang || 'en';
    var fmt  = window.CollectionsFormatters;

    // Concentration card
    var concEl  = document.getElementById('ca-kpib-concentration');
    var subEl   = document.getElementById('ca-kpib-subtitle');
    var cardEl  = document.getElementById('ca-kpib-container');
    var conc    = data.top_n_concentration || {};
    var concPct = fmt.formatRate(conc.pct, lang);
    var topNLbl = (s.ca_top_n_label || 'Top {n} customers').replace('{n}', conc.n || 10);
    var custCnt = fmt.formatCount(data.overdue_customer_count, lang);
    var subtitle = topNLbl + ' · ' + custCnt + ' ' + (s.ca_overdue_customers || 'overdue customers');

    if (concEl) { concEl.textContent = concPct; fadeIn(concEl); }
    if (subEl)  { subEl.textContent = subtitle; fadeIn(subEl); }
    if (cardEl) cardEl.setAttribute('aria-label', (s.ca_top_risk || 'Overdue Risk') + ': ' + concPct);

    // Detail table
    var tbody     = document.getElementById('ca-kpib-table-body');
    if (!tbody) return;
    var customers = data.top_customers || [];
    if (!customers.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="px-4 py-8 text-center text-sm text-neutral-400 dark:text-neutral-500">'
        + _escHtml(s.ca_no_data || 'No data') + '</td></tr>';
      return;
    }

    var rows = '';
    for (var i = 0; i < customers.length; i++) {
      var c       = customers[i];
      var amt     = fmt.formatEGP(c.due_amount, lang);
      var amtFull = fmt.formatEGP(c.due_amount, lang, { fullValue: true });
      var insts   = fmt.formatCount(c.installment_count, lang);
      rows += '<tr class="border-b border-neutral-100 dark:border-neutral-800'
            + ' hover:bg-neutral-50 dark:hover:bg-neutral-800/50 transition-colors'
            + ' cursor-pointer focus-visible:outline-none'
            + ' focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500"'
            + ' data-partner-id="' + _escHtml(String(c.customer_id)) + '"'
            + ' role="button" tabindex="0"'
            + ' aria-label="' + _escHtml(c.customer_name) + '">'
            + '<td class="px-4 py-3 text-sm tabular text-neutral-500 dark:text-neutral-400">'
            +   _escHtml(String(c.rank))
            + '</td>'
            + '<td class="px-4 py-3 text-sm text-neutral-900 dark:text-neutral-100">'
            +   _escHtml(c.customer_name)
            + '</td>'
            + '<td class="px-4 py-3 text-sm tabular text-end text-neutral-900 dark:text-neutral-100"'
            +   ' title="' + _escHtml(amtFull) + '">'
            +   _escHtml(amt)
            + '</td>'
            + '<td class="px-4 py-3 text-sm tabular text-end text-neutral-500 dark:text-neutral-400">'
            +   _escHtml(insts)
            + '</td>'
            + '</tr>';
    }
    tbody.innerHTML = rows;
  }

  // ── KPI C — Unallocated Wallet Balance ───────────────────────────────────

  function renderKpiC(data) {
    if (!data) return;
    var s    = window.CA_STRINGS || {};
    var lang = s.lang || 'en';
    var fmt  = window.CollectionsFormatters;

    var valueEl    = document.getElementById('ca-kpic-value');
    var subtitleEl = document.getElementById('ca-kpic-subtitle');
    var cardEl     = document.getElementById('ca-kpic-container');

    var formatted = fmt.formatEGP(data.value, lang);
    var custCount = fmt.formatCount(data.customer_count, lang);
    var subtitle  = custCount + ' ' + (s.ca_customers || 'customers');

    if (valueEl)    { valueEl.textContent = formatted; valueEl.title = fmt.formatEGP(data.value, lang, { fullValue: true }); fadeIn(valueEl); }
    if (subtitleEl) { subtitleEl.textContent = subtitle; fadeIn(subtitleEl); }
    if (cardEl)     cardEl.setAttribute('aria-label', (s.ca_unallocated || 'Unallocated Wallet') + ': ' + formatted);
  }

  // ── Refunds Alert ────────────────────────────────────────────────────────

  function renderRefunds(data) {
    if (!data) return;
    var s         = window.CA_STRINGS || {};
    var lang      = s.lang || 'en';
    var fmt       = window.CollectionsFormatters;
    var contentEl = document.getElementById('ca-refunds-content');
    if (!contentEl) return;

    // total_refunds is negative — display absolute value
    var absTotal  = Math.abs(data.total_refunds);
    var formatted = fmt.formatEGP(absTotal, lang);
    var fullFmt   = fmt.formatEGP(absTotal, lang, { fullValue: true });
    var count     = fmt.formatCount(data.refund_count, lang);

    var text = (s.ca_refunds_total || 'Total refunds') + ': ' + formatted
             + ' · ' + count + ' ' + (s.ca_refunds_records || 'records');

    if (data.null_partner_count > 0) {
      var nullCnt = fmt.formatCount(data.null_partner_count, lang);
      text += ' · ' + nullCnt + ' ' + (s.ca_unknown_partners || 'unknown partner(s)');
    }

    contentEl.innerHTML = '<span title="' + _escHtml(fullFmt) + '">' + _escHtml(text) + '</span>';
    fadeIn(contentEl);
  }

  // ── Error banner ──────────────────────────────────────────────────────────

  function showErrorBanner() {
    var banner = document.getElementById('ca-error-banner');
    if (banner) banner.classList.remove('hidden');
  }

  function hideErrorBanner() {
    var banner = document.getElementById('ca-error-banner');
    if (banner) banner.classList.add('hidden');
  }

  // ── Fetch ─────────────────────────────────────────────────────────────────

  // `manual` is true ONLY for a user-initiated refresh; it adds ?refresh=1 so the
  // server skips its cache (see the note above crmWithRefresh in api.js). The
  // hourly timer and the visibilitychange handler call this with no argument.
  function fetchAllKPIs(manual) {
    var t0 = performance.now();
    return Promise.all(KPI_ENDPOINTS.map(function (url) {
      return fetch(crmWithRefresh(url, manual), { headers: { Accept: 'application/json' } })
        .then(function (r) { return r.json(); });
    })).then(function (results) {
      var elapsed = Math.round(performance.now() - t0);
      console.log('[CustomerAccounts] Fetched 4 KPIs in ' + elapsed + 'ms');
      var state = {
        receivables: results[0],
        topOverdue:  results[1],
        wallet:      results[2],
        refunds:     results[3],
      };
      _lastFetchData = state;
      _lastFetchTime = new Date();
      hideErrorBanner();
      renderKpiA(state.receivables);
      renderKpiB(state.topOverdue);
      renderKpiC(state.wallet);
      renderRefunds(state.refunds);
      return state;
    }).catch(function (err) {
      console.error('[CustomerAccounts] fetch error', err);
      showErrorBanner();
      throw err;
    });
  }

  // ── Auto-refresh (1h, paused when tab is hidden) ─────────────────────────

  function startAutoRefresh() {
    if (_autoRefreshInterval) return;
    // Wrapped, never `setInterval(fetchAllKPIs, ...)`. A bare reference hands the
    // callback whatever the host chooses to pass: Firefox historically passed an
    // interval-lateness argument, which would land in `manual`, be truthy, and
    // put ?refresh=1 on every hourly tick of every open tab — permanently
    // defeating the cache Phase 1 (79e9b15) exists to protect. Legacy-only on
    // current browsers, but this was the one shape in this file where a
    // host-supplied argument could reach `manual` at all.
    //
    // The explicit call also routes the tick through restartTimersAfter, so a
    // failed hourly fetch no longer leaves an unhandled rejection behind.
    _autoRefreshInterval = setInterval(function () {
      restartTimersAfter(fetchAllKPIs());
    }, 3600000);
  }

  function stopAutoRefresh() {
    if (_autoRefreshInterval) {
      clearInterval(_autoRefreshInterval);
      _autoRefreshInterval = null;
    }
  }

  // ── Live-time heartbeat ───────────────────────────────────────────────────

  function relativeTime(date) {
    var s    = window.CA_STRINGS || {};
    var lang = s.lang || 'en';
    if (!date) return s.just_now || (lang === 'ar' ? 'الآن' : 'Just now');
    var diffSec = Math.round((Date.now() - date.getTime()) / 1000);
    if (diffSec < 10) {
      return s.just_now || (lang === 'ar' ? 'الآن' : 'Just now');
    }
    if (diffSec < 60) {
      var secLabel = s.seconds_short || (lang === 'ar' ? 'ثانية' : 's');
      var agoWord  = s.ago           || (lang === 'ar' ? 'منذ'   : '');
      return lang === 'ar'
        ? agoWord + ' ' + diffSec + ' ' + secLabel
        : diffSec + secLabel + ' ago';
    }
    var mins     = Math.floor(diffSec / 60);
    var minLabel = mins === 1
      ? (s.minute_short  || (lang === 'ar' ? 'دقيقة'  : 'min'))
      : (s.minutes_short || (lang === 'ar' ? 'دقائق' : 'mins'));
    var agoWord  = s.ago || (lang === 'ar' ? 'منذ' : '');
    return lang === 'ar'
      ? agoWord + ' ' + mins + ' ' + minLabel
      : mins + ' ' + minLabel + ' ago';
  }

  function tickHeartbeat() {
    var el = document.getElementById('ca-last-updated');
    if (!el || !_lastFetchTime) return;
    el.textContent = relativeTime(_lastFetchTime);
  }

  function startHeartbeat() {
    if (_heartbeatInterval) return;
    tickHeartbeat();
    _heartbeatInterval = setInterval(tickHeartbeat, 10000);
  }

  function stopHeartbeat() {
    if (_heartbeatInterval) {
      clearInterval(_heartbeatInterval);
      _heartbeatInterval = null;
    }
  }

  // Every fetchAllKPIs caller funnels through here. All FOUR of them: the manual
  // refresh button, the visibilitychange re-fetch, the initial page load, and
  // the hourly auto-refresh tick in startAutoRefresh.
  //
  // fetchAllKPIs re-throws after showing the error banner. A caller that only
  // chained .then() therefore lost BOTH timers for the life of the page on any
  // failure, and left an unhandled rejection behind. The initial load is the
  // worst of the four: the timers had never started, so there was no auto
  // refresh to recover on and no heartbeat, on a page the user had not clicked
  // anything on yet.
  function restartTimersAfter(promise) {
    return promise.catch(function (err) {
      // Swallowed so the restart below runs on both outcomes — but never
      // silently. fetchAllKPIs wraps the render functions too, so a genuine
      // coding error inside renderKpiA..C or renderRefunds arrives here having
      // already been mis-reported to the user as a connection error; without
      // this line it would then vanish with no trace anywhere. Distinct wording
      // from fetchAllKPIs's own 'fetch error' so the two layers stay tellable
      // apart in the console.
      console.error('[CustomerAccounts] refresh failed; restarting timers', err);
    }).then(function () {
      startAutoRefresh();
      startHeartbeat();
    });
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  function _wireKpiBDrilldown() {
    var tbody = document.getElementById('ca-kpib-table-body');
    if (!tbody) return;

    function _openPanel(e) {
      var tr = e.target.closest('[data-partner-id]');
      if (!tr) return;
      var pid = parseInt(tr.getAttribute('data-partner-id'), 10);
      if (!pid || !window.caCustomerPanel) return;
      window.caCustomerPanel.open(pid, tr);
    }

    tbody.addEventListener('click', _openPanel);

    tbody.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var tr = e.target.closest('[data-partner-id]');
      if (!tr) return;
      var pid = parseInt(tr.getAttribute('data-partner-id'), 10);
      if (!pid || !window.caCustomerPanel) return;
      e.preventDefault();
      window.caCustomerPanel.open(pid, tr);
    });
  }

  function _wireRefundsDrilldown() {
    var card = document.getElementById('ca-refunds-container');
    if (!card) return;

    card.addEventListener('click', function () {
      if (window.caRefundsPanel) window.caRefundsPanel.open(card);
    });

    card.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      if (window.caRefundsPanel) window.caRefundsPanel.open(card);
    });
  }

  function init() {
    // Wrapped, never `onclick = window.customerAccountsRefresh`: a bare reference
    // makes the browser pass the MouseEvent as the first argument, so `manual`
    // would be truthy by accident here and falsy everywhere else it is called.
    var topbarBtn = document.getElementById('refresh-btn');
    if (topbarBtn) topbarBtn.onclick = function () { customerAccountsRefresh(true); };

    _wireKpiBDrilldown();
    _wireRefundsDrilldown();

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        stopAutoRefresh();
        stopHeartbeat();
      } else {
        stopAutoRefresh();
        // The heartbeat now restarts with the auto-refresh rather than
        // synchronously ahead of it — a delay equal to one cached fetch
        // (~20ms), in exchange for one restart path shared by all four
        // callers and no unhandled rejection on a failed re-focus.
        restartTimersAfter(fetchAllKPIs());
      }
    });

    window.customerAccountsDashboard = {
      get state() { return _lastFetchData; },
      fetchAll: fetchAllKPIs,
    };

    restartTimersAfter(fetchAllKPIs());
  }

  window.customerAccountsRefresh = function (manual) {
    stopAutoRefresh();
    stopHeartbeat();
    // Affordance ONLY when a human asked. This refresh is slow by design — it
    // carries ?refresh=1 and goes to Odoo — but the hourly timer, the
    // visibilitychange re-fetch and the initial load all reach this same
    // function's siblings unattended. A loading bar appearing on its own once
    // an hour is a regression, not a feature.
    if (manual) crmRefreshFeedback.start();
    // restartTimersAfter never rejects, so this .then runs on both outcomes:
    // the spinner always stops and a failed refresh stays clickable.
    restartTimersAfter(fetchAllKPIs(manual)).then(function () {
      if (manual) crmRefreshFeedback.stop();
    });
  };

  document.addEventListener('DOMContentLoaded', init);
}());
