/**
 * projects_inventory_drill.js — Projects Inventory hierarchy drill-down panel (Slice 1b).
 *
 * Public API: window.piDrill.open(level, id, name, triggerEl)
 *             window.piDrill.close()
 *             window.piDrill.state   (read-only snapshot)
 *
 * READ-ONLY: only GETs /api/v1/projects-inventory/drill/{level}/{id}; never writes.
 * Walks Project → Phase → Zone → Building → Unit in ONE panel via an in-panel nav
 * stack: each child row is a <button> that pushes a level and re-fetches; breadcrumb
 * crumbs pop to that level; the back affordance pops one level; leaf unit rows are
 * non-interactive. Mirrors the Collections drilldown.js panel behaviour (desktop side
 * panel ≥lg / full-screen <lg, RTL-aware, #app inert, Esc-to-close, focus trap).
 *
 * i18n: all strings from window.PROJINV_STRINGS (injected server-side, | tojson). No
 * cross-module string sharing. English fallbacks keep it functional in standalone tests.
 */
(function () {
  'use strict';

  var S = (typeof window !== 'undefined' && window.PROJINV_STRINGS) || {};

  // ── Level maps (pure) ───────────────────────────────────────────────────────
  // The child level each level produces (building ⇒ the unit leaf).
  var _CHILD_LEVEL = {
    project: 'phase',
    phase: 'zone',
    zone: 'building',
    building: 'unit',
  };

  function _childLevelOf(level) {
    return _CHILD_LEVEL[level] || null;
  }

  function _isLeafLevel(level) {
    return level === 'building';
  }

  // The plural-heading strings key for a child level (drives the list heading).
  function _headingKeyOf(childLevel) {
    return {
      phase: 'dd_phases',
      zone: 'dd_zones',
      building: 'dd_buildings',
      unit: 'dd_units',
    }[childLevel] || null;
  }

  function _buildUrl(level, id) {
    return '/api/v1/projects-inventory/drill/' + level + '/' + id;
  }

  // Nav-stack transforms (pure — exported for unit tests).
  function _pushNode(stack, node) {
    return stack.concat([node]);
  }

  function _popToIndex(stack, idx) {
    return stack.slice(0, idx + 1);
  }

  // ── State ──────────────────────────────────────────────────────────────────
  var _state = {
    stack: [],          // [{level, id, name}]; the last entry is the current scope
    isLoading: false,
    triggerEl: null,
  };

  function _current() {
    return _state.stack.length ? _state.stack[_state.stack.length - 1] : null;
  }

  // ── DOM refs ───────────────────────────────────────────────────────────────
  var _panel, _backdrop, _crumbs, _rootCrumb, _backBtn, _closeBtn,
      _scope, _childHeading, _loading, _rows, _units, _empty, _error;

  function _initRefs() {
    _panel        = document.getElementById('pi-dd-panel');
    _backdrop     = document.getElementById('pi-dd-backdrop');
    _crumbs       = document.getElementById('pi-dd-crumbs');
    _rootCrumb    = document.getElementById('pi-dd-root-crumb');
    _backBtn      = document.getElementById('pi-dd-back-btn');
    _closeBtn     = document.getElementById('pi-dd-close-btn');
    _scope        = document.getElementById('pi-dd-scope');
    _childHeading = document.getElementById('pi-dd-child-heading');
    _loading      = document.getElementById('pi-dd-loading');
    _rows         = document.getElementById('pi-dd-rows');
    _units        = document.getElementById('pi-dd-units');
    _empty        = document.getElementById('pi-dd-empty');
    _error        = document.getElementById('pi-dd-error');
    return !!_panel;
  }

  // ── Panel open / close ───────────────────────────────────────────────────────
  function open(level, id, name, triggerEl) {
    if (!_panel && !_initRefs()) {
      console.warn('[piDrill] panel DOM not ready');
      return;
    }
    if (!_CHILD_LEVEL[level]) {
      console.warn('[piDrill] unknown level:', level);
      return;
    }
    _state.stack     = [{ level: level, id: Number(id), name: name || '' }];
    _state.triggerEl = triggerEl || document.activeElement;

    _backdrop.classList.remove('hidden');
    _panel.removeAttribute('hidden');
    var rtl = document.documentElement.dir === 'rtl';
    _panel.classList.add(rtl ? '-translate-x-full' : 'translate-x-full');
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        _panel.classList.remove('-translate-x-full', 'translate-x-full');
        _panel.classList.add('translate-x-0');
      });
    });
    _setMainInert(true);
    _panel.focus();

    _fetch();
  }

  function close() {
    if (!_panel) return;
    var rtl = document.documentElement.dir === 'rtl';
    _panel.classList.remove('translate-x-0');
    _panel.classList.add(rtl ? '-translate-x-full' : 'translate-x-full');

    function _onEnd() {
      _panel.removeEventListener('transitionend', _onEnd);
      _panel.setAttribute('hidden', '');
      _backdrop.classList.add('hidden');
      _panel.classList.remove('-translate-x-full', 'translate-x-full');
    }
    _panel.addEventListener('transitionend', _onEnd);

    _setMainInert(false);
    if (_state.triggerEl && _state.triggerEl.focus) {
      _state.triggerEl.focus();
    }
    _state.stack     = [];
    _state.triggerEl = null;
  }

  // Drill one level deeper (push) and re-fetch.
  function _drillInto(node) {
    _state.stack = _pushNode(_state.stack, node);
    _fetch();
  }

  // Pop the stack to a breadcrumb index and re-fetch.
  function _popTo(idx) {
    if (idx < 0 || idx >= _state.stack.length - 1) return;  // no-op for current/invalid
    _state.stack = _popToIndex(_state.stack, idx);
    _fetch();
  }

  function _back() {
    if (_state.stack.length <= 1) { close(); return; }
    _state.stack = _state.stack.slice(0, -1);
    _fetch();
  }

  // ── Fetch ────────────────────────────────────────────────────────────────────
  function _fetch() {
    var cur = _current();
    if (!cur || _state.isLoading) return;
    _state.isLoading = true;

    _renderCrumbs();
    _hide(_scope); _hide(_childHeading); _hide(_empty); _hide(_error);
    _rows.innerHTML = ''; _units.innerHTML = '';
    _loading.classList.remove('hidden');

    var reqLevel = cur.level, reqId = cur.id;

    window.crmApi.get(_buildUrl(reqLevel, reqId)).then(function (data) {
      _state.isLoading = false;
      // Stale-response guard: discard if the user navigated while in flight.
      var now = _current();
      if (!now || now.level !== reqLevel || now.id !== reqId) return;
      _loading.classList.add('hidden');
      // Adopt the authoritative parent name from the payload (first open passes the
      // card's label; deeper levels derive it from data).
      now.name = data.parent_name || now.name;
      _render(data);
    }).catch(function () {
      _state.isLoading = false;
      _loading.classList.add('hidden');
      _error.innerHTML = ''
        + '<span>' + _esc(_s('dd_error', 'Failed to load. Try again.')) + '</span>'
        + ' <button type="button" class="underline ms-1 focus-visible:ring-2 focus-visible:ring-danger-500"'
        + ' data-pi-dd-retry="1">' + _esc(_s('dd_try_again', 'Try again')) + '</button>';
      _error.classList.remove('hidden');
    });
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  function _render(data) {
    _renderCrumbs();
    _renderScope(data);

    var heading = _headingLabel(data.child_level);
    _childHeading.textContent = heading;
    _childHeading.classList.remove('hidden');

    if (data.is_leaf) {
      var units = data.units || [];
      if (!units.length) { _empty.classList.remove('hidden'); return; }
      var ufrag = document.createDocumentFragment();
      for (var i = 0; i < units.length; i++) ufrag.appendChild(_makeUnitRow(units[i]));
      _units.appendChild(ufrag);
      return;
    }

    var rows = data.rows || [];
    if (!rows.length) { _empty.classList.remove('hidden'); return; }
    var childLevel = data.child_level;
    var frag = document.createDocumentFragment();
    for (var j = 0; j < rows.length; j++) frag.appendChild(_makeGroupRow(rows[j], childLevel));
    _rows.appendChild(frag);
  }

  // Breadcrumb: static root crumb (in HTML) + one crumb per stack node. The last
  // crumb is the current scope (non-interactive); earlier crumbs pop to their level.
  function _renderCrumbs() {
    if (!_crumbs) return;
    _crumbs.innerHTML = '';
    for (var i = 0; i < _state.stack.length; i++) {
      _crumbs.appendChild(_chevron());
      var node = _state.stack[i];
      var isLast = (i === _state.stack.length - 1);
      if (isLast) {
        var span = document.createElement('span');
        span.className = 'shrink-0 font-medium text-neutral-900 dark:text-neutral-100 truncate';
        span.setAttribute('aria-current', 'page');
        span.textContent = node.name || ('#' + node.id);
        _crumbs.appendChild(span);
      } else {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'shrink-0 text-neutral-500 dark:text-neutral-400 '
          + 'hover:text-neutral-700 dark:hover:text-neutral-200 transition-colors truncate';
        btn.textContent = node.name || ('#' + node.id);
        (function (idx) { btn.addEventListener('click', function () { _popTo(idx); }); })(i);
        _crumbs.appendChild(btn);
      }
    }
    if (_backBtn) {
      if (_state.stack.length > 1) _backBtn.classList.remove('hidden');
      else _backBtn.classList.add('hidden');
    }
  }

  function _chevron() {
    var s = document.createElement('span');
    s.className = 'shrink-0 text-neutral-300 dark:text-neutral-600 select-none';
    s.setAttribute('aria-hidden', 'true');
    s.textContent = '›';
    return s;
  }

  // Scope summary: this node's own stacked status bar + 3 counts + sold%.
  function _renderScope(data) {
    _scope.innerHTML = ''
      + '<div class="flex items-center justify-between gap-2 mb-2">'
        + '<p class="text-sm font-semibold text-neutral-900 dark:text-neutral-100 truncate" title="'
          + _esc(data.parent_name) + '">' + _esc(data.parent_name) + '</p>'
        + '<p class="text-xs text-neutral-500 dark:text-neutral-400 shrink-0">'
          + '<span class="font-semibold text-success-700 dark:text-success-400 font-mono tabular-nums">'
            + _fmtPct(data.sold_pct) + '%</span> ' + _esc(_s('sold', 'Sold'))
        + '</p>'
      + '</div>'
      + _statusBarHtml(data.buckets, 'h-4')
      + '<div class="grid grid-cols-3 gap-2 mt-2">' + _legendHtml(data.buckets) + '</div>'
      + '<p class="mt-2 text-xs text-neutral-400 dark:text-neutral-500">'
        + _fmtNum(data.total_units) + ' ' + _esc(_s('units', 'units')) + '</p>';
    _scope.classList.remove('hidden');
  }

  // ── Row builders ─────────────────────────────────────────────────────────────
  function _makeGroupRow(row, childLevel) {
    // Group rows render only for non-leaf levels (project/phase/zone); every child
    // (phase/zone/building) is itself drillable — building drills to the unit leaf.
    var li = document.createElement('li');
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'w-full text-start px-5 py-3.5 hover:bg-neutral-50 dark:hover:bg-neutral-800/60 '
      + 'transition-colors focus-visible:ring-2 focus-visible:ring-primary-500';
    btn.setAttribute('aria-label', (row.group_name || '') + ' — ' + _fmtNum(row.total_units) + ' '
      + _s('units', 'units'));

    btn.innerHTML = ''
      + '<div class="flex items-center justify-between gap-3 mb-2">'
        + '<p class="text-sm font-medium text-neutral-900 dark:text-neutral-100 truncate" title="'
          + _esc(row.group_name) + '">' + _esc(row.group_name) + '</p>'
        + '<div class="shrink-0 text-end">'
          + '<span class="text-sm font-bold font-mono tabular-nums text-neutral-900 dark:text-neutral-100">'
            + _fmtNum(row.total_units) + '</span>'
          + '<span class="ms-1 text-xs text-success-700 dark:text-success-400 font-mono tabular-nums">'
            + _fmtPct(row.sold_pct) + '%</span>'
        + '</div>'
      + '</div>'
      + _statusBarHtml(row.buckets, 'h-3')
      + '<div class="grid grid-cols-3 gap-2 mt-2">' + _legendHtml(row.buckets) + '</div>';

    btn.addEventListener('click', function () {
      _drillInto({ level: childLevel, id: row.group_id, name: row.group_name });
    });
    li.appendChild(btn);
    return li;
  }

  function _makeUnitRow(u) {
    var li = document.createElement('li');
    li.className = 'px-5 py-3 flex items-center justify-between gap-3';
    var nameHtml = u.name
      ? '<p class="text-xs text-neutral-500 dark:text-neutral-400 truncate">' + _esc(u.name) + '</p>'
      : '';
    li.innerHTML = ''
      + '<div class="min-w-0">'
        + '<p class="text-sm font-mono text-neutral-900 dark:text-neutral-100 truncate" title="'
          + _esc(u.code) + '">' + _esc(u.code || ('#' + u.unit_id)) + '</p>'
        + nameHtml
      + '</div>'
      + '<span class="shrink-0 ' + _badgeClass(u.bucket) + '">' + _esc(_bucketLabel(u.bucket)) + '</span>';
    return li;
  }

  // ── Status-bar / legend HTML (shared by scope summary + group rows) ──────────
  var _SEG_CLS = {
    available: 'bg-primary-500 dark:bg-primary-600',
    reserved: 'bg-warning-400 dark:bg-warning-500',
    contracted: 'bg-success-500 dark:bg-success-600',
  };

  function _statusBarHtml(buckets, heightCls) {
    var segs = '';
    for (var i = 0; i < buckets.length; i++) {
      var b = buckets[i];
      if (b.pct > 0) {
        segs += '<div class="h-full ' + (_SEG_CLS[b.key] || '') + '" style="width:' + b.pct + '%" '
          + 'title="' + _esc(_bucketLabel(b.key)) + ': ' + _fmtNum(b.count) + ' (' + _fmtPct(b.pct) + '%)"></div>';
      }
    }
    return '<div class="' + heightCls + ' flex rounded overflow-hidden bg-neutral-100 dark:bg-neutral-700">'
      + segs + '</div>';
  }

  function _legendHtml(buckets) {
    var html = '';
    for (var i = 0; i < buckets.length; i++) {
      var b = buckets[i];
      html += '<div class="min-w-0">'
        + '<div class="flex items-center gap-1.5">'
          + '<span class="w-2 h-2 rounded-sm shrink-0 ' + (_SEG_CLS[b.key] || '') + '"></span>'
          + '<span class="text-[11px] text-neutral-500 dark:text-neutral-400 truncate" title="'
            + _esc(_bucketLabel(b.key)) + '">' + _esc(_bucketLabel(b.key)) + '</span>'
        + '</div>'
        + '<p class="mt-0.5 ms-3.5 text-sm font-bold font-mono tabular-nums '
          + 'text-neutral-900 dark:text-neutral-100">' + _fmtNum(b.count) + '</p>'
      + '</div>';
    }
    return html;
  }

  // ── Label / format helpers ───────────────────────────────────────────────────
  function _bucketLabel(key) {
    return { available: _s('available', 'Available'),
             reserved: _s('reserved', 'Reserved'),
             contracted: _s('contracted', 'Contracted') }[key] || key;
  }

  function _badgeClass(bucket) {
    return 'badge ' + ({ available: 'badge-info', reserved: 'badge-warning',
                         contracted: 'badge-success' }[bucket] || 'badge-neutral');
  }

  function _headingLabel(childLevel) {
    var key = _headingKeyOf(childLevel);
    return key ? _s(key, childLevel) : childLevel;
  }

  function _s(key, fallback) {
    return (S[key] != null) ? S[key] : fallback;
  }

  function _fmtNum(n) {
    if (n == null) return '0';
    var lang = S.lang || 'en';
    return Number(n).toLocaleString(lang === 'ar' ? 'ar-EG' : 'en-US');
  }

  function _fmtPct(p) {
    if (p == null) return '0';
    return (Math.round(p * 10) / 10).toString();
  }

  function _esc(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function _hide(el) { if (el) el.classList.add('hidden'); }

  // ── Focus management (mirrors drilldown.js D7) ──────────────────────────────
  function _setMainInert(active) {
    var app = document.getElementById('app');
    if (!app) return;
    if (active) app.setAttribute('inert', '');
    else app.removeAttribute('inert');
  }

  function _getFocusable() {
    if (!_panel) return [];
    var sel = 'button:not([disabled]), [tabindex]:not([tabindex="-1"]), a[href], input, select, textarea';
    return Array.prototype.filter.call(_panel.querySelectorAll(sel), function (el) {
      return !el.closest('[hidden]') && getComputedStyle(el).display !== 'none';
    });
  }

  function _handleKeydown(e) {
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key !== 'Tab') return;
    var f = _getFocusable();
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey) {
      if (document.activeElement === first) { e.preventDefault(); last.focus(); }
    } else {
      if (document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  // ── Event wiring ───────────────────────────────────────────────────────────
  function _wire() {
    if (!_panel) return;
    _closeBtn.addEventListener('click', close);
    _backdrop.addEventListener('click', close);
    if (_rootCrumb) _rootCrumb.addEventListener('click', close);
    if (_backBtn) _backBtn.addEventListener('click', _back);

    _error.addEventListener('click', function (e) {
      if (e.target.closest('[data-pi-dd-retry]')) {
        _hide(_error);
        _loading.classList.remove('hidden');
        _fetch();
      }
    });

    _panel.addEventListener('keydown', _handleKeydown);

    // Delegated trigger: any [data-pi-drill-level] element opens a fresh drill.
    document.addEventListener('click', function (e) {
      var el = e.target.closest('[data-pi-drill-level]');
      if (!el) return;
      var level = el.getAttribute('data-pi-drill-level');
      var id = el.getAttribute('data-pi-drill-id');
      if (!level || !id) return;
      open(level, id, el.getAttribute('data-pi-drill-name') || '', el);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var el = e.target.closest('[data-pi-drill-level]');
      if (!el) return;
      var level = el.getAttribute('data-pi-drill-level');
      var id = el.getAttribute('data-pi-drill-id');
      if (!level || !id) return;
      e.preventDefault();
      open(level, id, el.getAttribute('data-pi-drill-name') || '', el);
    });
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.piDrill = {
    open: open,
    close: close,
    get state() { return { stack: _state.stack.slice(), isLoading: _state.isLoading }; },
  };

  // Export pure functions for Node unit tests.
  if (typeof module !== 'undefined') {
    module.exports = {
      _buildUrl: _buildUrl,
      _childLevelOf: _childLevelOf,
      _isLeafLevel: _isLeafLevel,
      _headingKeyOf: _headingKeyOf,
      _pushNode: _pushNode,
      _popToIndex: _popToIndex,
    };
  }

  // Init
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { _initRefs(); _wire(); });
    } else {
      _initRefs();
      _wire();
    }
  }

}());
