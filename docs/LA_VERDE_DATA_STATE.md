# حالة بيانات La Verde في Odoo

> **آخر تحديث:** 2026-05-28 (§2.4، §2.5، §3، §4 D-5 — بعد discovery موجَّه)
> **الغرض:** مرجع واحد لحالة بيانات La Verde في Odoo — أي منطقة مكتملة، أي منطقة انتقالية، وما الذي يجب أن يتغيّر قبل العودة للبنود المؤجّلة.
> **هذه الوثيقة:** وصف للبيانات فقط، لا كود، لا feature design.

---

## 1. السياق

La Verde انتقلت إلى Odoo من نظام أريب. البيانات لم تُدخَل دفعةً واحدة — بعض المناطق اكتملت وتعمل يومياً، وبعضها مستورد كـ opening balances فقط، وبعضها لسه ما انتقلش. هذا الواقع يؤثر مباشرةً على ما يمكن بناؤه الآن وما يجب تأجيله.

الوثيقة دي تجمّع كل ما تأكّد عن حالة البيانات عبر discovery sessions متعددة — عشان أي session جديدة تبدأ بصورة كاملة بدل ما تدوّر في وثائق متفرّقة.

---

## 2. خريطة حالة البيانات

### 2.1 `rs.installment` — أقساط البيع

**الحالة: مكتملة + حيّة (live — moving daily)**

| المقياس | القيمة (baseline 2026-05-23) |
|---------|-------------------------------|
| إجمالي السجلات (`state='post'`) | 42,413 |
| إجمالي المستحق (KPI A baseline) | 2,634,209,716.28 EGP |
| إجمالي المتأخر (KPI B baseline) | 333,271,714.40 EGP |

**مصدر البيانات:**
- Bulk import **أبريل 2026**: كامل قاعدة البيانات التاريخية (42,413+ سجل) أُدخلت في يوم واحد — `write_date` = 2026-04-15 على جميعها.
- عمليات يومية منتظمة تضاف بعد الـ import.

**تحذير واحد — `write_date` غير صالح كـ trend axis:**
الـ bulk import كتب 26,110 سجل في يوم واحد. أي groupby على `write_date:month` يُرجع 2.97B EGP في أبريل 2026 وحده — وهو رقم مضلّل. استخدم `date` (تاريخ الاستحقاق المُدخَل يدوياً) لأي تحليل زمني.

**التأثير على المشروع:**
- KPI 1 / KPI 2 / KPI 5 / KPI 7 (Module 2) — أرقام تتحرّك يومياً. **طبيعي ومتوقّع.**
- KPI A / KPI B (Module 3) — نفس الحركة.
- Baselines المُوثَّقة في discovery docs هي نقاط قياس لحظية، مش أرقام ثابتة.

---

### 2.2 `rs.account.payment.reconcile` — محفظة العملاء (Wallet)

**الحالة: مستوردة + حيّة (migration 2026-05-17 + تطبيق نشط)**

| المقياس | القيمة |
|---------|--------|
| إجمالي السجلات (`state='post'`) | 205 |
| نوعها كلها | `type='advance_payment'` (Scenario A) |
| رصيد محفظة غير مخصّص (KPI C baseline, 2026-05-23) | 17,214,301.92 EGP / 27 عميل |
| استردادات (amount < 0) | 7 سجلات / −719,812 EGP |

**مصدر البيانات:**
- Migration request واحد: `RR/2026/05/00002` (create_date: 2026-05-17) — كل الـ 205 سجل تنتمي له.
- الـ request لسه `state='new'` (لم يُغلق رسمياً منذ الـ migration).
- البيانات حيّة: بين 2026-05-22 و 2026-05-23 طُبّق 44,573 EGP من محافظ عملاء على أقساط — يعني النظام يُستخدم فعلياً.

**ملاحظات البنية:**
- `rs.account.payment.reconcile.line` (sub-lines) = **0 سجلات** — غير مستخدمة حتى الآن.
- `type='termination_payment'` = **0 سجلات** — Scenario B (نقل ملكية) لم يُسجَّل بعد عبر هذا النموذج.

**التأثير على المشروع:**
- KPI C (Module 3): رقم متحرّك بطبعه — لا baseline ثابت. التحقق عند التطبيق يكون دائماً ضد Odoo UI في نفس اللحظة.
- قسم الاستردادات (Module 3): 7 سجلات مستقرة نسبياً (تواريخ 2025 — ليست migration artifact).

---

### 2.3 `rs.account.payment.installment` — سجلات الدفع الفعلية (Payment Events)

**الحالة: فاضية فعلياً — لا payment events حقيقية مسجّلة**

**ما وجده الـ discovery (M3-S6، 2026-05-23):**
العميل الأول في قائمة KPI B (76 قسطاً، 18.2M EGP متأخر) — تمت مراجعته يدوياً:
- `partner_id` field موجود على النموذج.
- لكن: **0 records** تخصّه في `rs.account.payment.installment`.
- الأقساط الـ 8 "المدفوعة" (`payment_state='paid'`) في `rs.installment` هي **opening balances مستوردة**: `write_date = 2026-04-15` (لحظة bulk import، مش دفعة فعلية من العميل).

