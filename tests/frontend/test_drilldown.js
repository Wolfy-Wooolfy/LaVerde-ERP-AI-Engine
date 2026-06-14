/**
 * test_drilldown.js — Unit tests for drilldown.js pure functions.
 * Run with: node tests/frontend/test_drilldown.js
 * Stage 6 D2+D5.
 */

'use strict';

// Stub browser globals so the IIFE loads without a DOM
global.window = {
  COLLECTIONS_STRINGS: {},
  CollectionsFormatters: {},
  crmApi: {},
  drilldownController: null,
  location: { hash: '', pathname: '/', search: '' },
};
global.document = {
  readyState: 'complete',
  getElementById: function () { return null; },
  addEventListener: function () {},
};
global.requestAnimationFrame = function (cb) { cb(); };
global.history = { replaceState: function () {} };

var { _resolveEndpoint, _parseForecastTarget, _buildHash, _parseHash, _paymentStateChipVals } = require('../../frontend/static/js/drilldown.js');

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
  'forecast-this_month-cleared → forecast/this_month/cleared (N5)',
  _resolveEndpoint('forecast-this_month-cleared'),
  '/api/v1/collections/drilldown/forecast/this_month/cleared'
);
assert(
  'forecast-this_quarter-pending → forecast/this_quarter/pending (N5)',
  _resolveEndpoint('forecast-this_quarter-pending'),
  '/api/v1/collections/drilldown/forecast/this_quarter/pending'
);
assert(
  'forecast-this_half-remaining → forecast/this_half/remaining (N5)',
  _resolveEndpoint('forecast-this_half-remaining'),
  '/api/v1/collections/drilldown/forecast/this_half/remaining'
);
assert(
  'forecast-this_year-cleared → forecast/this_year/cleared (N5)',
  _resolveEndpoint('forecast-this_year-cleared'),
  '/api/v1/collections/drilldown/forecast/this_year/cleared'
);
assert(
  'legacy forecast target without segment → null (N5 removed v1)',
  _resolveEndpoint('forecast-this_month'),
  null
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

// ── _parseForecastTarget — bucket/segment split on the LAST dash (N5) ────────
console.log('\n_parseForecastTarget — bucket/segment split\n');

assert(
  'bucket has underscore, segment after last dash',
  JSON.stringify(_parseForecastTarget('forecast-this_month-cleared')),
  JSON.stringify({ bucket: 'this_month', segment: 'cleared' })
);
assert(
  'this_year + remaining',
  JSON.stringify(_parseForecastTarget('forecast-this_year-remaining')),
  JSON.stringify({ bucket: 'this_year', segment: 'remaining' })
);
assert(
  'no segment → null',
  _parseForecastTarget('forecast-this_month'),
  null
);
assert(
  'non-forecast target → null',
  _parseForecastTarget('kpi2'),
  null
);

// ── _buildHash / _parseHash round-trips ────────────────────────────────────
console.log('\n_buildHash — encoding\n');

assert(
  'defaults are omitted from hash',
  _buildHash('kpi2', { payment_state: 'all', sort_by: 'date', sort_dir: 'desc', has_pending_cheque: false }),
  '#dd=kpi2'
);
assert(
  'payment_state unpaid included (API-accepted value, not not_paid)',
  _buildHash('kpi2', { payment_state: 'unpaid', sort_by: 'date', sort_dir: 'desc' }),
  '#dd=kpi2&st=unpaid'
);
assert(
  'sort_by amount + sort_dir asc included',
  _buildHash('kpi2', { sort_by: 'amount', sort_dir: 'asc' }),
  '#dd=kpi2&sb=amount&sd=asc'
);
assert(
  'has_pending_cheque=true → pc=1',
  _buildHash('kpi2-cheques', { has_pending_cheque: true }),
  '#dd=kpi2-cheques&pc=1'
);
assert(
  'forecast segment target preserved (N5)',
  _buildHash('forecast-this_month-cleared', {}),
  '#dd=forecast-this_month-cleared'
);
assert(
  'null target → empty string',
  _buildHash(null, {}),
  ''
);

console.log('\n_parseHash — decoding\n');

assert(
  'minimal hash → defaults restored',
  JSON.stringify(_parseHash('#dd=kpi2')),
  JSON.stringify({ target: 'kpi2', filters: { payment_state: 'all', sort_by: 'date', sort_dir: 'desc', has_pending_cheque: false } })
);
assert(
  'payment_state unpaid decoded (API-accepted value)',
  _parseHash('#dd=kpi2&st=unpaid').filters.payment_state,
  'unpaid'
);
assert(
  'payment_state partial decoded',
  _parseHash('#dd=kpi2&st=partial').filters.payment_state,
  'partial'
);
assert(
  'pc=1 decoded as has_pending_cheque true',
  _parseHash('#dd=kpi2-cheques&pc=1').filters.has_pending_cheque,
  true
);
assert(
  'pc omitted decoded as false',
  _parseHash('#dd=kpi2').filters.has_pending_cheque,
  false
);
assert(
  'no #dd param → null',
  _parseHash('#foo=bar'),
  null
);
assert(
  'empty hash → null',
  _parseHash(''),
  null
);
assert(
  'no hash → null',
  _parseHash(null),
  null
);

console.log('\n_buildHash + _parseHash round-trip\n');

var targets = ['kpi1', 'kpi2', 'kpi2-cheques', 'forecast-this_quarter-pending', 'kpi5-proj-2', 'trend-2025-11'];
targets.forEach(function (t) {
  var filters = { payment_state: 'unpaid', sort_by: 'amount', sort_dir: 'asc', has_pending_cheque: false };
  var hash   = _buildHash(t, filters);
  var parsed = _parseHash(hash);
  assert(
    'round-trip target: ' + t,
    parsed && parsed.target,
    t
  );
  assert(
    'round-trip payment_state API value: ' + t,
    parsed && parsed.filters.payment_state,
    'unpaid'
  );
  assert(
    'round-trip sort_by: ' + t,
    parsed && parsed.filters.sort_by,
    'amount'
  );
});

// ── _paymentStateChipVals — no paid chip (Decision 15.15) ─────────────────
console.log('\n_paymentStateChipVals — no paid chip (all 4 endpoints return 422 for paid)\n');

assert(
  'chip set has exactly 3 entries (all / unpaid / partial)',
  _paymentStateChipVals().length,
  3
);
assert(
  '"paid" absent — sending paid to any drill-down endpoint returns 422',
  _paymentStateChipVals().indexOf('paid'),
  -1
);
assert(
  '"all" present',
  _paymentStateChipVals().indexOf('all') >= 0,
  true
);
assert(
  '"unpaid" present (API-accepted value)',
  _paymentStateChipVals().indexOf('unpaid') >= 0,
  true
);
assert(
  '"partial" present',
  _paymentStateChipVals().indexOf('partial') >= 0,
  true
);

// ── summary ────────────────────────────────────────────────────────────────
console.log('\n─────────────────────────────────────────────');
console.log('Results: ' + passed + ' passed, ' + failed + ' failed');
if (failed > 0) {
  process.exit(1);
}
