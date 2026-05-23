# Module 3 — Customer Accounts (حسابات العملاء)
## خطة معتمدة — Approved Plan v1

> **الحالة:** معتمدة من خالد 2026-05-22 بعد مراجعة الشكل البصري. جاهزة للتحويل لـ prompts.
> **التاريخ:** 2026-05-22
> **مرحلة المشروع:** بعد Module 2 (Collections) complete، بعد Phase 3 Reconcile discovery complete.

> **القرارات المعتمدة (2026-05-22):**
> - الـ layout: 3 KPIs فوق + قائمة KPI B + قسم استردادات تحت.
> - KPI B يُعرض كنسبة تركيز ("أعلى 10 = X%") مش كرقم فلوس — لأن رقم الفلوس موجود في Collections KPI 2.
> - قسم الاستردادات = بطاقة تنبيه تحت، مش KPI.
> - اسم الـ module ومدخل الـ sidebar: قرارات frontend، تُحسم في M3-S5.

---

## 1. الهدف والنطاق

**Module 3 = صفحة "حسابات العملاء" — Board-level.**

صفحة مستقلة في الـ sidebar، زي CRM والتحصيلات. منظور العميل، مش منظور القسط.

الفرق الجوهري عن Module 2: التحصيلات بتعدّ **أقساط**؛ حسابات العملاء بتعدّ **عملاء**. الوحدة الأساسية للتجميع = العميل (`partner_id`).

### داخل النطاق

- 3 KPIs Board-level (تفصيلهم في §3).
- قسم استردادات (تفصيله في §4).
- صفحة مستقلة بمدخل خاص في الـ sidebar.
- read-only — زي كل المشروع.

### خارج النطاق (صريح — لمنع الـ scope creep)

- **مش أداة تشغيلية.** مش بحث عن عميل بعينه كوظيفة أساسية. الـ Board بيشوف نظرة مجمّعة.
- **مش كشف حساب فردي كصفحة رئيسية.** "العميل دفع كام" (له) مش سؤال Board-level — يبقى في drill-down على الأكثر.
- **مش تفسير سبب الاسترداد تلقائياً.** الـ reconcile model مفيهوش حقل سبب (تأكد في Phase 3). الصفحة تعرض إن فيه استرداد، مش "ليه".
- **مفيش كتابة لـ Odoo.** مطلق.

---

## 2. مصادر البيانات

| المصدر | الموديل | الاستخدام في Module 3 |
|--------|---------|------------------------|
| الأقساط | `rs.installment` | تجميع المستحق/المتأخر لكل عميل |
| المحفظة (reconcile) | `rs.account.payment.reconcile` | رصيد غير مخصّص + استردادات |
| العميل | `res.partner` | الـ partner_id — رابط مشترك بين الموديلين |

**ملاحظة مهمة من Phase 3:** الـ `partner_id` هو نفس اسم الحقل على `rs.installment` و على `rs.account.payment.reconcile`. ده بيخلّي التجميع لكل عميل ممكن عبر الموديلين بنفس المفتاح.

**تنبيه على الاستردادات:** `rs.account.payment.reconcile` فيه نوعين تدفّق — `amount` موجب (مدفوعات داخلة) و `amount` سالب (استردادات). الـ `payment_type` field غير موثوق للتمييز — إشارة `amount` هي المؤشّر. ده مؤكّد في Phase 3 §4.1.

---

## 3. الـ 3 KPIs

> كل KPI هنا **مبدئي**. الـ domain النهائي والـ baseline لازم يتأكّدوا بـ discovery قبل أي كود — زي ما حصل في كل KPI في Collections.

### KPI A — إجمالي المستحق على العملاء / Total Customer Receivables

**السؤال:** "إجمالي اللي على العملاء كام، وعلى كام عميل؟"

**التجميع المبدئي:** `SUM(rs.installment.due_amount)` على كل العملاء، مع `read_group` groupby `partner_id` لعدّ العملاء المتميّزين.

