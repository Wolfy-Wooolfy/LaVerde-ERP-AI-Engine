# HR Module — Implementation Decisions

## i18n Pass (2026-06-08)

### D-HR-I18N-1: Scope = HR templates + JS only; base.html nav deferred

The HR i18n pass covers exactly:
- `frontend/templates/hr/dashboard.html` (F1)
- `frontend/templates/components/_hr_dept_panel.html` (F2)
- `frontend/templates/components/_hr_profile_panel.html` (F3)
- `frontend/static/js/hr_drilldown.js` (F2 controller)
- `frontend/static/js/hr_employee_drilldown.js` (F3 controller)

`base.html` lines 162/167/316 (`title="HR Overview"`, sidebar `HR` text, mobile nav `HR` text) are left in English and deferred to a dedicated nav/topbar i18n pass. The `"HR Overview"` key added in this pass will be reused there without changes.

### D-HR-I18N-2: window.HR_STRINGS pattern for JS-rendered strings

JS controllers cannot call `_t()` directly (server-side Jinja). Instead, `dashboard.html` injects a `window.HR_STRINGS` object in `{% block extra_scripts %}` before the two `<script src>` tags, mirroring the `window.CHAT_STRINGS` pattern in `base.html`. Both JS files read from it via an `_s(key, fallback)` helper that gracefully falls back to English if the object is absent. English fallback strings are preserved in every `_s()` call for robustness; the coverage checker explicitly skips `|| 'fallback'` positions so they do not produce false positives.

### D-HR-I18N-3: Hybrid keying — self-keyed short strings + hr_* IDs

Short strings where the English text is the natural key use the English text directly as the JSON key (e.g., `"Running"`, `"Headcount"`, `"Departments"`). Long, parameterized, or JS-only strings use machine keys with the `hr_` prefix (e.g., `"hr_aggregate_wage_only"`, `"hr_err_session"`). Both patterns are valid; see `docs/I18N.md` for the documented convention.

### D-HR-I18N-4: wage = base monthly; "الأساسي" confirmed

`hr.contract.wage` is the standard Odoo monthly base wage field. Egyptian-localisation allowance fields (`l10n_eg_housing_allowance`, `l10n_eg_transportation_allowance`, `allowances`) and insurance fields are separate and out of scope for Phase 1 (discovery W4). The Arabic label "إجمالي الراتب الأساسي فقط" (aggregate base wage only) therefore correctly uses "الأساسي" (base/basic).

### D-HR-I18N-5: Neutral Arabic contract-date wording

Contract end dates display as "حتى {date}" (Through {date}) — neutral "until", no urgency. Open-ended contracts display as "غير محدد المدة" (without defined duration). Bucket labels use factual time-range phrases ("ينتهي خلال 45 يوماً", "ينتهي 46–90 يوم", etc.) — these are neutral countdown ranges, not alarm language. Prohibited forms: "ينتهي قريب", "بينتهي قريب", "وشيك الانتهاء".

### D-HR-I18N-6: Western digits; tabular alignment preserved

All numeric values (headcount, wages, percentages, tenure) render with Western digits (0–9) and the `tabular` / `tabular-nums` CSS class. Arabic-Indic digits (٠١٢٣٤٥٦٧٨٩) are not used. Month abbreviations in F3 dates are localized (e.g., "يونيو") but the surrounding digits stay Western: "30 يونيو 2026".

### D-HR-I18N-7: Copy fix — disabled drill-down button title

The original tooltip `title="Department drill-down available in F2"` leaked the internal "F2" component label to the board. Changed to `"Drill-down not available for this group"` (more accurate — the group is disabled, not "available in F2"). The English key `"hr_dept_drill_disabled"` carries this new copy. AR: "التفاصيل غير متاحة لهذه المجموعة". No functionality changed.

### D-HR-I18N-8: RTL — first use of rtl: variants in this project

Two Tailwind RTL-variant classes introduced:
- `rtl:rotate-180` — flips department-row chevron arrows to point left in RTL
- `rtl:text-left` — flips the EGP/share amount column to align at the correct trailing edge in RTL

CSS rebuilt (`npm run build:css`) in commit D to land both rules in `app.css`. Tailwind 3.4.19 built-in `rtl:` variant used (no third-party plugin). Panel slide animation was already RTL-correct in both JS controllers (reads `document.documentElement.dir`). Panel edge (`end-0`) and border (`border-s`) were already logical properties — no changes needed.

### D-HR-I18N-9: Pluralization — 2-form v1 limitation

Arabic pluralization rules are complex (singular/dual/plural-3-10/plural-11+). This pass uses a 2-form approximation (singular for n=1, plural otherwise), matching the pattern already used in other modules. ICU plural logic is not introduced. Known limitation flagged for a future i18n infrastructure upgrade.

### D-HR-I18N-10: F1 reference_date format — no localization gap

`headcount.reference_date` is `YYYY-MM-DD` (ISO format, numeric digits only). No English month name appears; the string reads the same in both EN and AR. The month-names localization (12-entry `HR_STRINGS.months` array) applies only to F3's `_fmtDate()` function, which formats ISO dates as "30 Jun 2026" → "30 يونيو 2026".

### D-HR-I18N-11: Backend-emitted data strings — static checker blind spot resolved by architecture

The static i18n checker (`scripts/check_hr_i18n_coverage.py`) skips any template line containing `{{` on the grounds that dynamic expressions cannot be statically validated. This created a blind spot: `kpi_service.py` was emitting display strings (`"<1y"`, `"1-3y"`, etc.) as the `band.band` payload, and the template rendered them raw via `{{ band.band }}` — invisible to the checker.

Fix: backend emits stable machine keys (`"lt1y"`, `"y1_3"`, `"y3_5"`, `"y5_10"`, `"y10plus"`) as `band.band`. The template calls `_t("hr_tenure_" + band.band)`, which the checker CAN see (the `_t(` token is present). Translations for all five keys added to `en.json` and `ar.json`.

Rule: backend data values that flow into user-visible template text must be machine keys, never display strings. Display strings belong only in translation files.
