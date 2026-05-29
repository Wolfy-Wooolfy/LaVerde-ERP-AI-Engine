# Module 5 — HR Dashboard (لوحة الموارد البشرية)
## خطة مقترحة — Plan v1 — Pending Approval

> **الحالة:** مقترحة. في انتظار موافقة خالد.
> **التاريخ:** 2026-05-29
> **مرحلة المشروع:** بعد Module 3 (حسابات العملاء). Phase 1 فقط — البيانات المستقرة.
> **مصدر الـ Discovery:** `docs/HR_CLUSTER_DISCOVERY.md` commit 72718b4 — canonical run 2026-05-28T13:43:49Z (76 RPC calls). لا discovery جديد مطلوب لنطاق Phase 1.

> **ملاحظة مهمة على النطاق:**
> Module 5 يُبنى على مرحلتين. هذه الخطة تُغطي **Phase 1 فقط** — البيانات المستقرة
> (الموظفون، الإدارات، الوظائف، العقود). Phase 2 (الحضور، الرواتب، الإجازات،
> الأوفرتايم) مؤجّل لما بعد يونيو 2026 — بيانات الحضور الحالية test data كلها.

---

## 1. الهدف والنطاق

**Module 5 = صفحة "الموارد البشرية" — Board-level (Phase 1).**

صفحة مستقلة في الـ sidebar. منظور القوى العاملة — الـ Board بيشوف الصورة الكاملة:
عدد الموظفين وتوزيعهم، أقدميتهم، وموجة تجديد العقود السنوية القادمة.

### داخل النطاق (Phase 1)

- **KPI A:** الرأسمال البشري / Headcount — إجمالي الموظفين النشطين وغير النشطين،
  توزيع بالإدارة، توزيع بالوظيفة.
- **KPI B:** توزيع مدة الخدمة / Tenure Distribution — تجميع الموظفين النشطين
  في فترات خدمة بناءً على `first_contract_date`.
- **KPI C:** موجة تجديد العقود / Contract Renewal Wave — عدد العقود المستحقة
  للتجديد على تاريخ التجديد السنوي، مع breakdown بالإدارة.
- **قسم D:** جودة البيانات / Data Quality Summary — بطاقة تنبيه بالموظفين
  الناقصين بيانات والعقود المفتوحة (بدون تاريخ انتهاء).
- read-only — زي كل المشروع. مفيش كتابة لـ Odoo. مطلق.

### خارج النطاق (Phase 1 — صريح، لمنع الـ scope creep)

- **الحضور (`hr.attendance`):** 21,800 سجل test data — مؤجّل Phase 2.
- **الرواتب (`hr.payslip`):** AccessError على الـ API user + لا بيانات — مؤجّل Phase 2.
- **الإجازات (`hr.leave` / `hr.leave.allocation`):** فارغة — مؤجّل Phase 2.
- **الأوفرتايم (`hr.overtime.request`):** فارغ — مؤجّل Phase 2.
- **التوظيف (`hr.applicant`):** فارغ — مؤجّل Phase 2.
- **مأموريات (`mission.request`):** فارغة — مؤجّل Phase 2.
- **الإنهاءات (Terminations app):** خارج HR كلياً — cluster مختلف
  (Collections/Contracts discovery).
- **مش أداة تشغيلية.** الـ Board بيشوف نظرة مجمّعة — مش بحث أو كشف حساب
  لموظف بعينه.

---

## 2. مصادر البيانات

| المصدر | الموديل | الاستخدام في Module 5 |
|--------|---------|------------------------|
| الموظفون | `hr.employee` | KPI A (headcount) + KPI B (tenure) + قسم D (data quality) |
| الإدارات | `hr.department` | تجميع الـ headcount بالإدارة (KPI A) + breakdown التجديد (KPI C) |
| الوظائف | `hr.job` | تجميع الـ headcount بالوظيفة (KPI A) |
| العقود | `hr.contract` | موجة التجديد (KPI C) + عقد مفتوح (قسم D) |

**بيانات مؤكّدة من Discovery (commit 72718b4):**

| نقطة | القيمة | المصدر |
|------|--------|--------|
| موظفون نشطون | 136 | S3 — `active=True` |
| موظفون غير نشطين (مؤرشفون) | 24 | S3 — `active=False` |
| إدارات | 24 (هرمي حقيقي — مش flat) | S3.3 |
| وظائف | 84 | S1 |
| عقود مفتوحة (`state='open'`) | 136 | S4.1 |
| عقود منتهية (`state='close'`) | 13 | S4.1 |
| عقود بـ `date_end` = 30/06/2026 | 114 | S4.3 — موجة تجديد سنوية حقيقية |
| عقد بـ `date_end = False` | 1 | S4.4 — عقد مفتوح بدون تاريخ |

