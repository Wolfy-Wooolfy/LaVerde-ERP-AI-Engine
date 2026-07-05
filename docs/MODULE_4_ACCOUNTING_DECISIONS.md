# Module 4 — Accounting: Implementation Decisions

> **Scope:** Phase 1 — Opening Balance Sheet (backend service + API endpoint, no frontend).
> **Date:** 2026-07-05
> **References:** `docs/MODULE_4_PLAN.md` (conceptual plan) · `docs/ACCOUNTING_DISCOVERY.md` ·
> `docs/LA_VERDE_DATA_STATE.md` · read-only recon probe 2026-07-05 (16 (internal_group, account_type)
> pairs; 5,620 posted lines; total debit == total credit == 11,269,506,408.53 EGP).

Decisions ship in the same commit as the code implementing them.

---

## M4.1 — Classification by `internal_group`, never code prefixes

Every account is classified via `account.account.internal_group`
(`asset` / `liability` / `equity` / `income` / `expense`). Code prefixes are
**forbidden** as a classification signal in this database: prefix "2" mixes
13 asset accounts with 70 liability accounts, with a 131.3M EGP effect on
account `20002000` alone (misclassified under a prefix rule). `account.group`
is likewise unusable (single junk record) and is never read.

## M4.2 — Amounts from line-level debit/credit only

All amounts come from `account.move.line` `debit`/`credit` with domain
`[('parent_state', '=', 'posted')]`, aggregated per account via one
`read_group` (`read_group` is in the client's `ALLOWED_METHODS`, so no
`search_read` fallback is needed). `account.move.amount_total` is **never**
read: it is known to be wrong by ~2M EGP on `move_type='entry'` moves in this
database, and the opening balance consists exclusively of 'entry' moves.

## M4.3 — Live read on every request; no caching

The opening balance is still being edited in place by finance (modified twice
already; 296 lines removed on 2026-07-02). The service therefore computes
LIVE on every request:

- no server-side cache (no TTL, no cache key, no `cache_status` field);
- response header `Cache-Control: no-store` (deviation from the house
  `private, max-age=60` — a browser-cached figure could contradict the Odoo
  UI during verification, which is exactly the workflow this phase serves);
- no `X-Cache-Status` header (nothing is ever cached);
- fixed payload banner `banner_ar: "أرصدة افتتاحية — بيانات تحت الإدخال"`.

A short TTL may be introduced in a later phase once figures stabilize.

## M4.4 — `unallocated_result` closes the accounting equation

`totals.unallocated_result = Σ (credit − debit)` over all accounts with
`internal_group in ('income', 'expense')`. While only the opening balance is
posted this is exactly 0.00; once operational entries post, it becomes the
current-period result. The balance check is:

```
difference = assets − (liabilities + equity + unallocated_result)
balanced   = |difference| < 0.01   # one piaster, evaluated pre-rounding
```

**Presentation note:** Odoo's balance sheet UI folds the current-period
result into the Equity section as a computed line; our API keeps it separate
as `totals.unallocated_result`, and a future frontend phase will render it as
a synthetic line inside the equity section. No code change for this now.

## M4.5 — Fail-loud Arabic label map (and other integrity guards)

Subgroups are keyed by `account_type` with Arabic labels from
`ACCOUNT_TYPE_LABELS_AR`. The map covers the 11 values present for the three
displayed groups in the live chart (probe 2026-07-05):

| account_type | label_ar |
|---|---|
| asset_receivable | ذمم مدينة |
| asset_cash | النقدية وما في حكمها |
| asset_current | أصول متداولة |
| asset_prepayments | مصروفات مدفوعة مقدماً |
| asset_fixed | أصول ثابتة |
| asset_non_current | أصول غير متداولة |
| liability_payable | ذمم دائنة |
| liability_current | خصوم متداولة |
| liability_non_current | خصوم غير متداولة |
| equity | حقوق الملكية |
| equity_unaffected | أرباح (خسائر) غير موزعة |

`equity_unaffected` mirrors Odoo's own term (Unallocated Earnings) and stays
direction-neutral via the parenthesized **خسائر** — correct whether the
balance is accumulated profit or loss (renamed in M4.12; original Phase-1
wording was "نتيجة السنة الحالية").

The service raises `BalanceSheetIntegrityError` — naming every offending
value — when:

- a displayed account (asset/liability/equity) has an `account_type` absent
  from the map (income/expense types are exempt — they are never displayed);
- a posted line group references an account id absent from the chart fetch
  (a balance sheet must never silently drop posted amounts).

`BalanceSheetIntegrityError` lives in the service module (single consumer;
`backend/core/exceptions.py` stays untouched) and subclasses
`LaVerdeERPError`. It is deliberately NOT an `OdooQueryError`: the endpoint
maps it to **500** (data/label map must be fixed), not 503 (retry later).

## M4.6 — Sign conventions and fixed section order

```
internal_group == 'asset'                → balance = debit_sum − credit_sum
internal_group in ('liability','equity') → balance = credit_sum − debit_sum
```

Sections are emitted in fixed order: `asset` ("الأصول"), `liability`
("الخصوم"), `equity` ("حقوق الملكية"). Equity is currently **negative**
(≈ −586M EGP live) — the sign is preserved as-is.

## M4.7 — Off-balance exclusion

Accounts whose `internal_group` is outside the five known values are excluded
from all totals and surfaced as
`excluded_off_balance: {count, total}`, where `total` is their raw
`debit − credit` sum (no sign convention applies). The live chart has **zero**
such accounts today (probe 2026-07-05); the field exists so a future
off-balance account cannot silently distort the sheet.

## M4.8 — Omission and ordering rules

- Accounts with `|balance| < 0.005` are omitted from account **lists**;
  subgroup/section **totals always include every member account**.
- A subgroup is omitted only when it has no visible account AND its
  full-precision total is `< 0.005`. (Offsetting accounts — e.g. +500/−500 —
  keep the subgroup; many sub-piaster balances that accumulate to ≥ 0.005
  keep it too, with an empty account list.)
- Deterministic ordering, computed on full-precision values:
  subgroups by `|total|` descending (ties: `account_type` ascending);
  accounts by `|balance|` descending (ties: `code` ascending).

## M4.9 — Rounding at serialization only

All computation runs at full float precision; every emitted amount is rounded
to 2 decimals only when the payload is built (`-0.0` is normalized to `0.0`).
Consequence: a subgroup/section total may differ from the sum of its
*rounded* account balances by ≤ 0.01 — this is intentional and correct.
`balanced` is evaluated on the pre-rounding difference.

## M4.10 — Response contract (service payload == endpoint body)

`GET /api/v1/accounting/balance-sheet` (session auth; module gate
"accounting" at router include + per-endpoint `get_current_user` — exactly
the collections pattern). Payload:

```
generated_at            ISO-8601, Africa/Cairo (zoneinfo)
currency                "EGP"
banner_ar               fixed banner (M4.3)
totals                  assets, liabilities, equity, unallocated_result,
                        liabilities_plus_equity_plus_result, difference, balanced
excluded_off_balance    {count, total}                       (M4.7)
sections[]              {group, label_ar, total, subgroups[]}
  subgroups[]           {account_type, label_ar, total, accounts[]}
    accounts[]          {code, name, balance}
rpc_duration_ms         int — house-consistent observability field
```

No `cache_status` field, no `X-Cache-Status` header, `Cache-Control:
no-store` (M4.3). Exactly 2 read-only RPCs per request.

## M4.11 — Module registration & access (Commit 2 addendum)

- Router gated at include time in `backend/api/v1/router.py` via
  `require_module_api("accounting")`; each endpoint also depends on
  `get_current_user` (401 unauthenticated, 403 without the module key).
- `"accounting"` added to `_VALID_MODULES` in
  `backend/api/v1/endpoints/settings.py` so admins can grant it via the
  Settings API. `scripts/manage_users.py` performs no module-id validation
  (unchanged).
