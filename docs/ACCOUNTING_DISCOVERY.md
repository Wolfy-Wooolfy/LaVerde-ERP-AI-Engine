# Accounting Module Discovery — Phase 1 Findings

> **Status:** Complete — awaiting Khaled review and go/no-go decision.
> **Discovery date:** 2026-05-25
> **Script:** `scripts/discover_accounting_phase1.py`
> **Output file:** `scripts/discover_accounting_phase1_2026-05-25.txt`
> **Cost:** $0.00 (no OpenAI calls, read-only RPCs only)
> **Questions answered:** 5 Board questions + timing recommendation.

---

## 1. الهدف

تقييم هل فيه Module 4 يستاهل يتبني فوق Odoo Accounting — يعرض معلومات Board-level مش مغطّاة بـ Collections (M2) + Customer Accounts (M3).

**النطاق خارج الـ discovery:** receivables / أقساط / محفظة → مغطّاة بالكامل في M2+M3، مش موضوع هنا.

---

## 2. السؤال الأول — البنية: هل Accounting مفعّل بعمق؟

**النتيجة: نعم، مفعّل بشكل حقيقي.**

| المقياس | القيمة |
|---------|--------|
| `account.move` — إجمالي | 9,442 |
| `account.move` — posted (مؤكّدة) | **9,158** |
| `account.move` — draft | 2 |
| `account.move` — cancelled | 282 |
| `account.account` (دليل الحسابات) | 340 |
| `account.journal` (يوميات) | 88 |

**نوع القيود:** كل الـ 9,158 قيد مؤكّد من نوع `entry` — لا `out_invoice` ولا `in_invoice`. يعني La Verde مش بتستخدم Odoo Invoicing — كل شيء قيود يدوية مباشرة في الدفتر العام. ده pattern طبيعي لشركة real estate بيانات migration.

**توزيع اليوميات:**

| النوع | العدد |
|-------|-------|
| bank (بنكية) | 50 |
| cash (خزينة) | 18 |
| general (عامة) | 18 |
| purchase | 1 |
| sale | 1 |

68 يومية بنك/خزينة — مؤشّر على أن La Verde عندها حسابات بنكية كثيرة (موزّعة بالمشروع/المرحلة).

---

## 3. السؤال 1ب — الحالة الزمنية + استقرار البيانات

**النتيجة الحرجة: البيانات في حالة انتقالية — مش مستقرّة بعد.**

### توزيع القيود بالسنة

| السنة | العدد |
|-------|-------|
| 1015 | 1 (**تاريخ خاطئ — anomaly migration**) |
| 2018–2024 | 849 (إجمالي) |
| 2025 | **6,580** — الغالبية العظمى |
| 2026 | 1,428 |

- **أقدم قيد:** 1015-05-31 (ref: BNK28/1015/00001) — تاريخ غلط واضح من migration.
- **أحدث قيد:** 2026-10-22 (ref: MISR-/2026/00001) — تاريخ مستقبلي (اليوم 2026-05-25) — anomaly أخرى.
- **الغالبية:** 85%+ من القيود سنة 2025 — يعني البيانات المدخلة حديثاً كلها 2025.

### فترات الإقفال

```
fiscalyear_lock_date:   NOT SET
tax_lock_date:          NOT SET
```

**لا توجد فترات مقفولة.** كل الفترات مفتوحة — أي رقم قابل للتغيير.

### مؤشّرات أخرى على حالة انتقالية

- بيانات الكاش one-sided: كل حسابات البنك/الخزينة عندها debit فقط وcredit=0. ده يعني entries بتسجّل إيداعات بس، مش مصروفات — opening balances أو migration جزئي.
- حسابات المصروفات 0 تماماً (تفصيل في §4 أدناه).

**الخلاصة:** البيانات تعكس الحالة اللحظية للـ migration. الأرقام مؤشّرية وستتغيّر.

---

## 4. السؤال الثاني — الربحية: نقدر نجاوب "الشركة كسبت ولا خسرت"؟

**النتيجة: هيكلياً نعم، عمليًا أرقام مرحلة الـ migration ناقصة.**

### دليل الحسابات — الأنواع الموجودة (Odoo 16 style)

