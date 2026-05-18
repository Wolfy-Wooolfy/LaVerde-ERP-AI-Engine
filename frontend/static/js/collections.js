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
      // TODO(D2): render KPI 1 — Total Portfolio Value  → results[1]
      // TODO(D2): render KPI 5 — Late Uncollected per project → results[2]
      // TODO(D2): render KPI 3 — Pending Check Exposure → results[3]
      // TODO(D2): render KPI 6 — 6-Month Trend          → results[4]
      // TODO(D2): render KPI 4 — Collection Rate MTD/YTD → results[5]
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
