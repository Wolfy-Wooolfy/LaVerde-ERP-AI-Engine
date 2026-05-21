/**
 * test_drilldown.js — Unit tests for drilldown.js pure functions.
 * Run with: node tests/frontend/test_drilldown.js
 * Stage 6 D2.
 */

'use strict';

// Stub browser globals so the IIFE loads without a DOM
global.window = {
  COLLECTIONS_STRINGS: {},
  CollectionsFormatters: {},
  crmApi: {},
  drilldownController: null,
};
global.document = {
  readyState: 'complete',
  getElementById: function () { return null; },
  addEventListener: function () {},
};
global.requestAnimationFrame = function (cb) { cb(); };

var { _resolveEndpoint } = require('../../frontend/static/js/drilldown.js');

// ── helpers ────────────────────────────────────────────────────────────────
var passed = 0;
var failed = 0;

function assert(description, actual, expected) {
  if (actual === expected) {
    console.log('  PASS  ' + description);
    passed++;
  } else {
    console.error('  FAIL  ' + description);
    console.error('        expected: ' + JSON.stringify(expected));
    console.error('        actual:   ' + JSON.stringify(actual));
    failed++;
  }
}

// ── _resolveEndpoint — all 11 targets ──────────────────────────────────────
console.log('\n_resolveEndpoint — 11 canonical targets\n');

assert(
  'kpi1 → portfolio',
  _resolveEndpoint('kpi1'),
  '/api/v1/collections/drilldown/portfolio'
);
assert(
  'kpi2 → late',
  _resolveEndpoint('kpi2'),
  '/api/v1/collections/drilldown/late'
);
assert(
  'kpi2-cheques → late (preset filter applied by caller)',
  _resolveEndpoint('kpi2-cheques'),
  '/api/v1/collections/drilldown/late'
);
assert(
  'forecast-this_month → forecast/month',
  _resolveEndpoint('forecast-this_month'),
  '/api/v1/collections/drilldown/forecast/month'
);
assert(
  'forecast-this_quarter → forecast/quarter',
  _resolveEndpoint('forecast-this_quarter'),
  '/api/v1/collections/drilldown/forecast/quarter'
);
assert(
  'forecast-this_half → forecast/half',
  _resolveEndpoint('forecast-this_half'),
  '/api/v1/collections/drilldown/forecast/half'
);
assert(
  'forecast-this_year → forecast/year',
  _resolveEndpoint('forecast-this_year'),
  '/api/v1/collections/drilldown/forecast/year'
);
assert(
  'kpi5-proj-1 → project/1',
  _resolveEndpoint('kpi5-proj-1'),
  '/api/v1/collections/drilldown/project/1'
);
assert(
  'kpi5-proj-2 → project/2',
  _resolveEndpoint('kpi5-proj-2'),
  '/api/v1/collections/drilldown/project/2'
);
assert(
  'kpi5-proj-3 → project/3',
  _resolveEndpoint('kpi5-proj-3'),
  '/api/v1/collections/drilldown/project/3'
);
assert(
  'trend-2025-12 → trend/2025-12',
  _resolveEndpoint('trend-2025-12'),
  '/api/v1/collections/drilldown/trend/2025-12'
);

// ── edge cases ──────────────────────────────────────────────────────────────
console.log('\n_resolveEndpoint — edge / invalid targets\n');

assert(
  'null target → null',
  _resolveEndpoint(null),
  null
);
assert(
  'empty string → null',
  _resolveEndpoint(''),
  null
);
assert(
  'unknown target → null',
  _resolveEndpoint('kpi99'),
  null
);
assert(
  'trend-2026-01 → trend/2026-01',
  _resolveEndpoint('trend-2026-01'),
  '/api/v1/collections/drilldown/trend/2026-01'
);

// ── summary ────────────────────────────────────────────────────────────────
console.log('\n─────────────────────────────────────────────');
console.log('Results: ' + passed + ' passed, ' + failed + ' failed');
if (failed > 0) {
  process.exit(1);
}
