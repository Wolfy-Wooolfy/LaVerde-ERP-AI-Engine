/**
 * charts.js — Chart.js initialization for the CRM dashboard
 * Reads window.CRM_DATA injected by dashboard.html
 */

'use strict';

// ── Color helpers ─────────────────────────────────────────────────────────────
function isDark() {
  return document.documentElement.classList.contains('dark');
}

function palette() {
  return {
    text:       isDark() ? '#a3a3a3' : '#737373',
    gridLines:  isDark() ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.06)',
    bg:         isDark() ? '#262626' : '#ffffff',
    danger:     ['#f43f5e', '#fb7185', '#fda4af', '#fecdd3'],
    warning:    ['#f59e0b', '#fbbf24', '#fcd34d', '#fde68a'],
    primary:    ['#6366f1', '#818cf8', '#a5b4fc', '#c7d2fe'],
    success:    ['#10b981', '#34d399', '#6ee7b7', '#a7f3d0'],
    mixed:      ['#6366f1','#f43f5e','#f59e0b','#10b981','#3b82f6','#8b5cf6','#ec4899','#14b8a6','#f97316','#84cc16'],
  };
}

// ── Global chart defaults ─────────────────────────────────────────────────────
function applyChartDefaults() {
  Chart.defaults.font.family = 'Inter, ui-sans-serif, sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = palette().text;
  Chart.defaults.plugins.legend.labels.color = palette().text;
  Chart.defaults.plugins.tooltip.backgroundColor = isDark() ? '#1a1a1a' : '#ffffff';
  Chart.defaults.plugins.tooltip.titleColor = isDark() ? '#f5f5f5' : '#171717';
  Chart.defaults.plugins.tooltip.bodyColor  = isDark() ? '#a3a3a3' : '#525252';
  Chart.defaults.plugins.tooltip.borderColor = isDark() ? '#404040' : '#e5e5e5';
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
  Chart.defaults.plugins.tooltip.padding = 10;
}

// ── Chart registry (for theme updates) ───────────────────────────────────────
const _charts = {};

function registerChart(id, chart) {
  _charts[id] = chart;
}

window.reinitCharts = function() {
  applyChartDefaults();
  for (const [id, chart] of Object.entries(_charts)) {
    chart.options.scales && updateScaleColors(chart);
    chart.update('none');
  }
};

function updateScaleColors(chart) {
  const p = palette();
  for (const scale of Object.values(chart.options.scales || {})) {
    if (scale.ticks) scale.ticks.color = p.text;
    if (scale.grid)  scale.grid.color  = p.gridLines;
  }
}

// ── Activity distribution donut ───────────────────────────────────────────────
function initActivityChart() {
  const canvas = document.getElementById('activityChart');
  if (!canvas || !window.CRM_DATA) return;

  const d = window.CRM_DATA.activity;
  const total = d.overdue + d.today + d.planned + d.none;
  if (total === 0) return;

  const p = palette();
  const chart = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: ['Overdue', 'Today', 'Planned', 'No Activity'],
      datasets: [{
        data: [d.overdue, d.today, d.planned, d.none],
        backgroundColor: [p.danger[0], p.success[0], p.primary[0], p.text],
        borderWidth: 0,
        hoverOffset: 6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '68%',
      plugins: {
        legend: {
          position: 'right',
          labels: {
            padding: 12,
            usePointStyle: true,
            pointStyleWidth: 8,
          },
        },
        tooltip: {
          callbacks: {
            label(ctx) {
              const pct = ((ctx.parsed / total) * 100).toFixed(1);
              return ` ${ctx.label}: ${ctx.parsed.toLocaleString()} (${pct}%)`;
            },
          },
        },
      },
    },
    plugins: [{
      // Center text
      id: 'centerText',
      beforeDraw(chart) {
        const { ctx, chartArea: { width, height, top, left } } = chart;
        ctx.save();
        ctx.font = `bold 22px Inter, sans-serif`;
        ctx.fillStyle = isDark() ? '#f5f5f5' : '#171717';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(total.toLocaleString(), left + width / 2, top + height / 2 - 8);
        ctx.font = `11px Inter, sans-serif`;
        ctx.fillStyle = isDark() ? '#a3a3a3' : '#737373';
        ctx.fillText('Total', left + width / 2, top + height / 2 + 14);
        ctx.restore();
      },
    }],
  });
  registerChart('activityChart', chart);
}

