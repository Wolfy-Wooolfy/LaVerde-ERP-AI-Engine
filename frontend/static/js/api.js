/**
 * api.js — Fetch wrapper with auth, retry, and loading state management
 */

'use strict';

// ── Manual-refresh URL seam ───────────────────────────────────────────────────
// The ONE place that turns a URL into a cache-bypassing URL. backend/main.py:233
// reads `?refresh=1` on a GET as "skip every in-memory cache read for this
// request", so this must be reachable ONLY from a user-initiated refresh: an
// automatic timer that sent it would defeat the cache on every tick and put Odoo
// load straight back where Phase 1 (79e9b15) found it — silently, because the
// pages would still render perfectly. Hence `manual` is an explicit argument
// with no truthy default, and every automatic caller passes nothing at all.
//
// URLSearchParams.set — never append, never string concatenation — is what makes
// this both param-preserving and idempotent. The SSR pages carry real state in
// their query strings (window, tab, months, campaign_id, start_month, end_month),
// so a concatenated "?refresh=1" would corrupt them; and the reload lands on a
// URL that already has refresh=1, so a second application must not stack.
//
// Relative in, relative out; absolute in, absolute out — the SSR reload feeds it
// location.href and assigns the result straight back.
window.crmWithRefresh = function (url, manual) {
  if (!manual) return url;
  var u = new URL(url, window.location.origin);
  u.searchParams.set('refresh', '1');
  return /^[a-z][a-z0-9+.-]*:/i.test(url) ? u.href : u.pathname + u.search + u.hash;
};

window.crmApi = {
  _retries: 2,
  _retryDelay: 1000,

  async get(url, options = {}) {
    return this._request('GET', url, null, options);
  },

  async _request(method, url, body, options = {}, attempt = 0) {
    const headers = {
      'Accept': 'application/json',
      ...options.headers,
    };
    if (body) headers['Content-Type'] = 'application/json';

    let response;
    try {
      response = await fetch(url, {
        method,
        headers,
        credentials: 'include',   // sends session cookie
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (networkError) {
      if (attempt < this._retries) {
        await this._sleep(this._retryDelay * (attempt + 1));
        return this._request(method, url, body, options, attempt + 1);
      }
      throw new Error(`Network error: ${networkError.message}`);
    }

    // Retry on 503 Service Unavailable (Odoo down)
    if (response.status === 503 && attempt < this._retries) {
      await this._sleep(this._retryDelay * (attempt + 1));
      return this._request(method, url, body, options, attempt + 1);
    }

    // Parse JSON
    const contentType = response.headers.get('Content-Type') || '';
    const data = contentType.includes('application/json') ? await response.json() : {};

    if (!response.ok) {
      const message = data?.error?.message || data?.detail || `HTTP ${response.status}`;
      throw Object.assign(new Error(message), { status: response.status, data });
    }

    return data;
  },

  _sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  },
};