**النتيجة:** استخدام `write_date` كـ fallback لـ"آخر دفعة" سيعرض للـ Board تاريخ import كأنه تاريخ دفعة — مضلّل.

**التأثير على المشروع:**
- **D-4 blocked:** ميزة "آخر دفعة" في Customer Drill-Down (Module 3) مؤجّلة لحد ما La Verde تكمّل إدخال payment events الحقيقية.
- باقي Module 3 غير متأثّر.

---

### 2.4 `account.move` / `account.move.line` — الدفتر العام

**الحالة: انتقالية — migration جزئي، أرقام P&L ناقصة**

| المقياس | القيمة |
|---------|--------|
| إجمالي القيود (`state='posted'`) | 9,158 |
| نوع القيود | **كلها `entry`** — لا `out_invoice`، لا `in_invoice` |
| دليل الحسابات | 340 حساب (Odoo 16+ style، 18 نوع) |
| اليوميات | 88 (50 bank + 18 cash + 18 general + 1 purchase + 1 sale) |

**ما هو موجود:**
- حسابات إيراد جزئية: 14 حساب income/income_other، 173 سطر فعلي، إجمالي **2,244,632 EGP**.
- 68 يومية بنك/خزينة مرتبطة بحسابات.
- **attribution المشروع (Path C):** `project_id` على `account.move` مملوء لـ 8,966 / 9,158 قيود (97.9%). يسمح بحساب per-project P&L مباشرةً من GL — `account.move.line` WHERE `move_id.project_id = X`. لا mapping table، لا analytic amounts. 192 قيد بدون `project_id` = "غير مخصّص" (قرار تصميم لاحق). تفاصيل: `scripts/discover_project_accounting_link.py` (2026-05-28).

**ما هو ناقص:**

| المنطقة | التفاصيل |
|---------|---------|
| **المصروفات** | 85 حساب expense/expense_direct_cost/expense_depreciation في دليل الحسابات — **0 قيود** على أي منهم. |
| **الكاش (one-sided)** | كل حسابات البنك/الخزينة: `credit = 0.00` بدون استثناء. رصيد ~584.86M EGP = opening balances مُدخلة كـ debits فقط. |
| **فترات الإقفال** | `fiscalyear_lock_date`: NOT SET. `tax_lock_date`: NOT SET. كل الفترات مفتوحة. |

**Anomalies في البيانات:**
- قيد بتاريخ **1015-05-31** (ref: BNK28/1015/00001) — تاريخ خاطئ واضح من migration.
- قيد بتاريخ **2026-10-22** (ref: MISR-/2026/00001) — تاريخ مستقبلي (وقت discovery: 2026-05-25).

**التأثير على المشروع:**
- **D-5 blocked:** Module 4 (الأداء المالي / ربحية بالمشروع) مؤجّل بالكامل — تفاصيل في §4.

---

### 2.5 `account.analytic.account` / `account.analytic.line` — المحاسبة التحليلية

**الحالة: هيكل مكتمل — أرقام فاضية**

| المقياس | القيمة |
|---------|--------|
| حسابات تحليلية | **2,167** |
| سطور تحليلية | **1,979** |
| منهم مربوط بـ GL (`move_line_id != False`) | **1,977** |
| إجمالي `amount` على كل السطور | **0.00 EGP** |

**الهيكل التحليلي — ممتاز:**
5 مستويات كاملة للمشاريع الثلاثة:
```
Project → Phase → Zone → Building → Unit
────────────────────────────────────────
New Capital  : Phase 1 / Phase 2 / Phase 3
Cassette     : Phase 1
La Puerta    : Phase 4
```
مثال: `Project#New Capital / Phase#2 / Zone#2 / Building#6 / Unit#BF175-6-301`

**المشكلة:** 1,977 سطر موجود ومربوط بقيود GL عبر `move_line_id`، لكن حقل `amount` = 0.00 على الكل. يعني الـ per-project P&L عبر analytic غير متاح.

**محلول بديل — Path C (محدَّد 2026-05-28):** per-project P&L لا يحتاج analytic amounts. `project_id` على `account.move` هو مسار الـ attribution المباشر (97.9% coverage). تفاصيل: §2.4 أعلاه و`docs/ACCOUNTING_DISCOVERY.md §12`.

**مسارات الربط — ثلاثة (discovery 2026-05-28):**

| المسار | الآلية | الحالة |
|--------|--------|--------|
| **Path C (أساسي)** | `project_id` على `account.move` → GL query مباشر | ✓ جاهز هيكلياً — محجوب بالمصروفات فقط |
| **Path A (احتياطي)** | `analytic_plan_id` على المشروع → analytic plan → child plans → analytic accounts | محجوب — amounts = 0، child plans غير مؤكّدة |
| **Path B (مرفوض)** | خريطة GL accounts على المشروع (23 حقل) | مرفوض — الحسابات مشتركة بين المشاريع |