**الـ domain المتوقّع:** نفس domain الـ portfolio (`state = 'post'`). **يحتاج تأكيد discovery.**

**العرض:** رقم كبير (إجمالي EGP) + عدد العملاء.

**ملاحظة:** ده منظور العميل لنفس البيانات اللي KPI 1 في Collections بيشوفها من منظور القسط. مش تكرار — رقم مختلف (عدد عملاء بدل عدد أقساط) وتجميع مختلف.

**verification:** identity-equal ضد Odoo UI — تجميع `due_amount` لازم يطابق.

---

### KPI B — أعلى العملاء تأخّراً / Top Overdue Customers

**السؤال:** "مين العملاء الأخطر — وهل المخاطرة مركّزة ولا موزّعة؟"

**التجميع المبدئي:** `read_group` على الـ Late domain (نفس domain Candidate C ثلاثي البنود من Collections KPI 2) groupby `partner_id`، مرتّب بالمبلغ المتأخر تنازلياً، أعلى 10–20.

**الـ domain المتوقّع:** الـ Late domain المعتمد من Module 2 — `state='post'` + `payment_state in [unpaid,partial]` + `date < today`. **يحتاج تأكيد إنه نفسه ينطبق على التجميع بالعميل.**

**العرض:** قائمة/جدول أعلى العملاء، كل صف: اسم العميل، المبلغ المتأخر، عدد الأقساط المتأخرة. + سطر يوضّح التركيز ("أعلى 10 عملاء = X% من إجمالي التأخير").

**verification:** مجموع كل العملاء المتأخرين لازم يطابق KPI 2 في Collections (نفس الـ domain، تجميع مختلف).

---

### KPI C — رصيد المحفظة غير المخصّص / Unallocated Wallet Balance

**السؤال:** "كام فلوس داخلة الشركة بس لسه معلّقة على العملاء؟"

**التجميع المبدئي:** `SUM(rs.account.payment.reconcile.residual_amount)` WHERE `state='post'` AND `residual_amount > 0`، مع عدّ العملاء المتميّزين.

**ملاحظة حرجة:** الفلتر `residual_amount > 0` مقصود — بيستثني الـ 7 records السالبة (الاستردادات). الاستردادات ليها قسمها الخاص (§4). لو جمعنا كله على عماه، الاستردادات هتطرح من الرقم وتدّي الـ Board رقم مضلّل.

**العرض:** رقم كبير (إجمالي الرصيد المعلّق) + عدد العملاء اللي عندهم رصيد.

**ملاحظة:** ده السؤال الوحيد اللي مفيش KPI في Collections بيلمسه. القيمة المضافة الحقيقية للـ module.

**verification:** identity-equal ضد Odoo "Payments to Reconcile" UI، مفلتر `residual_amount > 0`.

---

## 4. قسم الاستردادات

**مش KPI — قسم تنبيه واحد.**

**المصدر:** `rs.account.payment.reconcile` WHERE `state='post'` AND `amount < 0`.

**العرض المقترح:** بطاقة تنبيه واحدة — "استردادات: إجمالي X جنيه، عدد Y، منهم Z لعملاء غير معروفين".

**ليه قسم منفصل ومش KPI:**
- العدد صغير (7 records وقت الـ discovery).
- 4 من الـ 7 "عميل غير معروف" — مش بينتموا لعميل محدد، فمش بيدخلوا حساب عميل بعينه.
- الـ reconcile model مفيهوش حقل سبب — الصفحة تعرض الحقيقة (فيه استرداد بقيمة كذا) من غير محاولة تفسير "ليه".

**القرار المؤجّل (من Phase 3 §4.1):** هل نعرض الاستردادات لعملاء معروفين كخصم من حسابهم، ولا منفصلة تماماً؟ — يتحسم في تصميم الـ frontend stage.

---