**حقل التاريخ المعتمد للـ Tenure:** `first_contract_date` — النطاق على الموظفين
النشطين: 2017-12-26 إلى 2025-11-17.

> **تنبيه:** `hire_date` غير موجود على `hr.employee` (تأكّد في discovery S3.5
> — الـ field scan لم يجده). أي KPI للأقدمية لازم يستخدم `first_contract_date`.

---

## 3. الـ KPIs

> كل KPI هنا **مبدئي**. الـ domain النهائي والـ baseline يتأكّدوا بـ verification
> على الـ live قبل أي commit — زي ما حصل في كل KPI في Collections و Module 3.

---

### KPI A — الرأسمال البشري / Headcount

**السؤال:** "إجمالي القوى العاملة كام — وكيف موزّعة بالإدارة والوظيفة؟"

**التجميع المبدئي:**
- نشطون: `search_count([('active', '=', True)])` → baseline 136.
- غير نشطين: `search_count([('active', '=', False)])` → baseline 24.
- بالإدارة (نشطون):
  `read_group([('active','=',True)], ['department_id'], ['department_id'], lazy=False)`
  → 24 مجموعة.
- بالوظيفة (نشطون):
  `read_group([('active','=',True)], ['job_id'], ['job_id'], lazy=False)`
  → 67 مجموعة.

**العرض:**
- بطاقة رئيسية: إجمالي الموظفين النشطين (136) + عدد غير النشطين (24).
- جدول الإدارات: أعلى الإدارات بالعدد (24 إدارة — هرمية، مش flat).
- جدول الوظائف: أعلى الوظائف بالعدد (67 وظيفة).

**قرار محسوم:** الموظفون بدون إدارة يظهرون كـ bucket "(بدون إدارة)" — لا
يُخفون. السبب: مجموع الـ breakdown لازم يساوي الإجمالي 136 (matches Odoo UI).

**verification:** مجموع الـ department breakdown = 136 (إجمالي النشطين). نطابق
ضد Odoo HR UI.

---

### KPI B — توزيع مدة الخدمة / Tenure Distribution

**السؤال:** "إيه توزيع الأقدمية بين الموظفين؟"

**التجميع المبدئي:** `search_read` على الموظفين النشطين بـ `first_contract_date != False`،
fields = `['id', 'first_contract_date']`، بعدين حساب المدة من `first_contract_date`
إلى today في الـ backend (Python/FastAPI) وتصنيفها في فترات:

| الفترة | الوصف |
|--------|-------|
| أقل من سنة | حديث التعيين |
| 1–3 سنوات | — |
| 3–5 سنوات | — |
| 5–10 سنوات | — |
| 10+ سنوات | قدامى |

**الـ domain المتوقّع:**
`[('active', '=', True), ('first_contract_date', '!=', False)]`.

**ملاحظة حرجة:** الحساب بيتم في الـ backend (Python) — مش في Odoo.
`read_group` بـ date trunc في Odoo مش مناسب هنا لأن الفترات مخصصة (1–3 سنة)
ومش بتطابق fiscal/calendar groupby مباشرة.

**العرض:** توزيع (قائمة أو أعمدة) بعدد الموظفين لكل فترة.

**ملاحظة:** الموظفون اللي `first_contract_date = False` عندهم يُستثنوا من الحساب
ويُبلَّغ عنهم في قسم D.

**verification:** مجموع كل الفترات + الموظفين بدون `first_contract_date` = 136
(إجمالي النشطين).

---

### KPI C — Contract Renewal: Payroll Risk Dashboard

**Purpose:** Surface payroll-blocking risk from contract expiry and HR ops
workload from onboarding limbo. NOT a renewal calendar.

**Threshold rationale:** 45/90/135 days — derived from La Verde HR's 45-day
labor-office response time (Khaled, 2026-05-29). NOT the industry default
30/60/90.

**Seven buckets — main response payload (all keyed to active employees):**

