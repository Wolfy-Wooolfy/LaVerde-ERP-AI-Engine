/**
 * test_refresh_url.js — Behavioural tests for the crmWithRefresh URL seam.
 * Run with: node tests/frontend/test_refresh_url.js
 *
 * tests/unit/core/test_refresh_wiring.py pins the IMPLEMENTATION properties of
 * this helper (uses URLSearchParams.set, never append, never string
 * concatenation, guards on `manual`) because the pytest gate has no node
 * bridge. This file proves the BEHAVIOUR those properties are supposed to buy:
 * that real URLs carrying real query strings survive the round trip, and that
 * applying the helper twice still yields exactly one refresh=1.
 */

'use strict';

// api.js is a browser script: it assigns onto `window` and reads
// window.location.origin to resolve relative URLs. Stub just those two.
global.window = { location: { origin: 'http://localhost:8000' } };

require('../../frontend/static/js/api.js');

var withRefresh = global.window.crmWithRefresh;

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

// ── 1. the manual gate ─────────────────────────────────────────────────────
console.log('\ncrmWithRefresh — manual gate');
assert('manual=false leaves the URL untouched',
  withRefresh('/api/v1/dashboard/kpis', false), '/api/v1/dashboard/kpis');
assert('manual omitted leaves the URL untouched',
  withRefresh('/api/v1/dashboard/kpis'), '/api/v1/dashboard/kpis');
assert('manual=true appends refresh=1',
  withRefresh('/api/v1/dashboard/kpis', true), '/api/v1/dashboard/kpis?refresh=1');
assert('an already-parameterised URL is untouched when manual=false',
  withRefresh('/hr/dashboard?window=90', false), '/hr/dashboard?window=90');

// ── 2. existing query parameters survive ───────────────────────────────────
// These are the real ones the SSR routes declare (dashboard.py:222-224,
// :318-320, :364-366, :452-455, :652). Losing one would silently reset the
// page's filters on every manual refresh.
console.log('\ncrmWithRefresh — preserves existing query parameters');
assert('single param preserved',
  withRefresh('/hr/dashboard?window=90', true), '/hr/dashboard?window=90&refresh=1');
assert('tab preserved',
  withRefresh('/data-quality?tab=phone', true), '/data-quality?tab=phone&refresh=1');
assert('months + campaign_id preserved',
  withRefresh('/campaign-performance/timeline?campaign_id=7&months=6', true),
  '/campaign-performance/timeline?campaign_id=7&months=6&refresh=1');
assert('start_month + end_month + window preserved',
  withRefresh('/marketing-attribution/dashboard?window=custom&start_month=2026-01&end_month=2026-06', true),
  '/marketing-attribution/dashboard?window=custom&start_month=2026-01&end_month=2026-06&refresh=1');
assert('a path parameter in the path is untouched',
  withRefresh('/marketing-attribution/buyer/42/timeline?months=3', true),
  '/marketing-attribution/buyer/42/timeline?months=3&refresh=1');

// ── 3. idempotency ─────────────────────────────────────────────────────────
// The reload lands on a URL that already carries refresh=1. If the user hits
// Refresh again before the strip-on-load runs, or the strip is ever removed,
// the helper must not stack duplicates.
console.log('\ncrmWithRefresh — idempotent');
assert('applying twice yields one refresh=1',
  withRefresh(withRefresh('/hr/dashboard?window=90', true), true),
  '/hr/dashboard?window=90&refresh=1');
assert('applying three times yields one refresh=1',
  withRefresh(withRefresh(withRefresh('/data-quality', true), true), true),
  '/data-quality?refresh=1');
assert('an existing refresh=0 is replaced, not duplicated',
  withRefresh('/hr/dashboard?refresh=0&window=90', true),
  '/hr/dashboard?refresh=1&window=90');

// ── 4. absolute URLs (the SSR reload feeds location.href) ──────────────────
console.log('\ncrmWithRefresh — absolute URLs stay absolute');
assert('absolute URL keeps its origin',
  withRefresh('http://localhost:8000/hr/dashboard?window=90', true),
  'http://localhost:8000/hr/dashboard?window=90&refresh=1');
assert('absolute URL with no query string',
  withRefresh('http://localhost:8000/data-quality', true),
  'http://localhost:8000/data-quality?refresh=1');
assert('absolute URL applied twice is still idempotent',
  withRefresh(withRefresh('http://localhost:8000/data-quality?tab=stage', true), true),
  'http://localhost:8000/data-quality?tab=stage&refresh=1');

// ── 5. the fragment survives ───────────────────────────────────────────────
// drilldown.js encodes panel state in location.hash; a reload that dropped it
// would close the user's open drilldown on every refresh.
console.log('\ncrmWithRefresh — fragment preserved');
assert('hash is kept after the query string',
  withRefresh('/collections/dashboard?window=90#dd=late', true),
  '/collections/dashboard?window=90&refresh=1#dd=late');

// ── summary ────────────────────────────────────────────────────────────────
console.log('\n' + passed + ' passed, ' + failed + ' failed');
process.exit(failed === 0 ? 0 : 1);