## 5. تقسيم الـ Stages المقترح

نفس نمط Collections: discovery أول، backend KPI by KPI، frontend آخر حاجة.

| Stage | المحتوى | النوع | الحالة |
|-------|---------|-------|--------|
| **M3-S1** | Discovery — تأكيد domains الـ 3 KPIs ضد الـ live، تأكيد الـ baselines، إغلاق OQ2/OQ4 من Phase 3 | discovery بحت | ✅ COMPLETE — commit 00f3abf، 2026-05-23. KPI A = 2.63B/1272، KPI B = 333.3M/797/top10=21.8%، KPI C = 17.2M/27 (متحرّك). R1a/R1b pass. |
| **M3-S2** | Backend KPI A — module scaffold + خدمة + endpoint + tests + verification | backend | ✅ COMPLETE — commit 19b5283، 2026-05-23. value=2,634,209,716.28 EGP / 1,272 عميل / 42,413 قسط. delta=0.00 vs baseline. 11 unit tests pass. verified on fresh server (uptime=26.6s، cache_status=fresh). |
| **M3-S3** | Backend KPI B | backend | ✅ COMPLETE — commit 8876dda، 2026-05-23. total_overdue=333,271,714.40 EGP / 797 عميل / top10=21.8% (21.77% مطابق، اختلاف عرض الخانات فقط). 14 unit tests pass. verified fresh server delta=0.00. |
| **M3-S4** | Backend KPI C + قسم الاستردادات | backend | ✅ COMPLETE — 2026-05-23. KPI C = 17,214,301.92 EGP / 27 عميل / 198 record (identity delta=0.00). Refunds = −719,812 EGP / 7 records / 0 null-partner (identity delta=0.00). 26 unit tests pass. verified on fresh server (cache_status=fresh, both identity deltas=0.0000 EGP). |
| **M3-S5** | Frontend — صفحة حسابات العملاء، الـ 3 كروت + قسم الاستردادات + مدخل sidebar | frontend | ⏳ pending |

**ملاحظة على M3-S1:** ده مش نفس Phase 3 discovery. Phase 3 اكتشف الـ reconcile model. M3-S1 بيأكّد domains الـ KPIs و baselines — زي pre-implementation discovery اللي كل KPI في Collections كان بيعمله.

---

## 6. المخاطر والأسئلة المفتوحة

| # | المخاطرة / السؤال | لازم يتحسم |
|---|-------------------|-----------|
| R1 | الـ Late domain اتأكّد على مستوى القسط — هل ينطبق بنفس الدقّة على التجميع بالعميل؟ | M3-S1 discovery |
| R2 | OQ2 (reconcile_line فاضي) و OQ4 (دور الـ request) من Phase 3 لسه مفتوحين | M3-S1 discovery |
| R3 | الاستردادات لعملاء معروفين — تُخصم من حسابهم ولا تُعرض منفصلة؟ | M3-S5 تصميم |
| R4 | بيانات الـ reconcile طازة (أُدخلت bulk 2026-05-17) — الأرقام ممكن تتغير زي بيانات Collections | متابعة، مش blocker |
| R5 | "عميل غير معروف" في الاستردادات — إزاي يتعرض من غير ما يبان غلط للـ Board | M3-S5 تصميم |

---

## 7. القرارات المتبقية (frontend — تُحسم في M3-S5)

1. ✅ الـ 3 KPIs بالأسماء والتعريفات — معتمدة.
2. ✅ تقسيم الـ 5 stages — معتمد.
3. ⏳ اسم الـ module ("Customer Accounts / حسابات العملاء") — يُحسم في M3-S5.
4. ⏳ مدخل الـ sidebar (جديد ولا من الـ "Soon" الموجودين) — يُحسم في M3-S5.

---

*الخطة معتمدة. Stage M3-S1 (discovery) يتحوّل لـ prompt session-spawning لـ Claude Code — أول session في Module 3.*