| # | Bucket | Domain | Baseline (2026-05-29) | Urgency |
|---|--------|--------|-----------------------|---------|
| 1 | **Active without contract** | `hr.employee` where `active=True` AND `employee.id` NOT IN (employee_ids of running contracts) | **17** | HR ops — onboarding limbo; pre-payroll state (by-design forcing function) |
| 2 | **Expired** | `hr.contract` where `state='open'`, `date_end < today`, employee active | **0** (expected) | HIGH — payroll-blocking alert if >0 |
| 3 | **Expiring ≤45d** | `state='open'`, `today <= date_end <= today+45`, employee active | — | MEDIUM — schedule renewal now |
| 4 | **Expiring 46–90d** | `state='open'`, `today+45 < date_end <= today+90`, employee active | — | Begin preparation |
| 5 | **Expiring 91–135d** | `state='open'`, `today+90 < date_end <= today+135`, employee active | — | Heads-up |
| 6 | **Beyond 135d** | `state='open'`, `date_end > today+135`, employee active | — | Stable |
| 7 | **Open-ended** | `state='open'`, `date_end = False`, employee active | **1** | No renewal needed |

**Sanity invariant (main payload):**
`sum(buckets 1..7)` == `search_count([('active','=',True)])` == **136** today.

**Separate data-quality metadata — NOT in the 7 main buckets:**

`orphan_contracts_count`: running contracts (`state='open'`) whose `employee_id`
is NOT active (`employee.active=False`). Today's baseline: **17** (verified
2026-05-29). Paperwork debt from exit workflow — HR cleanup item, NOT
payroll-blocking.

*Why separate:* The 7 main buckets describe the operational reality for 136 active
employees. Orphan contracts belong to ex-employees and are not part of that reality.
Mixing them into the main buckets would distort the Board's view. Showing them as
data-quality metadata flags the workflow gap without polluting the operational picture.

**Breakdown by department:** Expired and Expiring ≤45d buckets only (the two
actionable buckets). Active-without-contract is too small at MVP for a department
breakdown — defer.

**Reference date:** today in Cairo TZ (Africa/Cairo) — same rule as KPI B.

**Alert rules (priority order):**
- **HIGH** — Expired bucket > 0: active employees with payroll blocked until renewal.
- **MEDIUM** — Expiring ≤45d > 0: action window open; HR must schedule.
- **LOW** — Active-without-contract growing month-over-month: onboarding velocity signal.
- **INFO** — `orphan_contracts_count` growing: exit cleanup debt accumulating.

**Implementation (~3 RPCs):**
- RPC 1 — `search_read(hr.contract, [('state','=','open')], fields=['employee_id','date_end'])`
  → all ~153 running contracts (136 active-employee contracts + 17 orphan). Python
  partitions by whether `employee_id` is in the active set, then classifies
  active-employee contracts into buckets 2–7 by `(date_end − today).days` relative
  to 0/45/90/135.
- RPC 2 — `search_read(hr.employee, [('active','=',True)], fields=['id'])`
  → all 136 active employee IDs. Bucket 1 = `active_emp_set − {emp_id for c in
  active_contracts}`. (May reuse KPI A cached response if available — see R6.)
- RPC 3 — department `read_group` for Expired and ≤45d buckets when non-empty.

**Verification baselines (2026-05-29):**

| Item | Value |
|------|-------|
| Bucket 1 — active without contract | 17 |
| Bucket 7 — open-ended | 1 |
| `orphan_contracts_count` | 17 |
| sum(buckets 1..7) | 136 |

*Source: `scripts/verify_active_running_mapping.py` +
`logs/active_running_mapping.log`, verification run 2026-05-29 12:50:02Z.*

**Open decision R1 superseded:** Distance-to-expiry buckets replace the single-
renewal-date design. No hardcoded date needed. See §6 R1 and §7 item 1.

---

## 4. قسم جودة البيانات / Data Quality Summary

**مش KPI — قسم تنبيه واحد.**

**المصدر:** `hr.employee` (active + inactive) و `hr.contract`.

**الأرقام من Discovery (baseline):**

| الفجوة | العدد | الـ domain |
|--------|-------|-----------|
| موظفون بدون إدارة | 4 | `active in [True,False]`, `department_id=False` |
| موظفون بدون وظيفة | 3 | `active in [True,False]`, `job_id=False` |
| موظفون بدون مدير | 4 | `active in [True,False]`, `parent_id=False` |
| عقود مفتوحة (date_end=False) | 1 | `state='open'`, `date_end=False` |

**العرض المقترح:** بطاقة تنبيه واحدة —
"جودة البيانات: 4 موظفين بدون إدارة، 3 بدون وظيفة، 4 بدون مدير؛ 1 عقد مفتوح."