- Admin users with `modules=["*"]` (Khaled's `admin`) reach the endpoint with
  no grant step. No HTML route / sidebar entry this phase: browser
  verification is the raw JSON at `/api/v1/accounting/balance-sheet` after
  login.
- Error mapping: `OdooQueryError` → 503 `odoo_unavailable`;
  `BalanceSheetIntegrityError` and any unexpected exception → 500
  `internal_error` (house body shapes; offending values go to the log).

## M4.12 — equity_unaffected label renamed (2026-07-05, Phase 2)

`ACCOUNT_TYPE_LABELS_AR["equity_unaffected"]` renamed:

- **Before (Phase 1):** "نتيجة السنة الحالية"
- **After:** "أرباح (خسائر) غير موزعة"

Rationale: the new wording matches Odoo's own term for this account type
(**Unallocated Earnings**), so the board sees the same vocabulary in our
page and in the Odoo Balance Sheet screen during side-by-side verification.
It remains direction-neutral — the parenthesized **خسائر** makes it correct
whether finance confirms the current debit balance as accumulated losses or
flips it during the opening-balance cleanup. The M4.5 label table above is
current-state reference and was updated in place (it must never contradict
the code); this dated entry preserves the history. Value-only change: the
payload **shape** is untouched (M4.10 contract intact), and the fail-loud
guard (M4.5) is unaffected.

## M4.13 — Frontend page + sidebar link (2026-07-05, Phase 2)

`GET /accounting/balance-sheet` (HTML, `require_module_html("accounting")`,
`page="accounting_balance_sheet"`) renders
`frontend/templates/accounting/balance_sheet.html`. The route serves the
**shell only** — zero Odoo calls, zero service calls; every figure arrives
client-side from the Phase-1 API. Unauthenticated → 302 `/login?next=…`;
without the module key → 403 (exactly the collections HTML behavior).

- **Fetch pattern (approved recon amendment):** no inline "Alpine fetch"
  exists anywhere in the house — collections fetches via a vanilla JS module
  through `window.crmApi`. This page uses the faithful hybrid: an Alpine
  component `balanceSheetPage()` defined in
  `frontend/static/js/accounting_balance_sheet.js` (the `crmApp()` /
  `chatDrawer()` precedent), fetching once on init via
  `window.crmApi.get()` (same-origin cookie, built-in 503/network retry),
  rendering the section → subgroup → account tree with `x-for`. The topbar
  refresh button is rebound to the page's refetch, collections-style.
- **No cache, no polling:** the statement is edited live by finance and the
  API is `Cache-Control: no-store` (M4.3) — the page keeps no cache, no
  local/sessionStorage, no auto-refresh interval; manual refresh only.
  Every load/refresh refetches by design.
- **Synthetic equity rows:** when `totals.unallocated_result != 0` the
  equity section appends a non-expandable row "نتيجة الفترة الحالية (غير
  مرحّلة)" plus a bold "إجمالي حقوق الملكية شامل نتيجة الفترة" =
  `equity + unallocated_result` — computed client-side from `totals` only
  (no payload change). This makes the section foot to Odoo's EQUITY total
  once operational entries start posting (Odoo folds the period result into
  Equity — M4.4 presentation note); while the value is 0.00 (today) neither
  row renders, so the page is clean now and adapts automatically later.
- **Number formatting (approved):** local formatter — Western digits,
  thousands separators, exactly 2 decimals
  (`toLocaleString('en-EG', {min/maximumFractionDigits: 2})`), negatives
  keep the minus sign. `CollectionsFormatters.formatEGP` is deliberately NOT
  used: under the `ar` locale it emits Arabic-Indic digits. Currency
  presentation: "ج.م" (via `_t("bs_egp")`; en: "EGP") on the three summary
  cards and the equation footer; bare grouped numbers in the detail tree
  rows (statement style, matching Odoo's own screen); one muted line under
  the title: "جميع المبالغ بالجنيه المصري (EGP)". `generated_at` renders
  with Latin digits in both locales (`ar-EG-u-nu-latn` under Arabic).
- **Sidebar = ONE link:** the desktop "Accounting — Coming Soon" placeholder
  became the live link in place (position-preserving; gate
  `'accounting' in am or '*' in am`); the mobile drawer gained the same
  single link after the projects-inventory links. Nothing else moved —
  sidebar grouping remains a separately deferred task.
- **Tailwind rebuild:** `frontend/static/css/app.css` regenerated with the
  EXISTING `npm run build:css` (house convention — the compiled CSS ships in
  feature commits; the content scan covers templates + static JS). No build
  step was added.
- **Tests:** `tests/unit/modules/accounting/test_html_routes.py` (302 with
  `next`, 403 without the module, 200 shell + mount markers + Arabic/RTL
  render, sidebar active-state) and accounting rows in
  `tests/integration/test_rbac.py` sections B and C — all against Odoo-free
  pages, so the 4-skip environmental signature cannot grow.
