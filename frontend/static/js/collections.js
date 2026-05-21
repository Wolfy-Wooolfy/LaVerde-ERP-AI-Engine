(function () {
  'use strict';

  var KPI_ENDPOINTS = [
    '/api/v1/collections/kpi/late-uncollected',
    '/api/v1/collections/kpi/total-portfolio-value',
    '/api/v1/collections/kpi/late-uncollected-by-project',
    '/api/v1/collections/kpi/collection-trend-6m',
    '/api/v1/collections/kpi/collection-rate',
    '/api/v1/collections/kpi/collection-rate-by-project',
    '/api/v1/collections/kpi/expected-forecast',
  ];

  var _lastFetchData       = null;
  var _kpi6Chart           = null;
  var _autoRefreshInterval = null;
  var _heartbeatInterval   = null;
  var _lastFetchTime       = null;

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

  // ── Section 1 — Portfolio Scale (KPI 1) ───────────────────────────────────

  function renderSection1(state) {
    var kpi1 = state.portfolio;
    if (!kpi1) return;
    var s    = window.COLLECTIONS_STRINGS || {};
    var lang = s.lang || 'en';
    var fmt  = window.CollectionsFormatters;

    var valueEl = document.getElementById('col-kpi1-value');
    var subEl   = document.getElementById('col-kpi1-subtitle');
    var card    = document.getElementById('col-kpi1-container');

    var f1 = fmt.formatEGP(kpi1.value, lang);
    if (valueEl) {
      valueEl.textContent = f1;
      valueEl.title = fmt.formatEGP(kpi1.value, lang, { fullValue: true });
      fadeIn(valueEl);
    }
    if (subEl) {
      subEl.textContent = fmt.formatCount(kpi1.record_count, lang) + ' ' + (s.installments || 'installments');
      fadeIn(subEl);
    }
    if (card) card.setAttribute('aria-label', (s.total_portfolio || 'Total Portfolio Value') + ': ' + f1);
  }

  // ── Section 2 — Current Risk (KPI 2) ─────────────────────────────────────

  function renderSection2(state) {
    var kpi2 = state.late;
    if (!kpi2) return;
    var s    = window.COLLECTIONS_STRINGS || {};
    var lang = s.lang || 'en';
    var fmt  = window.CollectionsFormatters;

    var valueEl    = document.getElementById('col-kpi2-value');
    var recordsEl  = document.getElementById('col-kpi2-records');
    var asOfEl     = document.getElementById('col-kpi2-as-of');
    var subtitleEl = document.getElementById('col-kpi2-subtitle');
    var card       = document.getElementById('col-kpi2-container');

    var formatted = fmt.formatEGP(kpi2.value, lang);
    var count     = fmt.formatCount(kpi2.record_count, lang);
    var asOf      = kpi2.as_of
      ? new Date(kpi2.as_of).toLocaleDateString(
          lang === 'ar' ? 'ar-EG' : 'en-GB',
          { day: 'numeric', month: 'long', year: 'numeric' }
        )
      : '—';

    if (valueEl)    { valueEl.textContent = formatted; valueEl.title = fmt.formatEGP(kpi2.value, lang, { fullValue: true }); fadeIn(valueEl); }
    if (recordsEl)  recordsEl.textContent = count;
    if (asOfEl)     asOfEl.textContent    = asOf;
    if (subtitleEl) fadeIn(subtitleEl);
    if (card) card.setAttribute('aria-label', (s.late_uncollected || 'Late Uncollected') + ': ' + formatted);

    // Cheques annotation — PATH A (Decision 11.13).
    // cheques_in_pipeline is a SUBSET of value; show only when > 0.
    var annotationEl = document.getElementById('col-kpi2-cheques-annotation');
    var chequesValEl = document.getElementById('col-kpi2-cheques-value');
    var cheques      = kpi2.cheques_in_pipeline;
    if (annotationEl && chequesValEl && cheques) {
      chequesValEl.textContent = fmt.formatEGP(cheques, lang);
      annotationEl.removeAttribute('hidden');
      fadeIn(annotationEl);
    }
  }

  // ── Section 3 — Expected Collections (KPI 7) ──────────────────────────────

  function renderSection3(state) {
    var forecast = state.forecast;
    if (!forecast) return;
    var s    = window.COLLECTIONS_STRINGS || {};
    var lang = s.lang || 'en';
    var fmt  = window.CollectionsFormatters;

    var BUCKETS = ['this_month', 'this_quarter', 'this_half', 'this_year'];
    for (var b = 0; b < BUCKETS.length; b++) {
      var key    = BUCKETS[b];
      var bucket = (forecast.buckets || {})[key];
      if (!bucket) continue;

      var amtEl = document.getElementById('col-forecast-' + key + '-amount');
      var subEl = document.getElementById('col-forecast-' + key + '-subtitle');
      var card  = document.getElementById('col-forecast-' + key + '-container');

      var amount          = fmt.formatEGP(bucket.amount, lang);
      var countStr        = fmt.formatCount(bucket.record_count, lang);
      var installmentsLbl = s.installments || 'installments';
      var subtitle        = '';
      if (bucket.period_end) {
        var d = new Date(bucket.period_end);
        var dateStr = d.toLocaleDateString(
          lang === 'ar' ? 'ar-EG' : 'en-GB',
          { day: 'numeric', month: 'long' }
        );
        var untilStr = (s.until || 'until {date}').replace('{date}', dateStr);
        subtitle = countStr + ' ' + installmentsLbl + ' · ' + untilStr;
      } else {
        subtitle = countStr + ' ' + installmentsLbl;
      }

      if (amtEl) { amtEl.textContent = amount; amtEl.title = fmt.formatEGP(bucket.amount, lang, { fullValue: true }); fadeIn(amtEl); }
      if (subEl) { subEl.textContent = subtitle; fadeIn(subEl); }
      if (card)  card.setAttribute('aria-label', (s[key] || key) + ': ' + amount);
    }
  }

  // ── Section 4 — Performance & Trend (KPI 4, KPI 5a/5b, KPI 6) ────────────

  function renderSection4(state) {
    var s    = window.COLLECTIONS_STRINGS || {};
    var lang = s.lang || 'en';
    var fmt  = window.CollectionsFormatters;

    // KPI 4 — Collection Rate MTD / YTD
    var kpi4 = state.rate;
    if (kpi4) {
      var mtdEl = document.getElementById('col-kpi4-mtd');
      var ytdEl = document.getElementById('col-kpi4-ytd');
      var s4    = document.getElementById('col-kpi4-subtitle');
      var c4    = document.getElementById('col-kpi4-container');

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

    // KPI 5a/5b — Per-project performance
    var kpi5a = state.perProject;
    var kpi5b = state.rateByProject;
    if (kpi5a && kpi5a.projects) {
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
        if (lateEl) { lateEl.textContent = lateAmt; lateEl.title = fmt.formatEGP(p.late_uncollected, lang, { fullValue: true }); fadeIn(lateEl); }
        if (rateEl) { rateEl.textContent = rateStr; rateEl.title = rateTip; fadeIn(rateEl); }
        if (card) {
          card.setAttribute('data-drilldown-target', 'kpi5-proj-' + p.project_id);
          card.setAttribute('aria-label', displayName + ': ' + lateAmt + ' · ' + rateStr);
        }
      }
    }

    // KPI 6 — 6-Month Collection Trend
    var kpi6 = state.trend;
    if (!kpi6 || !kpi6.months) return;

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
        onClick: function (event, elements) {
          if (!elements || !elements.length) return;
          if (elements[0].datasetIndex !== 0) return;
          var idx      = elements[0].index;
          var trendData = window.collectionsDashboard && window.collectionsDashboard.state;
          if (!trendData || !trendData.trend || !trendData.trend.months) return;
          var monthStr = trendData.trend.months[idx] && trendData.trend.months[idx].month;
          if (!monthStr) return;
          if (window.drilldownController) {
            window.drilldownController.open('trend-' + monthStr, {}, event.native && event.native.target);
          }
        },
        onHover: function (event, elements) {
          var c = event.native && event.native.target;
          if (c) c.style.cursor = elements && elements.length && elements[0].datasetIndex === 0 ? 'pointer' : 'default';
        },
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

  // ── Data-entry banner ─────────────────────────────────────────────────────

  function shouldShowDataEntryBanner(state) {
    if (new URLSearchParams(window.location.search).get('show_banner') === '1') return true;
    var kpi4 = state.rate;
    if (kpi4 && (isRateUnavailable(kpi4.mtd) || isRateUnavailable(kpi4.ytd))) return true;
    var kpi6 = state.trend;
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
      var state = {
        late:          results[0],
        portfolio:     results[1],
        perProject:    results[2],
        trend:         results[3],
        rate:          results[4],
        rateByProject: results[5],
        forecast:      results[6],
      };
      _lastFetchData = state;
      if (state.late && state.late.data_quality_warning) {
        console.warn('[Collections] KPI 2 data_quality_warning:', state.late.data_quality_warning);
      }
      if (state.forecast && state.forecast.data_quality_warning) {
        console.warn('[Collections] KPI 7 data_quality_warning:', state.forecast.data_quality_warning);
      }
      hideErrorBanner();
      updateTimestamps();
      renderSection1(state);
      renderSection2(state);
      renderSection3(state);
      renderSection4(state);
      updateDataEntryBanner(state);
      return state;
    }).catch(function (err) {
      showErrorBanner();
      throw err;
    });
  }

  function updateTimestamps() {
    var now     = new Date();
    var strings = window.COLLECTIONS_STRINGS || {};
    var asOf    = document.getElementById('col-as-of');

    _lastFetchTime = now;

    if (asOf) {
      asOf.textContent = now.toLocaleDateString(
        strings.lang === 'ar' ? 'ar-EG' : 'en-GB',
        { day: 'numeric', month: 'long', year: 'numeric' }
      );
    }
    // col-live-dot removed in Phase E (Stage 4); live indicator
    // is now the always-on .live-dot in the header pill
    // col-last-updated is owned by the heartbeat (tickHeartbeat)
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
    if (_autoRefreshInterval) return;
    _autoRefreshInterval = setInterval(fetchAllKPIs, 60000);
  }

  function stopAutoRefresh() {
    if (_autoRefreshInterval) {
      clearInterval(_autoRefreshInterval);
      _autoRefreshInterval = null;
    }
  }

  // ── Live-time heartbeat (Pillar 4) ────────────────────────────────────────

  function relativeTime(date) {
    var s    = window.COLLECTIONS_STRINGS || {};
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
    var el = document.getElementById('col-last-updated');
    if (!el) return;
    if (!_lastFetchTime) return;
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

  // ── Dark canvas activation (Pillar 1) ──────────────────────────────────────

  function evaluateCanvas() {
    var theme = localStorage.getItem('crmTheme') || 'system';
    var main  = document.querySelector('main.main-content');
    if (!main) return;
    if (theme === 'light') {
      main.classList.remove('collections-canvas-dark');
      main.style.backgroundColor = '';
      main.style.backgroundImage = '';
    } else {
      main.classList.add('collections-canvas-dark');
      // Inline style required: bg-neutral-50 utility (@layer utilities) beats
      // .collections-canvas-dark (@layer components) at equal specificity.
      main.style.backgroundColor = '#050505';
      main.style.backgroundImage =
        'radial-gradient(ellipse 800px 600px at top right, rgba(226,75,74,0.04) 0%, #050505 60%)';
    }
  }

  function activateDarkCanvas() {
    evaluateCanvas();
    // Cross-tab: storage event fires when another tab calls setTheme()
    window.addEventListener('storage', function (e) {
      if (e.key === 'crmTheme') evaluateCanvas();
    });
    // Same-tab: setTheme() → applyTheme() toggles 'dark' class on <html>
    new MutationObserver(evaluateCanvas).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class'],
    });
  }

  function init() {
    activateDarkCanvas();

    var topbarBtn = document.getElementById('refresh-btn');
    if (topbarBtn) topbarBtn.onclick = collectionsRefresh;

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        stopAutoRefresh();
        stopHeartbeat();
      } else {
        stopAutoRefresh();
        fetchAllKPIs().then(startAutoRefresh);
        startHeartbeat();
      }
    });

    window.collectionsDashboard = {
      get state() { return _lastFetchData; },
      fetchAll: fetchAllKPIs
    };
    window.collectionsDashboard.fetchAll().then(function () {
      startAutoRefresh();
      startHeartbeat();
    });
  }

  window.collectionsRefresh = function () {
    stopAutoRefresh();
    stopHeartbeat();
    fetchAllKPIs().then(function () {
      startAutoRefresh();
      startHeartbeat();
    });
  };

  document.addEventListener('DOMContentLoaded', init);
}());