| Account Type | عدد الحسابات | الدور |
|---|---|---|
| `income` | 6 | إيرادات |
| `income_other` | 8 | إيرادات أخرى |
| `expense` | 46 | مصروفات تشغيلية |
| `expense_direct_cost` | 32 | تكاليف مباشرة |
| `expense_depreciation` | 7 | إهلاك |
| **إجمالي إيرادات** | **14 حساب** | |
| **إجمالي مصروفات** | **85 حساب** | |

**الحسابات موجودة ومبنية في دليل الحسابات — يعني النظام مصمّم للـ P&L.**

### قيود P&L الفعلية (posted)

| الجانب | عدد السطور | Credit | Debit | Net |
|---|---|---|---|---|
| إيرادات (income + income_other) | 173 | 2,244,632 EGP | 0 | **2,244,632 EGP** |
| مصروفات (expense + depreciation + direct_cost) | **0** | 0 | 0 | **0 EGP** |

### ⚠️ Finding حرج: المصروفات = صفر تماماً

85 حساب مصروف موجود في دليل الحسابات، لكن **0 سطر** في `account.move.line` عليهم.

هذا مستحيل في شركة تعمل فعلياً — الاحتمالات:
1. **مصروفات لسه ما انتقلتش** في الـ migration (الأرجح).
2. مصنّفة تحت `asset_current` أو نوع آخر بدل `expense`.
3. لسه بتتدخل يدوياً.

**التأثير على Module 4:** لو بنبني P&L دلوقتي، هيظهر "ربح 2.24M EGP" بدون أي مصروفات — وده رقم مضلّل للـ Board. P&L مش جاهز للعرض.

### توزيع إيرادات السنين

| السنة | سطور | Credit EGP |
|-------|------|-----------|
| 2023 | 1 | 1,961 |
| 2024 | 5 | 46,847 |
| 2025 | 166 | 2,183,882 |
| 2026 | 1 | 11,942 |

---

## 5. السؤال الثالث — الكاش: نقدر نجاوب "فيه كاش قد إيه"؟

**النتيجة: هيكلياً نعم، لكن الأرقام one-sided.**

### اليوميات البنكية/الخزينة

68 يومية (50 bank + 18 cash) — مزوّدة بـ default_account_id لكل يومية.

**ملاحظة:** `account_type = 'asset_bank'` = 0 حساب في دليل الحسابات، و`asset_cash` = 68 حساب. يعني La Verde صنّفت حسابات البنك كـ `asset_cash` مش `asset_bank` — ممكن يكون configuration أو version issue.

### أرصدة الحسابات (أكبر 10)

| Account ID | Balance EGP |
|---|---|
| 849 (CIB-E) | 134,153,822 |
| 1330 (BNK28) | 116,507,738 |
| 233 (BNK2) | 102,470,904 |
| 859 (MISR-) | 88,374,071 |
| 895 (CSH1) | 65,634,750 |
| ... | ... |
| **إجمالي** | **~584,860,071 EGP** |

### ⚠️ Finding: أرقام one-sided

كل حسابات الكاش/البنك: `credit = 0.00 EGP` بدون استثناء. يعني الأرصدة دي مصدرها **فقط** إيداعات وارد، ومش فيه أي سحب أو مصروف خارج مسجّل.

**الخلاصة:** رصيد 584M EGP يمثّل على الأرجح opening balances مدخلة كـ debits في الـ migration، مش رصيد شغّال فعلي. البنك مش كامل بعد.

---

## 6. السؤال الرابع — البُعد التحليلي: نقدر نعرض الأرقام لكل مشروع؟

**النتيجة: البنية موجودة وممتازة — لكن الأرقام فاضية في المرحلة دي.**

### هيكل الـ Analytic Accounts

2,167 حساب تحليلي منظّم في هرم 5 مستويات:

```
Project
  └── Phase
        └── Zone
              └── Building
                    └── Unit
```

المشاريع الثلاثة موجودة بالكامل:
- **New Capital**: Phase 1 / Phase 2 / Phase 3
- **Cassette**: Phase 1
- **La Puerta**: Phase 4

مثال:
```
Project#New Capital / Phase#2 / Zone#2 / Building#6 / Unit#BF175-6-301
Project#La puerta / Phase#4 / Zone#9 / Building#La puerta / Unit#DO-244
```

