/**
 * test_projects_inventory_drill.js — unit tests for projects_inventory_drill.js
 * pure functions. Run with: node tests/frontend/test_projects_inventory_drill.js
 * Slice 1b.
 */

'use strict';

// Stub browser globals so the IIFE loads without a DOM.
global.window = { PROJINV_STRINGS: {}, crmApi: {}, piDrill: null };
global.document = {
  readyState: 'complete',
  getElementById: function () { return null; },
  addEventListener: function () {},
};
global.requestAnimationFrame = function (cb) { cb(); };

var {
  _buildUrl, _childLevelOf, _isLeafLevel, _headingKeyOf, _pushNode, _popToIndex,
} = require('../../frontend/static/js/projects_inventory_drill.js');

// ── helpers ──────────────────────────────────────────────────────────────────
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

// ── _buildUrl — endpoint builder ─────────────────────────────────────────────
console.log('\n_buildUrl — drill endpoint\n');

assert('project/1', _buildUrl('project', 1), '/api/v1/projects-inventory/drill/project/1');
assert('phase/10', _buildUrl('phase', 10), '/api/v1/projects-inventory/drill/phase/10');
assert('zone/20', _buildUrl('zone', 20), '/api/v1/projects-inventory/drill/zone/20');
assert('building/30', _buildUrl('building', 30), '/api/v1/projects-inventory/drill/building/30');

// ── _childLevelOf — level → child level ──────────────────────────────────────
console.log('\n_childLevelOf — child level mapping\n');

assert('project → phase', _childLevelOf('project'), 'phase');
assert('phase → zone', _childLevelOf('phase'), 'zone');
assert('zone → building', _childLevelOf('zone'), 'building');
assert('building → unit', _childLevelOf('building'), 'unit');
assert('unknown → null', _childLevelOf('street'), null);

// ── _isLeafLevel — only building is the leaf ─────────────────────────────────
console.log('\n_isLeafLevel — building is the leaf\n');

assert('building is leaf', _isLeafLevel('building'), true);
assert('project not leaf', _isLeafLevel('project'), false);
assert('phase not leaf', _isLeafLevel('phase'), false);
assert('zone not leaf', _isLeafLevel('zone'), false);

// ── _headingKeyOf — child level → strings key ────────────────────────────────
console.log('\n_headingKeyOf — heading strings key\n');

assert('phase → dd_phases', _headingKeyOf('phase'), 'dd_phases');
assert('zone → dd_zones', _headingKeyOf('zone'), 'dd_zones');
assert('building → dd_buildings', _headingKeyOf('building'), 'dd_buildings');
assert('unit → dd_units', _headingKeyOf('unit'), 'dd_units');
assert('unknown → null', _headingKeyOf('nope'), null);

// ── nav stack: push ──────────────────────────────────────────────────────────
console.log('\n_pushNode — push deeper level (immutable)\n');

var base = [{ level: 'project', id: 1, name: 'NC' }];
var pushed = _pushNode(base, { level: 'phase', id: 10, name: 'P1' });
assert('push grows the stack', pushed.length, 2);
assert('pushed top level', pushed[1].level, 'phase');
assert('pushed top id', pushed[1].id, 10);
assert('original stack unchanged (immutable)', base.length, 1);

// ── nav stack: pop to a breadcrumb index ─────────────────────────────────────
console.log('\n_popToIndex — pop to a breadcrumb crumb (immutable)\n');

var deep = [
  { level: 'project', id: 1, name: 'NC' },
  { level: 'phase', id: 10, name: 'P1' },
  { level: 'zone', id: 20, name: 'Z1' },
  { level: 'building', id: 30, name: 'B1' },
];
var popped = _popToIndex(deep, 1);   // pop back to the phase crumb
assert('pop keeps idx+1 entries', popped.length, 2);
assert('pop top is the phase', popped[1].level, 'phase');
assert('pop to root keeps one', _popToIndex(deep, 0).length, 1);
assert('pop to last is a no-op length', _popToIndex(deep, 3).length, 4);
assert('original deep stack unchanged (immutable)', deep.length, 4);

// ── summary ──────────────────────────────────────────────────────────────────
console.log('\n─────────────────────────────────────────────');
console.log('Results: ' + passed + ' passed, ' + failed + ' failed');
if (failed > 0) {
  process.exit(1);
}
