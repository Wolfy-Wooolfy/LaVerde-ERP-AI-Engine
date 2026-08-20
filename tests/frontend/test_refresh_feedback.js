/**
 * test_refresh_feedback.js — Behavioural tests for window.crmRefreshFeedback.
 * Run with: node tests/frontend/test_refresh_feedback.js
 *
 * The affordance exists because a manual refresh carries ?refresh=1 and so goes
 * to Odoo instead of the cache: ~20ms became 3401ms on /collections/dashboard,
 * with nothing on screen moving. tests/unit/core/test_refresh_wiring.py pins
 * WHERE it is invoked (manual paths only); this file proves WHAT it does.
 */

'use strict';

// ── Minimal DOM ────────────────────────────────────────────────────────────
// app.js runs real top-level code on load. It needs document.addEventListener
// (Alpine init + the keyboard shortcut) and window.matchMedia (the
// reduced-motion check). window.history is left UNDEFINED on purpose: the
// stripRefreshParam IIFE tests `if (!window.history || ...) return;` first, so
// it bails immediately and this stub needs no URL or location.href.

function makeClassList() {
  var classes = {};
  return {
    add: function (c) { classes[c] = true; },
    remove: function (c) { delete classes[c]; },
    contains: function (c) { return classes[c] === true; },
    toggle: function (c, on) { if (on) { classes[c] = true; } else { delete classes[c]; } },
  };
}

function makeEl(id) {
  return { id: id, style: {}, classList: makeClassList() };
}

var elements = {};

global.document = {
  documentElement: { classList: makeClassList(), style: {} },
  head: { appendChild: function () {} },
  addEventListener: function () {},
  createElement: function () { return makeEl('created'); },
  getElementById: function (id) { return elements[id] || null; },
};

global.window = {
  matchMedia: function () { return { matches: false, addEventListener: function () {} }; },
};

require('../../frontend/static/js/app.js');

var feedback = global.window.crmRefreshFeedback;

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

function withElements() {
  elements = { 'loading-bar': makeEl('loading-bar'), 'refresh-icon': makeEl('refresh-icon') };
  return elements;
}

function withoutElements() {
  elements = {};
}

// ── 0. the helper is reachable at all ──────────────────────────────────────
console.log('\ncrmRefreshFeedback — exposure');
assert('exposed on window', typeof feedback, 'object');
assert('start is a function', typeof feedback.start, 'function');
assert('stop is a function', typeof feedback.stop, 'function');
// loadingBar is a top-level `const` in app.js — a script-scoped lexical
// binding, NOT a window property. The helper must reach it by closure.
assert('loadingBar is NOT on window', typeof global.window.loadingBar, 'undefined');

// ── 1. start shows the bar and spins the icon ──────────────────────────────
console.log('\ncrmRefreshFeedback.start');
var els = withElements();
feedback.start();
assert('loading bar becomes visible', els['loading-bar'].style.opacity, '1');
assert('loading bar advances to 70%', els['loading-bar'].style.transform, 'scaleX(0.7)');
assert('refresh icon gains animate-spin', els['refresh-icon'].classList.contains('animate-spin'), true);

// ── 2. stop clears both ────────────────────────────────────────────────────
console.log('\ncrmRefreshFeedback.stop');
feedback.stop();
assert('loading bar completes to 100%', els['loading-bar'].style.transform, 'scaleX(1)');
assert('refresh icon loses animate-spin', els['refresh-icon'].classList.contains('animate-spin'), false);

// ── 3. stop is safe when the elements are absent ───────────────────────────
// The topbar #refresh-icon and #loading-bar live in base.html, but these
// scripts also run on pages mid-render and in contexts where either may be
// missing. A throw here would abort the caller's .then() and take the timer
// restart down with it.
console.log('\ncrmRefreshFeedback — missing elements');
withoutElements();
var startThrew = false;
try { feedback.start(); } catch (e) { startThrew = true; }
assert('start does not throw with no elements', startThrew, false);
var stopThrew = false;
try { feedback.stop(); } catch (e) { stopThrew = true; }
assert('stop does not throw with no elements', stopThrew, false);

// A stop with no preceding start must also be harmless — the failure path can
// reach stop() before anything ever started.
withElements();
var loneStopThrew = false;
try { feedback.stop(); } catch (e) { loneStopThrew = true; }
assert('stop without a prior start does not throw', loneStopThrew, false);
assert('stop without a start leaves no spin class',
  elements['refresh-icon'].classList.contains('animate-spin'), false);

// ── 4. stop after start is idempotent ──────────────────────────────────────
// Two rapid clicks, or a stop from both the .then and a future error path,
// must not leave the icon spinning or the bar stuck.
console.log('\ncrmRefreshFeedback — idempotency');
var els4 = withElements();
feedback.start();
feedback.stop();
var afterOne = {
  transform: els4['loading-bar'].style.transform,
  spinning: els4['refresh-icon'].classList.contains('animate-spin'),
};
feedback.stop();
assert('second stop leaves the same transform', els4['loading-bar'].style.transform, afterOne.transform);
assert('second stop leaves the icon still not spinning',
  els4['refresh-icon'].classList.contains('animate-spin'), false);
assert('icon was already not spinning after the first stop', afterOne.spinning, false);

// Repeated starts must not stack either.
feedback.start();
feedback.start();
feedback.stop();
assert('start twice then stop once still clears the spin',
  els4['refresh-icon'].classList.contains('animate-spin'), false);

// ── 5. the deferred fade-out ───────────────────────────────────────────────
// loadingBar.complete() schedules the fade 300ms later. Assert the end state
// really is reached, so "stop" means the bar is gone and not merely full.
console.log('\ncrmRefreshFeedback — deferred fade-out (300ms)');
var els5 = withElements();
feedback.start();
feedback.stop();

setTimeout(function () {
  assert('loading bar fades to invisible', els5['loading-bar'].style.opacity, '0');
  assert('loading bar retracts to 0%', els5['loading-bar'].style.transform, 'scaleX(0)');

  console.log('\n' + passed + ' passed, ' + failed + ' failed');
  process.exit(failed === 0 ? 0 : 1);
}, 350);