**الهيكل ممتاز — يسمح بعرض P&L على مستوى المشروع، المرحلة، المنطقة، المبنى، الوحدة.**

### الـ Analytic Lines

| المقياس | القيمة |
|---------|--------|
| إجمالي السجلات | 1,979 |
| مربوطة بـ GL (move_line_id != False) | 1,977 |
| إجمالي `amount` | **0.00 EGP** |
| مجموعة بـ `account_id=Internal` | 2 سجلات، 0 EGP |
| مجموعة بـ `account_id=(none)` | 1,977 سجلات، 0 EGP |

### ⚠️ Finding حرج: الـ Analytic amounts = صفر

1,977 سطر تحليلي موجود ومربوط بقيود GL عبر `move_line_id`، لكن **`amount` = 0.00 EGP على كلهم**.

هذا finding مهم جداً. الاحتمالات:
1. **Migration نقل السجلات بدون الأرقام** — الأكثر احتمالاً.
2. الأرقام موجودة على `account.move.line` (GL side) لكن لم تُنسخ للـ analytic side.
3. حقل الـ amount مختلف في هذا الـ version (مش `amount` بل حقل آخر).

**أهمية الـ `move_line_id` link:** الـ 1,977 سطر مربوطة بـ GL entries — يعني من الناحية المبدئية ممكن نجيب الأرقام من GL side (account.move.line) بدل الـ analytic side لو الـ analytic amounts فاضية. لكن ده يحتاج تأكيد من خالد.

### ماذا يعني هذا لـ Module 4؟

لو الـ analytic amounts فاضية، Module 4 مش هيقدر يعرض "ربحية مشروع New Capital" دلوقتي. لكن الهيكل جاهز — المشكلة بيانات، مش تصميم. بمجرد ما La Verde يخلّصوا الـ migration والـ analytic amounts تتملأ، Module 4 هيشتغل.

---

## 7. السؤال الخامس — التكرار: إيه المغطّى بالفعل؟

**النتيجة: Receivables في account.move.line موجودة — مش نلمسها.**

| المقياس | القيمة |
|---------|--------|
| حسابات `asset_receivable` | 13 |
| سطور GL على حسابات الذمم المدينة (posted) | 7,961 |
| SUM(debit) | 5,814,192,787 EGP |
| SUM(credit) | 594,534,464 EGP |
| Net receivable (debit - credit) | **5,219,658,323 EGP** |

هذا الرقم (~5.2B EGP) يمثّل الذمم المدينة في الدفتر العام — وهو جانب آخر من نفس البيانات اللي في `rs.installment` (2.63B استحقاقات).

**القرار:** هذا الجزء **مغطّى بالكامل** بـ M2+M3. Module 4 لا يلمس `asset_receivable`.

**نطاق Module 4 الصريح:**
- ✓ Income / Revenue — **غير مغطّى**
- ✓ Expenses — **غير مغطّى**
- ✓ Cash position — **غير مغطّى**
- ✓ Per-project P&L via Analytic — **غير مغطّى**
- ✗ Receivables/installments → M2+M3

---

## 8. Open Questions (OQs)

| # | السؤال | الأولوية |
|---|--------|---------|
| OQ-ACC-1 | **المصروفات = 0** — هل لسه ما انتقلتش في الـ migration؟ هل فيه خطة لإدخالها؟ | **حرج — يمنع P&L** |
| OQ-ACC-2 | **Analytic amounts = 0** — هل ده migration issue؟ هل يمكن استرداد الأرقام من GL (account.move.line) عبر `move_line_id`؟ | **حرج — يمنع project P&L** |
| OQ-ACC-3 | **Cash one-sided** — هل ده opening balances فقط؟ متى بيبدأ تسجيل الصرف (مدفوعات للموردين/مصاريف)؟ | عالية |
| OQ-ACC-4 | **Date anomalies** — قيد بتاريخ 1015-05-31 وقيد بتاريخ 2026-10-22 (مستقبلي) — يُصحَّحوا؟ | متوسطة |
| OQ-ACC-5 | **Analytic account_id = (none)** — لماذا 1,977 سطر تحليلي بدون `account_id`؟ هل التحليلي linked بطريقة مختلفة في هذا الـ version؟ | عالية |
| OQ-ACC-6 | **asset_bank = 0 حسابات** — كل حسابات البنك مصنّفة `asset_cash`؟ ده مقصود؟ | منخفضة |
| OQ-ACC-7 | متى تخطّط La Verde تقفل أول فترة محاسبية؟ (أسابيع أم أشهر؟) | **حرج — يحدّد توقيت Module 4** |

