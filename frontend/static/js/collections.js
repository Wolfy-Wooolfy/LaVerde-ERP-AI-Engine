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

  var _lastFetchData       = null;
  var _kpi6Chart           = null;
  var _autoRefreshInterval = null;

  // ── Render helpers ────────────────────────────────────────────────────────

  function fadeIn(el) {
    if (!el) return;
    el.classList.remove('opacity-0');
    el.classList.add('opacity-100');
  }

  // Returns true when a rate field should display "—" instead of a percentage.
  // Covers: null rate (no denominator) and data-entry phase (numerator=0, denominator>0).
  function isRateUnavailable(period) {
    if (!period) return true;
    if (period.rate_percent === null || period.rate_percent === undefined) return true;
    if (period.rate_percent === 0 && period.numerator_egp === 0 && period.denominator_egp > 0) return true;
    return false;
  }

  function getRateTooltip(period, s) {
    if (!period || period.rate_percent === null || period.rate_percent === undefined) {
      return s.no_installments_due || 'No installments due in this period';
    }
    if (period.rate_percent === 0 && period.numerator_egp === 0 && period.denominator_egp > 0) {
      return s.data_entry_in_progress || 'Data entry in progress';
    }
    return '';
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
      var mtdUnavail = isRateUnavailable(kpi4.mtd);
      var ytdUnavail = isRateUnavailable(kpi4.ytd);
      var mtdRate = mtdUnavail ? '—' : fmt.formatRate(kpi4.mtd.rate_percent, lang);
      var ytdRate = ytdUnavail ? '—' : fmt.formatRate(kpi4.ytd.rate_percent, lang);

      if (mtdEl) { mtdEl.textContent = mtdRate; mtdEl.title = mtdUnavail ? getRateTooltip(kpi4.mtd, s) : ''; fadeIn(mtdEl); }
      if (ytdEl) { ytdEl.textContent = ytdRate; ytdEl.title = ytdUnavail ? getRateTooltip(kpi4.ytd, s) : ''; fadeIn(ytdEl); }
      if (s4) {
        s4.textContent = (mtdUnavail && ytdUnavail)
          ? (s.data_entry_in_progress || 'Data entry in progress')
          : (s.mtd || 'MTD') + ' / ' + (s.ytd || 'YTD') + ' · ' + kpi4.ytd.period_start;
        fadeIn(s4);
      }
      if (c4) c4.setAttribute('aria-label', (s.collection_rate || 'Collection Rate') + ': MTD ' + mtdRate + ' / YTD ' + ytdRate);
    }
  }

  // D2.6 — Row 3: Top 3 Projects
  function renderRow3(state) {
    var kpi5a = state[2];
    var kpi5b = state[6];
    if (!kpi5a || !kpi5a.projects) return;
    var s    = window.COLLECTIONS_STRINGS || {};
    var lang = s.lang || 'en';
    var fmt  = window.CollectionsFormatters;

    for (var i = 0; i < kpi5a.projects.length; i++) {
      var p    = kpi5a.projects[i];
      var idx  = i + 1;
      var card   = document.getElementById('col-proj' + idx + '-card');
      var nameEl = document.getElementById('col-proj' + idx + '-name');
      var lateEl = document.getElementById('col-proj' + idx + '-late');
      var rateEl = document.getElementById('col-proj' + idx + '-rate');

      var displayName = (s.project_names && s.project_names[p.project_name]) || p.project_name;
      var lateAmt     = fmt.formatEGP(p.late_uncollected, lang);

      var proj5b = null;
      if (kpi5b && kpi5b.projects) {
        for (var j = 0; j < kpi5b.projects.length; j++) {
          if (kpi5b.projects[j].project_id === p.project_id) { proj5b = kpi5b.projects[j]; break; }
        }
      }
      var rateUnavail = isRateUnavailable(proj5b);
      var rateStr     = rateUnavail ? '—' : fmt.formatRate(proj5b.rate_percent, lang);
      var rateTip     = rateUnavail ? getRateTooltip(proj5b, s) : '';

      if (nameEl) { nameEl.textContent = displayName; fadeIn(nameEl); }
      if (lateEl) {
        lateEl.textContent = lateAmt;
        lateEl.title = fmt.formatEGP(p.late_uncollected, lang, { fullValue: true });
        fadeIn(lateEl);
      }
      if (rateEl) { rateEl.textContent = rateStr; rateEl.title = rateTip; fadeIn(rateEl); }
      if (card) {
        card.setAttribute('data-drilldown-target', 'kpi5-proj-' + p.project_id);
        card.setAttribute('aria-label', displayName + ': ' + lateAmt + ' · ' + rateStr);
      }
    }
  }

  // D2.7 — Row 4: 6-Month Collection Trend
  function renderRow4(state) {
    var kpi6 = state[4];
    if (!kpi6 || !kpi6.months) return;
    var s    = window.COLLECTIONS_STRINGS || {};
    var lang = s.lang || 'en';
    var fmt  = window.CollectionsFormatters;

    var canvas  = document.getElementById('col-kpi6-chart');
    var emptyEl = document.getElementById('col-kpi6-chart-empty');
    if (!canvas) return;

    var amounts = kpi6.months.map(function (m) { return m.amount; });
    var allZero = amounts.every(function (a) { return a === 0; });

    if (allZero) {
      canvas.style.display = 'none';
      if (emptyEl) emptyEl.classList.remove('hidden');
      return;
    }
    canvas.style.display = '';
    if (emptyEl) emptyEl.classList.add('hidden');

    var labels = kpi6.months.map(function (m) {
      return lang === 'ar' ? m.label_ar : m.label_en;
    });

    if (_kpi6Chart) { _kpi6Chart.destroy(); _kpi6Chart = null; }

    var p   = palette();
    var avg = kpi6.average_monthly;

    applyChartDefaults();
    _kpi6Chart = new Chart(canvas, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: s.trend_title || '6-Month Collection Trend',
            data: amounts,
            borderColor: '#059669',
            backgroundColor: 'rgba(5, 150, 105, 0.3)',
            fill: true,
            borderWidth: 2,
            pointRadius: 3,
            pointHoverRadius: 5,
            tension: 0.3,
          },
          {
            label: s.average || 'Average',
            data: kpi6.months.map(function () { return avg; }),
            borderColor: p.text,
            borderDash: [6, 4],
            borderWidth: 1.5,
            pointRadius: 0,
            fill: false,
            tension: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: true,
            labels: { usePointStyle: true, pointStyleWidth: 8 },
          },
          tooltip: {
            filter: function (tooltipItem) { return tooltipItem.datasetIndex !== 1; },
            callbacks: {
              label: function (ctx) { return ' ' + fmt.formatEGP(ctx.parsed.y, lang); },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { color: p.text },
          },
          y: {
            grid: { color: p.gridLines },
            ticks: {
              color: p.text,
              callback: function (val) { return fmt.formatEGP(val, lang); },
            },
            beginAtZero: true,
          },
        },
      },
    });
    registerChart('col-kpi6-chart', _kpi6Chart);
  }

  // D2.8 — Data-entry banner auto-hide
  function shouldShowDataEntryBanner(state) {
    if (new URLSearchParams(window.location.search).get('show_banner') === '1') return true;
    var kpi4 = state[5];
    if (kpi4 && (isRateUnavailable(kpi4.mtd) || isRateUnavailable(kpi4.ytd))) return true;
    var kpi6 = state[4];
    if (kpi6 && kpi6.months) {
      var nonZero = kpi6.months.filter(function (m) { return m.amount > 0; }).length;
      if (nonZero < 5) return true;
    }
    return false;
  }

  function updateDataEntryBanner(state) {
    var banner = document.getElementById('col-data-entry-notice');
    if (!banner) return;
    if (shouldShowDataEntryBanner(state)) {
      banner.classList.remove('hidden');
    } else {
      banner.classList.add('hidden');
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
      renderRow3(results);
      renderRow4(results);
      updateDataEntryBanner(results);
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

  // D2.9 — 60s auto-refresh with Visibility API pause
  function startAutoRefresh() {
    _autoRefreshInterval = setInterval(fetchAllKPIs, 60000);
  }

  function stopAutoRefresh() {
    if (_autoRefreshInterval) {
      clearInterval(_autoRefreshInterval);
      _autoRefreshInterval = null;
    }
  }

  function init() {
    // Redirect the topbar Refresh button to our handler on this page.
    var topbarBtn = document.getElementById('refresh-btn');
    if (topbarBtn) topbarBtn.onclick = collectionsRefresh;

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        stopAutoRefresh();
      } else {
        fetchAllKPIs().then(startAutoRefresh);
      }
    });

    window.collectionsDashboard = {
      get state() { return _lastFetchData; },
      fetchAll: fetchAllKPIs
    };
    window.collectionsDashboard.fetchAll().then(startAutoRefresh);
  }

  window.collectionsRefresh = function () {
    stopAutoRefresh();
    fetchAllKPIs().then(startAutoRefresh);
  };

  document.addEventListener('DOMContentLoaded', init);
}());
