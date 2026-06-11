# Module 2 — Stage Tracker

**Last updated:** 2026-06-11 (Session 19 / N3 — D-8 migration #1: verify_kpi7_live.py on session auth, live run all-PASS incl. June triple-collapse window check; 16 scripts remain)

This is the single source of truth for "where are we?" in
Module 2. Update at the close of every stage.

## Stage table

| Stage | Status | Tag | Session | Commits | Key Output |
|---|---|---|---|---|---|
| 1 — KPI 7 backend | ✅ Closed | `checkpoint-C-stage1-kpi7-backend-complete` | Session 9 | 4 | Expected Collections forecast endpoint |
| 2 — KPI 2 cheques extension | ✅ Closed | `checkpoint-C-stage2-kpi2-extended` | Session 10 (10.1-10.9) | 5 | KPI 2 backend extended (PATH C) |
| 3 — Frontend Restructure | ✅ Closed | `checkpoint-D-stage3-frontend-restructure-complete` | Session 11 (11.1-11.18) | 5 + 1 fix + 1 doc | 4-section layout, state refactor |
| 2.5 — KPI 2 redefinition | ✅ Closed | `checkpoint-C-stage2-5-kpi2-redefined` | Session 12 | 6 | KPI 2 formula PATH A; +1.93M EGP cheques annotation |
| 4 — Premium Visual Identity | ✅ Closed | `checkpoint-D-stage4-premium-visual-identity-complete` | Session 13 | 10 | Dark canvas, heartbeat, premium cards, cheques pill, D2.9 fix |
| 5 — Drill-down Backend | ✅ Closed | `checkpoint-E-stage5-drilldown-backend-complete` | Session 14 | 8 | 5 drill-down endpoints, D6 8/8 PASS, KPI 7 cheques_record_count |
| 6 — Drill-down Frontend | ✅ Closed | `checkpoint-E-stage6-drilldown-frontend-complete` | Session 15 + browser verification (2026-05-22) | 28 | Side-panel controller, filter bar, hash, keyboard nav, 52 unit tests; browser verification V0–V10 all pass; 6 bugs found and fixed in verification (page_size, paid chip, _escHtml, missing columns, amber note styling, inert isolation) |

## Current Numbers Baseline (2026-05-21, D6 live verification)

Values are live Odoo readings as of the D6 gate run. They will drift as
La Verde staff enter data daily.

| KPI | Value | Notes |
|---|---|---|
| KPI 1 Portfolio | 6,121,816,265.23 EGP / 42,413 records | D6 V3 confirmed |
| KPI 2 Late | 332,036,464.40 EGP / 2,027 records | D6 V1 confirmed; PATH A (Decision 12.1) |
| KPI 2 Cheques in Pipeline | — | not re-read in D6; prior baseline 790,500 EGP (Decision 14.6a) |
| KPI 3 Pending Check Exposure | backend live, frontend removed | per refactor §6 |
| KPI 4 Collection Rate | unavailable (data-state) | Decision 11.16 |
| KPI 5 New Capital | 171,695,538.40 EGP | D6 V4 confirmed |
| KPI 5 Cassette | 154,822,426.00 EGP | D6 V4 confirmed |
| KPI 5 La puerta | 3,589,500.00 EGP | D6 V4 confirmed |
| KPI 5 Sum | 330,107,464.40 EGP | = KPI 2 ✓ |
| KPI 7 This Year | 303,611,585.00 EGP / 1,706 records | re-read 2026-06-11 (Session 19 live run); due 302,767,157.00 EGP |
| KPI 7 Cheques this_year | 790,500 EGP / count = 2 | D6 V8 confirmed; Decision 14.6a baseline; re-confirmed identical 2026-06-11 |
| KPI 7 this_month = this_quarter = this_half | 21,014,883.00 EGP / 126 records each | 2026-06-11 — June triple nesting collapse (all end 2026-06-30), identical aggregates verified live |

## Rollback Tags

| Tag | Phase | Date |
|---|---|---|
| `checkpoint-A-D1-complete` | Module 2 Phase 5 D1 close | (historical) |
| `checkpoint-B-D2-complete` | Module 2 Phase 5 D2 close | (historical) |
| `checkpoint-C-stage1-kpi7-backend-complete` | Stage 1 close | 2026-05-17 |
| `checkpoint-C-stage2-kpi2-extended` | Stage 2 close | 2026-05-19 |
| `checkpoint-D-stage3-frontend-restructure-complete` | Stage 3 close | 2026-05-19 |
| `checkpoint-C-stage2-5-kpi2-redefined` | Stage 2.5 close | 2026-05-20 |
| `checkpoint-D-stage4-premium-visual-identity-complete` | Stage 4 close | 2026-05-20 |
| `checkpoint-E-stage5-drilldown-backend-complete` | Stage 5 close | 2026-05-21 |
| `checkpoint-E-stage6-drilldown-frontend-complete` | Stage 6 close — browser verification complete | 2026-05-22 |

## Post-close findings

Bugs or gaps discovered after a stage was closed. Logged here for traceability; not reopening closed stages.

| Stage affected | Finding | Discovered | Details |
|---|---|---|---|
| Stage 7 (type breakdown, within Stage 1 close) | `mock_client_kpi7` fixture (Stage 5 origin) never updated for Stage 7's `_fetch_bucket_type_breakdown` — 12 KPI 7 tests in `tests/unit/modules/collections/test_kpi_service.py` were silently failing since Stage 7 shipped. Root cause: the real Stage 7 tests lived in `backend/modules/collections/tests/test_stage7.py` (legacy path, never collected by pytest — see D-6), so the fixture gap went unnoticed. Fixed in D-1 (2026-05-25): `mock_client_kpi7` converted to yield fixture; `_fetch_bucket_type_breakdown` patched at fixture level. All 12 tests now pass. | D-1 work, 2026-05-25 | Fixture `mock_client_kpi7` in `tests/unit/modules/collections/test_kpi_service.py`. |

## Pending / Deferred Items

Items identified during development or browser verification that are not yet scheduled
into a numbered stage. Each entry carries enough context to pick up cold.

| # | Title | Priority | Raised by | Date | Description |
|---|---|---|---|---|---|
| D-1 | ~~English installment type names~~ **RESOLVED 2026-05-25** | Cosmetic, non-urgent | Khaled (browser visual check) | 2026-05-22 | **RESOLVED 2026-05-25 — browser-verified by Khaled.** EN mode: KPI 7 type-breakdown cards and drill-down show English names (Regular, Garage, Club, Maintenance…). AR mode: same section shows Arabic names (قسط دوري، الجراج، النادي، وديعة الصيانة…). Both directions confirmed; numbers unchanged in both modes. Added `INSTALLMENT_TYPE_NAMES_EN` + `get_type_name_en()` to `installment_type_names.py` (13 IDs). Added `installment_type_name_en` field to `TypeBreakdownEntry` + `InstallmentRow` schemas. Backend (`_fetch_bucket_type_breakdown`, `_serialize_row`) returns both `_ar` and `_en` fields. Frontend (`collections.js` `_buildBreakdownBars`, `drilldown.js` `_buildDrilldownBreakdownHtml` + `_makeInstallmentRow`) selects name by `lang === 'ar'`. 13 tests in live paths. Also fixed pre-existing Stage 7 fixture bug (mock_client_kpi7) — see Post-close findings. |
| D-2 | ~~today_str() timezone fragility in cache.py~~ **RESOLVED 2026-05-24** | Non-urgent, important — not cosmetic | M3-S3 design (KhElmasry) | 2026-05-23 | **RESOLVED 2026-05-24.** `today_str()` in both `collections/services/cache.py` and `customer_accounts/services/cache.py` changed from `date.today()` (system local clock) to `datetime.now(ZoneInfo("Africa/Cairo")).date().isoformat()` per Decision 5.9. All "UTC date" docstrings corrected to "Cairo-local date". Associated comment in `customer_accounts/services/kpi_service.py` updated. 10 unit tests added (5 per module) covering format, Cairo-match, timezone stability, DST boundary, and make_key embedding — all passing. Verification on fresh server (Decision 6.4) confirmed zero delta: KPI 2 = 336,111,714.40 EGP / 2,050 records, KPI 5 total = 334,182,714.40 EGP, KPI B total = 334,182,714.40 EGP — identical to pre-fix baseline. |
| D-3 | Mobile responsive — Customer Accounts dashboard + M3-S7 customer drill-down + M3-S8 refunds drill-down (Module 3) | Non-urgent — requires deploy or LAN access | M3-S5 close + M3-S7 close + M3-S8 close (KhElmasry) | 2026-05-23 | صفحة `/customer-accounts/dashboard` اتأكّدت على الـ desktop (عربي + إنجليزي) في M3-S5. الـ M3-S7 customer drill-down panel اتأكّد desktop في 2026-05-23. الـ M3-S8 refunds drill-down panel اتأكّد desktop في 2026-05-24. الموبايل لسه ماتأكّدش للتلاتة — التحقق محتاج deploy أو وصول من جهاز موبايل على نفس الشبكة. المطلوب وقت الاختبار: (1) الـ 3 KPI cards تتراص تحت بعض (grid-cols-1 sm:grid-cols-3)، (2) جدول KPI B مقروء (overflow-x-auto)، (3) قسم الاستردادات ظاهر صح وقابل للنقر، (4) مدخل "Customer Accounts" شغّال في الـ mobile drawer، (5) الـ M3-S7 customer drill-down panel ينزل full-screen على الموبايل (w-full على شاشات < lg) وينغلق صح، (6) الـ M3-S8 refunds drill-down panel (IDs: ca-rd-*) ينزل full-screen على الموبايل وينغلق صح — بيانات 7 سجلات تظهر صح + الإجمالي. بعد التأكيد، يتحط tag على M3-S5 + M3-S7 + M3-S8. المطلوب بعد الاختبار: تحديث MODULE_3_PLAN.md §4 mobile note + تحديث هذا البند إلى "Done". |
| D-4 | "آخر دفعة" في Customer drill-down (Module 3 M3-S6) | مؤجّل — يحتاج بيانات حقيقية | M3-S6 discovery (KhElmasry) | 2026-05-23 | M3-S6 discovery (scripts/discover_m3s6_drilldown.py) أثبت إن `rs.account.payment.installment` فاضي تماماً لعميل العيّنة (rank 1 KPI B / 76 قسط / 18.2M EGP): `partner_id` field موجود على الموديل لكن 0 records. `payment_line` (one2many) فاضي على كل 120 قسط للعميل. الأقساط الـ 8 "المدفوعة" كلها opening balances مستوردة — `write_date` = 2026-04-15 (لحظة bulk import، مش دفعة فعلية). استخدام `write_date` كـ fallback سيُظهر للـ Board تاريخ import كأنه آخر دفعة فعلية — مضلّل. القرار: "آخر دفعة" شالها خالد من نطاق M3-S6 (2026-05-23). تُعاد لما La Verde تكمّل إدخال payment events الحقيقية في `rs.account.payment.installment`. المرجع: MODULE_3_DRILLDOWN_PLAN.md §3.3. |
| D-5 | Module 4 محتمل — Accounting (أداء مالي / ربحية بالمشروع) | مؤجّل — blocked pending data migration | Accounting discovery (KhElmasry) | 2026-05-25 | Discovery أكّد إن Odoo Accounting هيكلياً ممتاز: 9,158 قيد مؤكّد، 340 حساب (Odoo 16 style)، 68 يومية بنك/خزينة، هرم تحليلي 2,167 حساب للمشاريع الثلاثة (New Capital / Cassette / La Puerta) بمستويات Project > Phase > Zone > Building > Unit. لكن البيانات الرقمية ناقصة في 3 مناطق حرجة: (1) المصروفات = 0 EGP — 85 حساب expense في دليل الحسابات لكن 0 قيود، لسه ما انتقلتش في الـ migration؛ (2) الـ analytic line amounts = 0 — 1,977 سطر تحليلي مربوط بالـ GL عبر `move_line_id` لكن `amount` = 0.00 EGP على كلهم؛ (3) الكاش one-sided — credit = 0 على كل الحسابات (opening balances فقط). La Verde في نص migration من أريب، الفترات المحاسبية مش مقفولة (fiscalyear_lock_date + tax_lock_date: NOT SET). شرط إعادة التقييم: discovery تاني بعد (أ) إدخال المصروفات، (ب) تملية الـ analytic amounts أو تأكيد طريقة استرجاعها من GL، (ج) إقفال أول فترة أو قرار La Verde بالاستعداد للعرض. السكربت جاهز لإعادة الاستخدام: `scripts/discover_accounting_phase1.py`. المراجع: `docs/ACCOUNTING_DISCOVERY.md` + **`docs/MODULE_4_PLAN.md`** (الخطة المفاهيمية المعتمدة 2026-05-25 — تحدّد الـ KPIs والأسئلة وإطار التنفيذ على مرحلتين Phase A / Phase B). مرفوع 2026-05-25. |
| D-7 | Cross-module Overview page (board summary) | Low priority — future enhancement | Nav Audit (KhElmasry) | 2026-06-10 | The pre-N1 "Overview" section header and nav label implied a cross-module summary board. No such page existed — both sidebar entries pointed to `/dashboard` (CRM-only). Decision 17.1 removed the label ambiguity. If a genuine multi-module overview board is wanted in the future, it needs a new route, a new template, and a new sidebar section — it cannot be achieved by relabeling `/dashboard`. |
| D-8 | Migrate remaining verify_*_live.py scripts to session-cookie auth | Important before next live-verify run — 16 remaining still get HTTP 401 | Session 18 / N2 Phase 0.4 audit (KhElmasry) | 2026-06-10 | Post-A2 the app is session-cookie-only (`get_current_user`, backend/api/deps.py:16-21); the old `auth=(USERNAME, PASSWORD)` HTTP Basic pattern returns 401. The shared helper exists: `scripts/_lib/api_session.py` (`login()` → httpx.Client with session cookie; env `VERIFY_USERNAME`/`VERIFY_PASSWORD`; ONE login per process — `/login` is rate-limited 10/minute). Pattern reference: `diagnose_ca_drilldown_anomaly.py` Section G and `verify_ca_drilldown_fix_live.py` (both migrated/built in Session 18). **✅ Migrated 2026-06-11 (Session 19):** verify_kpi7_live.py — live run all-PASS (exit 0) after Decision 6.4 ritual; also gained Step 8b window-arithmetic cross-check (mirror of `_compute_bucket_ends`, kpi_service.py:1182-1217) + type_breakdown identity check + collapse-group aggregate-identity checks (June 2026 triple collapse month=quarter=half end 2026-06-30 confirmed live). **16 scripts to migrate:** verify_kpi1_live.py, verify_kpi2_live.py, verify_kpi3_live.py, verify_kpi4_live.py, verify_kpi5_live.py, verify_kpi5b_live.py, verify_kpi6_live.py, verify_kpi7_breakdown_live.py, verify_kpia_live.py, verify_kpib_live.py, verify_kpic_live.py, verify_kpi_a_headcount_live.py, verify_kpi_b_tenure_live.py, verify_kpi_c_payroll_risk_live.py, verify_kpi_d_department_cost_live.py, verify_refunds_live.py. Each script's direct-Odoo RPC sections are unaffected — only the FastAPI probe sections need the helper. Decision 18.1. |
| D-6 | ~~`backend/modules/collections/tests/test_stage7.py` — 22 test في مسار legacy لا يُشغَّل~~ **RESOLVED 2026-05-25** | غير عاجل | Discovered during D-1 (KhElmasry) | 2026-05-25 | **RESOLVED 2026-05-25.** الملف كان يحتوي 22 test (D-6 ذكر 13 بالخطأ — العدد الفعلي 22). تحليل التقاطع: 4 tests كانت مغطّاة بالكامل في D-1 → حُذفت. 18 tests فريدة → نُقلت لـ 3 ملفات في المسار الحيّ: `tests/unit/modules/collections/test_installment_type_names.py` (ملف جديد، 6 tests AR mapping)، `test_drilldowns.py` (+5 tests `_serialize_row`)، `test_kpi_service.py` (+7 tests `_fetch_bucket_type_breakdown`). `test_serialize_row_all_required_fields_present` حُدِّث ليشمل `installment_type_name_en` (أُضيف في D-1، لم يكن في Stage 7). الملف القديم حُذف. كل الـ 18 test اجتازت. Full suite بعد النقل: 221 passed. |

---

## مرجع حالة البيانات

**`docs/LA_VERDE_DATA_STATE.md`** هي المرجع الموحّد لحالة بيانات La Verde في Odoo:
أي منطقة مكتملة، أي منطقة انتقالية، وشروط إعادة التقييم للبنود المؤجّلة بسبب البيانات (D-4، D-5).
راجعها قبل الشروع في أي feature مؤجّل أو discovery جديد يتعلّق بالبيانات.

---

## Maintenance instructions

UPDATE THIS DOCUMENT at the close of every stage:
1. Change the Stage's status from 🔄 to ✅
2. Fill in the actual tag, session range, commit count, and key output
3. Add the new tag to the Rollback Tags table
4. Update Current Numbers Baseline if any KPI value changed
5. Update "Last updated" date at the top