---

## 9. التقييم النهائي

> **الحكم: Module 4 مؤجّل — blocked pending data migration.**

### الهيكل جاهز، البيانات الرقمية ناقصة

Module 4 (أداء مالي / ربحية بالمشروع) يستاهل هيكلياً — دليل الحسابات مبني، الهرم التحليلي ممتاز، القيود المحاسبية حقيقية. لكن **البيانات الرقمية في 3 مناطق حرجة فاضية** بسبب إن La Verde في نص migration من أريب والفترات مش مقفولة بعد.

### الأسباب الثلاثة للتأجيل

**السبب 1 — المصروفات = 0:**
85 حساب مصروف موجود في دليل الحسابات، لكن 0 قيد عليهم في `account.move.line`. لو بنيت Module 4 دلوقتي، الـ Board هيشوف "ربح 2.24M EGP بدون أي مصروفات" — وده رقم مضلّل تماماً، مش ناقص.

**السبب 2 — Analytic amounts = 0:**
1,977 سطر تحليلي موجود ومربوط بالـ GL عبر `move_line_id`، لكن `amount = 0.00` على كل سجل. يعني الـ per-project P&L (القيمة الأعلى في Module 4) مش هيشتغل.

**السبب 3 — الفترات مش مقفولة:**
`fiscalyear_lock_date` و `tax_lock_date` كلاهما NOT SET. أي رقم قابل للتغيير في أي وقت. عرض أرقام غير مقفولة للـ Board كـ"حقائق" يخلق مشاكل ثقة.

### شرط إعادة التقييم

Discovery تاني يتعمل بعد ما **كل الشروط الثلاثة** تتحقق:
1. مصروفات تتدخل في `account.move.line` (expense accounts لازم تبقى > 0 قيود).
2. `account.analytic.line.amount` يتملأ (أو يتأكّد إن الأرقام جايّة من GL linkage).
3. La Verde تقفل أول فترة محاسبية (أو على الأقل تقرّر إنهم جاهزين للعرض).

---

## 10. المرحلة التالية

**لا action مطلوبة دلوقتي.** المرجع: `docs/ACCOUNTING_DISCOVERY.md` (هذا الملف) + بند D-5 في `docs/MODULE_2_STAGE_TRACKER.md`.

لما La Verde تكمّل إدخال البيانات — discovery تاني باستخدام `scripts/discover_accounting_phase1.py` (نفس السكربت) يكفي للتحقق. لو الأسباب الثلاثة اتحلّت، يبدأ Module 4 planning من الصفر بناءً على الأرقام الجديدة.

---

## 11. ملخّص اكتشافات للـ Module 4 (لو قرّرنا نبني)

**KPIs المقترحة (subject to data availability):**

| KPI | الـ Model | المتطلب قبل البناء |
|-----|-----------|-------------------|
| إجمالي الإيرادات | `account.move.line` WHERE account_type IN income | جاهز (173 سطر) |
| إجمالي المصروفات | `account.move.line` WHERE account_type IN expense | **ينتظر migration** |
| صافي الربح | إيرادات - مصروفات | **ينتظر migration** |
| إجمالي الكاش | `account.move.line` WHERE account_type = asset_cash | جاهز (one-sided — يحتاج review) |
| P&L لكل مشروع | `account.analytic.line` groupby `account_id` | **ينتظر OQ-ACC-2** |
| أعلى مشاريع ربحية | Analytic groupby plan (New Capital / Cassette / La Puerta) | **ينتظر OQ-ACC-2** |

---

*Discovery complete 2026-05-25. لا كود تطبيق كُتب. لا تعديل على Odoo.*
*الخطوة التالية: خالد يراجع OQ-ACC-1 و OQ-ACC-2 و OQ-ACC-7، ويحدّد قرار التوقيت.*
