/**
 * api.js — Fetch wrapper with auth, retry, and loading state management
 */

'use strict';

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
        credentials: 'include',   // sends Basic Auth cookies
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
