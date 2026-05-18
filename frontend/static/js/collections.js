(function () {
  'use strict';

  var KPI_ENDPOINTS = [
    '/api/v1/collections/kpi/late-uncollected',
    '/api/v1/collections/kpi/total-portfolio-value',
    '/api/v1/collections/kpi/late-uncollected-by-project',
    '/api/v1/collections/kpi/pending-check-exposure',
    '/api/v1/collections/kpi/collection-trend-6m',
    '/api/v1/collections/kpi/collection-rate',
    '/api/v1/collections/kpi/collection-rate-by-project',
  ];

  var _lastFetchData = null;

  // ── Render helpers ────────────────────────────────────────────────────────

  function fadeIn(el) {
    if (!el) return;
    el.classList.remove('opacity-0');
    el.classList.add('opacity-100');
  }

  // D2.4 — KPI 2 Late Uncollected hero card
  function renderKpi2(kpi) {
    if (!kpi) return;
    var s = window.COLLECTIONS_STRINGS || {};
    var lang = s.lang || 'en';
    var fmt = window.CollectionsFormatters;

    var valueEl    = document.getElementById('col-kpi2-value');
    var recordsEl  = document.getElementById('col-kpi2-records');
    var asOfEl     = document.getElementById('col-kpi2-as-of');
    var subtitleEl = document.getElementById('col-kpi2-subtitle');
    var card       = document.querySelector('[data-drilldown-target="kpi2"]');

    var formatted  = fmt.formatEGP(kpi.value, lang);
    var fullVal    = fmt.formatEGP(kpi.value, lang, { fullValue: true });
    var count      = fmt.formatCount(kpi.record_count, lang);
    var asOf       = kpi.as_of
      ? new Date(kpi.as_of).toLocaleDateString(
          lang === 'ar' ? 'ar-EG' : 'en-GB',
          { day: 'numeric', month: 'long', year: 'numeric' }
        )
      : '—';

    if (valueEl) {
      valueEl.textContent = formatted;
      valueEl.title = fullVal;
      fadeIn(valueEl);
    }
    if (recordsEl) recordsEl.textContent = count;
    if (asOfEl)    asOfEl.textContent    = asOf;
    if (subtitleEl) fadeIn(subtitleEl);
    if (card) card.setAttribute('aria-label', (s.late_uncollected || 'Late Uncollected') + ': ' + formatted);
  }

  // D2.5 — Row 2: KPI 1 (Portfolio), KPI 3 (Pending Checks), KPI 4 (Rate)
  function renderRow2(state) {
    var s   = window.COLLECTIONS_STRINGS || {};
    var lang = s.lang || 'en';
    var fmt = window.CollectionsFormatters;

    // KPI 1 — Total Portfolio Value
    var kpi1 = state[1];
    var v1   = document.getElementById('col-kpi1-value');
    var s1   = document.getElementById('col-kpi1-subtitle');
    var c1   = document.getElementById('col-kpi1-container');
    if (v1 && kpi1) {
      var f1 = fmt.formatEGP(kpi1.value, lang);
      v1.textContent = f1;
      v1.title = fmt.formatEGP(kpi1.value, lang, { fullValue: true });
      fadeIn(v1);
      if (c1) c1.setAttribute('aria-label', (s.total_portfolio || 'Total Portfolio Value') + ': ' + f1);
    }
    if (s1 && kpi1) {
      s1.textContent = fmt.formatCount(kpi1.record_count, lang) + ' ' + (s.installments || 'installments');
      fadeIn(s1);
    }

    // KPI 3 — Pending Check Exposure
    var kpi3 = state[3];
    var v3   = document.getElementById('col-kpi3-value');
    var s3   = document.getElementById('col-kpi3-subtitle');
    var c3   = document.getElementById('col-kpi3-container');
    if (v3 && kpi3) {
      var f3 = fmt.formatEGP(kpi3.value, lang);
      v3.textContent = f3;
      v3.title = fmt.formatEGP(kpi3.value, lang, { fullValue: true });
      fadeIn(v3);
      if (c3) c3.setAttribute('aria-label', (s.pending_check || 'Pending Check Exposure') + ': ' + f3);
    }
    if (s3) {
      s3.textContent = s.in_pipeline || 'in pipeline';
      fadeIn(s3);
    }

    // KPI 4 — Collection Rate MTD / YTD
    var kpi4 = state[5];
    var mtdEl = document.getElementById('col-kpi4-mtd');
    var ytdEl = document.getElementById('col-kpi4-ytd');
    var s4    = document.getElementById('col-kpi4-subtitle');
    var c4    = document.getElementById('col-kpi4-container');
    if (kpi4) {
      var mtdRate = fmt.formatRate(kpi4.mtd.rate_percent, lang);
      var ytdRate = fmt.formatRate(kpi4.ytd.rate_percent, lang);
      var bothNull = kpi4.mtd.rate_percent === null && kpi4.ytd.rate_percent === null;

      if (mtdEl) { mtdEl.textContent = mtdRate; fadeIn(mtdEl); }
      if (ytdEl) { ytdEl.textContent = ytdRate; fadeIn(ytdEl); }
      if (s4) {
        s4.textContent = bothNull
          ? (s.data_entry_in_progress || 'Data entry in progress')
          : (s.mtd || 'MTD') + ' / ' + (s.ytd || 'YTD') + ' · ' + kpi4.ytd.period_start;
        fadeIn(s4);
      }
      if (c4) c4.setAttribute('aria-label', (s.collection_rate || 'Collection Rate') + ': MTD ' + mtdRate + ' / YTD ' + ytdRate);
    }
  }

  function fetchAllKPIs() {
    var t0 = performance.now();
    return Promise.all(KPI_ENDPOINTS.map(function (url) {
      return fetch(url, { headers: { Accept: 'application/json' } })
        .then(function (r) { return r.json(); });
    })).then(function (results) {
      var elapsed = Math.round(performance.now() - t0);
      console.log('[Collections] Fetched 7 KPIs in ' + elapsed + 'ms');
      _lastFetchData = results;
      hideErrorBanner();
      updateTimestamps();
      renderKpi2(results[0]);
      renderRow2(results);
      // TODO(D2): render KPI 5 — Late Uncollected per project → results[2]
      // TODO(D2): render KPI 6 — 6-Month Trend          → results[4]
      // TODO(D2): render KPI 5b — Collection Rate per project → results[6]
      return results;
    }).catch(function (err) {
      showErrorBanner();
      throw err;
    });
  }

  function updateTimestamps() {
    var now = new Date();
    var strings = window.COLLECTIONS_STRINGS || {};
    var asOf = document.getElementById('col-as-of');
    var lastUpdated = document.getElementById('col-last-updated');
    var dot = document.getElementById('col-live-dot');

    if (asOf) {
      asOf.textContent = now.toLocaleDateString(
        strings.lang === 'ar' ? 'ar-EG' : 'en-GB',
        { day: 'numeric', month: 'long', year: 'numeric' }
      );
    }
    if (lastUpdated) {
      lastUpdated.textContent = now.toLocaleTimeString(
        strings.lang === 'ar' ? 'ar-EG' : 'en-GB',
        { hour: '2-digit', minute: '2-digit' }
      );
    }
    if (dot) {
      dot.className = 'w-1.5 h-1.5 rounded-full bg-success-500 animate-pulse';
    }
  }

  function showErrorBanner() {
    var banner = document.getElementById('col-error-banner');
    if (banner) banner.classList.remove('hidden');
  }

  function hideErrorBanner() {
    var banner = document.getElementById('col-error-banner');
    if (banner) banner.classList.add('hidden');
  }

  function init() {
    // Redirect the topbar Refresh button to our handler on this page.
    var topbarBtn = document.getElementById('refresh-btn');
    if (topbarBtn) topbarBtn.onclick = collectionsRefresh;

    window.collectionsDashboard = {
      get state() { return _lastFetchData; },
      fetchAll: fetchAllKPIs
    };
    window.collectionsDashboard.fetchAll();
  }

  window.collectionsRefresh = function () {
    fetchAllKPIs();
  };

  document.addEventListener('DOMContentLoaded', init);
}());