**السؤال المفتوح الأهم:** لما المصروفات تتدخل، هل ستحمل `project_id` على القيد (Path C يشتغل تلقائياً) أم ستربط عبر analytic فقط (Path A ضروري)؟

**التأثير على المشروع:**
- **D-5 — per-project P&L:** مؤجّل — الحاجز الوحيد المتبقّي هو دخول المصروفات في GL (مش analytic amounts).

---

## 3. جدول ملخّص

| المنطقة | Odoo Model | الحالة | قابل للاستخدام؟ | البنود المتأثّرة |
|---------|-----------|--------|----------------|-----------------|
| أقساط البيع | `rs.installment` | ✅ مكتملة + حيّة | نعم (أرقام تتحرّك يومياً) | KPI 1/2/5/7 (M2)، KPI A/B (M3) |
| محفظة العملاء | `rs.account.payment.reconcile` | ✅ مكتملة + حيّة | نعم (KPI C متحرّك بطبعه) | KPI C، Refunds (M3) |
| سجلات الدفع | `rs.account.payment.installment` | ❌ فاضية فعلياً | لا — opening balances فقط | D-4 |
| الدفتر العام | `account.move(.line)` | ⚠️ انتقالية | جزئياً (إيرادات فقط) | D-5 (كامل) |
| المحاسبة التحليلية | `account.analytic.*` | ⚠️ هيكل فقط / amounts=0 | Path C يتجاوزها (project_id على account.move) | D-5 — Path C جاهز هيكلياً |

---

## 4. شروط إعادة التقييم للبنود المؤجّلة بسبب البيانات

### D-4 — "آخر دفعة" (Customer Drill-Down, Module 3)

**الشرط الوحيد:**
`rs.account.payment.installment` تتملأ بـ payment events حقيقية من عمليات La Verde اليومية — أي سجلات بـ `write_date` مختلفة (مش 2026-04-15 كلها).

**كيف تتحقق:** discovery بسيط — `search_count(rs.account.payment.installment, [('partner_id', '=', X)])` لأحد عملاء KPI B. لو الرقم > 0 وـ `write_date` متنوّع، D-4 يُعاد تقييمه.

**المرجع:** `docs/MODULE_3_DRILLDOWN_PLAN.md §3.3`، `docs/MODULE_2_STAGE_TRACKER.md D-4`.

---

### D-5 — Module 4: الأداء المالي / ربحية بالمشروع

ثلاثة شروط **مستقلة** — كل شرط يفتح جزءاً مختلفاً من Module 4:

**الشرط 1 — P&L (إيرادات ومصروفات):**
حسابات المصروفات تتملأ: `search_count(account.move.line, [('parent_state','=','posted'), ('account_id.account_type','in',['expense','expense_depreciation','expense_direct_cost'])])` يرجع > 0.

**الشرط 2 — Per-project P&L (الأداء بالمشروع) — محدَّث 2026-05-28:**
Path C جاهز هيكلياً — `project_id` على `account.move` هو مسار الـ attribution (97.9% coverage). الشرط الآن:
- المصروفات تدخل GL (الشرط 1 أعلاه).
- **تأكيد** إن المصروفات ستحمل `project_id` على القيد — يتحدّد من أول فاتورة مصروف حقيقية في production.
- لو `project_id` غائب على المصروفات → Path A (analytic) ضروري لها — يُعاد التقييم حينها.

**الشرط 3 — موثوقية الأرقام للـ Board:**
فترة محاسبية واحدة على الأقل تُقفل (`fiscalyear_lock_date` يتملأ)، **أو** خالد يقرر إن اللحظة مناسبة لعرض أرقام مؤشّرية مع disclaimer.

**كيف تتحقق:** إعادة تشغيل `scripts/discover_accounting_phase1.py` للشرط الأول (expense lines > 0). بعدها، قيد مصروف عيّنة واحدة يُجيب على الشرط الثاني (هل يحمل `project_id`؟).

**المرجع:** `docs/ACCOUNTING_DISCOVERY.md`، `docs/MODULE_2_STAGE_TRACKER.md D-5`.

---

## 5. المراجع

| الموضوع | الوثيقة |
|---------|---------|
| Accounting discovery كامل (GL + analytic + cash) | `docs/ACCOUNTING_DISCOVERY.md` |
| Reconcile / محفظة العملاء — discovery Phase 3 | `docs/MODULE_3_DISCOVERY_PHASE_3.md` |
| KPI C baseline + ملاحظة "moving number" | `docs/MODULE_3_DISCOVERY_M3S1.md §5` |
| D-4 (آخر دفعة) — قرار التأجيل | `docs/MODULE_3_DRILLDOWN_PLAN.md §3.3` |
| جميع البنود المؤجّلة (D-1 إلى D-5) | `docs/MODULE_2_STAGE_TRACKER.md §Pending` |
| rs.installment write_date anomaly (Finding D) | `docs/MODULE_2_IMPLEMENTATION_DECISIONS.md §D0` |
| سكربت discovery الـ accounting (لإعادة التشغيل) | `scripts/discover_accounting_phase1.py` |