**ليه قسم منفصل ومش KPI:** هذه فجوات بيانات داخلية — قيمتها للـ Board إنهم
يعرفوا إن البيانات تحتاج مراجعة، مش إنهم يتعاملوا معها مباشرة.

**Drill-down في Phase 1:** counts only، بدون قائمة موظفين. البطاقة بتعرض
الأعداد المجمّعة بس ("4 موظفين بدون إدارة"). أي قائمة بأسماء/IDs تحتاج
قرار scope منفصل — الـ Board مش الجمهور المستهدف لقوائم تشغيلية. القرار
يتأجّل لـ M5-S5 إذا طُلب.

---

## 5. تقسيم الـ Stages المقترح

لا discovery جديد مطلوب — الـ 76 RPC calls من b7f8c61 تُغطي كل نطاق Phase 1.
نفس نمط Collections و Module 3: backend KPI by KPI، frontend آخر حاجة.

| Stage | المحتوى | النوع | الحالة |
|-------|---------|-------|--------|
| **M5-S1** | Backend KPI A — Headcount: module scaffold (`backend/modules/hr/`) + `kpi_service.py` + `schemas.py` + `router.py` + endpoint + tests + verification | backend | ⏳ Pending |
| **M5-S2** | Backend KPI B — Tenure Distribution: حساب الفترات في Python + endpoint + tests + verification | backend | ⏳ Pending |
| **M5-S3** | Backend KPI C — Contract Renewal Wave: عقود + breakdown بالإدارة + endpoint + tests + verification | backend | ⏳ Pending |
| **M5-S4** | Backend قسم D — Data Quality Summary: endpoint + tests | backend | ⏳ Pending |
| **M5-S5** | Frontend — صفحة HR، الـ 3 KPI cards + قسم D + drill-down panel + مدخل sidebar (desktop + mobile) | frontend | ⏳ Pending |

**ملاحظة على M5-S1 (scaffold):** البنية المقترحة:
```
backend/modules/hr/
    __init__.py
    router.py       ← مسارات الـ 4 endpoints
    schemas.py      ← Pydantic response models
    kpi_service.py  ← RPC calls + حسابات الـ tenure
```
نفس pattern بتاع Module 2 (Collections) و Module 3 (Customer Accounts) — بدون deviation.

---

## 6. المخاطر والأسئلة المفتوحة

| # | المخاطرة / السؤال | لازم يتحسم |
|---|-------------------|-----------|
| R1 | **تاريخ التجديد — Superseded 2026-05-29.** KPI C redesigned as a payroll-risk dashboard using 7 distance-to-expiry buckets (thresholds: 45/90/135 days from La Verde HR response time). Single renewal date is no longer central to the KPI design. No hardcoded date needed. See §3 KPI C. | ✅ Closed — M5-S3 redesign |
| R2 | **`first_contract_date = False`** — هل في موظفين نشطين بدون تاريخ غير الـ 4 gaps المعروفة؟ الـ backend لازم يتعامل مع الحالة دي (يستثنيها ويبلّغ في D). | M5-S2 |
| R3 | **`hr.payslip` AccessError** — لو خالد قرر منح الـ API user read access (discovery A1)، ممكن تُضاف payroll KPIs في Phase 2. مش مطلوب Phase 1. | Phase 2 تقرير |
| R4 | **الـ 13 close contracts vs 12 في الـ UI** — الـ discrepancy مش blocking لـ KPI C (بيشتغل على open فقط)، لكن لازم يتبان في verification report ومش يُتجاهل. | M5-S3 verification |
| R5 | **اسم الـ module ومدخل الـ sidebar** — "HR / الموارد البشرية" ولا اسم تاني؟ | M5-S5 |
| R6 | **KPI C implementation: reuse cached active employee IDs from KPI A, or independent RPC?** Reuse saves an RPC but creates coupling between KPI A and KPI C; independent RPC is cleaner architecturally but slightly slower. To be decided in M5-S3 build (D2). | M5-S3 D2 |

---

## 7. القرارات المتبقية

1. ✅ تاريخ التجديد — superseded. KPI C redesigned as 7-bucket payroll-risk dashboard (2026-05-29). See §3 KPI C.
2. ⏳ اسم الـ module ومدخل الـ sidebar — يُحسم في M5-S5.

---

*الخطة مقترحة — Phase 1 (stable data). Phase 2 (الحضور، الرواتب، الإجازات،
الأوفرتايم) في انتظار go-live يونيو 2026 — بيانات الحضور الحالية test data.*
