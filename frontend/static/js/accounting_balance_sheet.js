/**
 * accounting_balance_sheet.js — Alpine component for /accounting/balance-sheet
 * (Module 4 · Phase 2, M4.13).
 *
 * The route serves a shell; ALL figures come from
 * GET /api/v1/accounting/balance-sheet through window.crmApi (same-origin
 * session cookie, built-in retry on 503/network). The statement is being
 * edited live by finance and the API is Cache-Control: no-store — this page
 * keeps NO cache, NO localStorage/sessionStorage and NO polling: data loads
 * once on init and again only on manual refresh.
 */

'use strict';

function balanceSheetPage() {
  return {
    loading: true,
    error: false,
    data: null,
    open: {},   // 'group:account_type' → true while the subgroup is expanded

    init() {
      // Collections-style: the topbar refresh button refetches this page's data.
      var topbar = document.getElementById('refresh-btn');
      if (topbar) topbar.onclick = () => this.load(true);
      this.load();
    },

    // `manual` is true ONLY for a user-initiated refresh: the topbar button, the
    // header button, and the error-banner retry. It gates the shared affordance
    // in app.js (global loading bar + spinning topbar #refresh-icon) so the
    // initial load on init() stays silent — the same rule collections.js and
    // customer_accounts.js follow for their timers.
    //
    // Deliberately NOT threaded to crmWithRefresh. Every other manual refresh in
    // the app adds ?refresh=1 to bypass a server cache; this endpoint is
    // Cache-Control: no-store and uncached by design (accounting.py:75-78), so
    // the parameter would imply a cache that does not exist.
    async load(manual) {
      this.loading = true;
      this.error = false;
      if (manual) window.crmRefreshFeedback.start();
      try {
        this.data = await window.crmApi.get('/api/v1/accounting/balance-sheet');
      } catch (e) {
        // Any final failure (401/500/503 after retries, network): keep
        // whatever is already on screen and show the error banner + retry —
        // the collections behavior.
        this.error = true;
      } finally {
        this.loading = false;
        // In the finally, so a failed refresh clears the spinner too and the
        // button is immediately clickable again.
        if (manual) window.crmRefreshFeedback.stop();
      }
    },

    toggle(key) { this.open[key] = !this.open[key]; },
    isOpen(key) { return !!this.open[key]; },

    // Approved formatter (Phase-2 decision 2): Western digits, thousands
    // separators, exactly 2 decimals; negatives keep the minus sign.
    // CollectionsFormatters.formatEGP is deliberately NOT used — under the
    // ar locale it emits Arabic-Indic digits.
    fmt(value) {
      if (value === null || value === undefined) return '—';
      return value.toLocaleString('en-EG', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
    },

    // generated_at (ISO, Africa/Cairo) → localized date-time, Latin digits
    // in BOTH locales (ar-EG-u-nu-latn keeps Arabic month names).
    fmtGeneratedAt(iso) {
      if (!iso) return '—';
      var lang = document.documentElement.getAttribute('data-lang');
      return new Date(iso).toLocaleString(
        lang === 'ar' ? 'ar-EG-u-nu-latn' : 'en-GB',
        { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' }
      );
    },
  };
}