// ── Top salespeople horizontal bar ────────────────────────────────────────────
function initSalespersonChart() {
  const canvas = document.getElementById('salespersonChart');
  if (!canvas || !window.CRM_DATA) return;

  const data = [...(window.CRM_DATA.bySalesperson || [])]
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  if (data.length === 0) return;

  const p = palette();
  const chart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: data.map(d => d.name),
      datasets: [{
        label: 'Overdue',
        data: data.map(d => d.count),
        backgroundColor: data.map((_, i) => {
          const intensity = 1 - (i / data.length) * 0.5;
          return `rgba(244, 63, 94, ${intensity})`;
        }),
        borderRadius: 4,
        borderSkipped: false,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
      },
      scales: {
        x: {
          grid: { color: p.gridLines },
          ticks: { color: p.text, precision: 0 },
          beginAtZero: true,
        },
        y: {
          grid: { display: false },
          ticks: {
            color: p.text,
            callback(val) {
              const label = this.getLabelForValue(val);
              return label.length > 16 ? label.slice(0, 14) + '…' : label;
            },
          },
        },
      },
    },
  });
  registerChart('salespersonChart', chart);
}

// ── Stage distribution vertical bar ──────────────────────────────────────────
function initStageChart() {
  const canvas = document.getElementById('stageChart');
  if (!canvas || !window.CRM_DATA) return;

  const data = [...(window.CRM_DATA.byStage || [])]
    .sort((a, b) => b.count - a.count);

  if (data.length === 0) return;

  const p = palette();
  const chart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: data.map(d => d.name),
      datasets: [{
        label: 'Overdue',
        data: data.map(d => d.count),
        backgroundColor: p.mixed,
        borderRadius: 4,
        borderSkipped: false,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: {
            color: p.text,
            maxRotation: 35,
            callback(val) {
              const label = this.getLabelForValue(val);
              return label.length > 10 ? label.slice(0, 9) + '…' : label;
            },
          },
        },
        y: {
          grid: { color: p.gridLines },
          ticks: { color: p.text, precision: 0 },
          beginAtZero: true,
        },
      },
    },
  });
  registerChart('stageChart', chart);
}

// ── Sparklines ────────────────────────────────────────────────────────────────
window.loadAllSparklines = async function() {
  const canvases = document.querySelectorAll('[data-sparkline]');
  if (!canvases.length) return;

  const metrics = [...new Set(Array.from(canvases).map(c => c.dataset.sparkline))];

  await Promise.all(metrics.map(async (metric) => {
    try {
      const data = await crmApi.get(`/api/v1/dashboard/sparkline?metric=${metric}&days=7`);
      if (!data.ok) return;

      // Update trend badge
      const trendEl = document.querySelector(`[data-kpi-trend="${metric}"]`);
      if (trendEl && data.trend) {
        const isPositive = data.trend.startsWith('+');
        const isNeutral  = data.trend === '0%';
        trendEl.textContent = data.trend;
        trendEl.className = `text-xs font-mono font-medium ${
          isNeutral ? 'text-neutral-400' :
          isPositive ? 'text-danger-500' : 'text-success-500'
        }`;
      }

      // Draw sparkline on each matching canvas
      canvases.forEach(canvas => {
        if (canvas.dataset.sparkline !== metric) return;
        drawSparkline(canvas, data.data, metric);
      });
    } catch (e) {
      // Sparkline is non-critical — fail silently
    }
  }));
};

function drawSparkline(canvas, values, metric) {
  const isDanger = ['critical', 'overdue', 'missing_contact', 'data_quality'].includes(metric);
  const color = isDanger ? '#f43f5e' : '#6366f1';

  const existing = Chart.getChart(canvas);
  if (existing) existing.destroy();

  new Chart(canvas, {
    type: 'line',
    data: {
      labels: values.map((_, i) => i),
      datasets: [{
        data: values,
        borderColor: color,
        borderWidth: 1.5,
        pointRadius: 0,
        fill: true,
        backgroundColor: color + '18',
        tension: 0.4,
      }],
    },
    options: {
      responsive: false,
      animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false },
        y: { display: false },
      },
    },
  });
}

// ── Entry point ───────────────────────────────────────────────────────────────
window.initDashboardCharts = function() {
  applyChartDefaults();
  initActivityChart();
  initSalespersonChart();
  initStageChart();
};
