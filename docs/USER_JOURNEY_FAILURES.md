# User Journey Failures

**Generated**: 2026-05-13 11:25:44  
**Note**: Site-visit chatter probe confirmed 0 messages in Odoo — site visit intent returns empty by design (product gap).  

## Summary

| Metric | Value |
|--------|-------|
| Steps run | 44 |
| Failures | 4 |
| Passes | 40 |
| Total cost | $0.0180 |

## Failures

---

### J1-Q2 — Site Visit Investigation

- **Step**: J1-Q2 (Q2: details of first lead (context-dependent))
- **Question**: اعرض تفاصيل أول عميل منهم
- **Intent classified**: `lead_details_by_id`
- **Data type**: `error`
- **Failure reason**: clarification response (phrase: 'لا تتوفر')
- **Diagnosis**: lead_details_by_id — question has no numeric ID; parser can only extract ID from session context (previous response). If Q1 was empty, there's nothing to reference.
- **Proposed fix**: AI response builder should include clickable lead IDs; intent parser should extract ID from session context

**AI Response** (first 400 chars):

```
لا تتوفر لديّ بيانات كافية لهذا السؤال تحديداً. جرّب أحد هذه:

- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده
```

**Data snapshot**:

```json
{
  "type": "error",
  "message": "Odoo query error: {'code': 200, 'message': 'Odoo Server Error', 'data': {'name': 'builtins.ValueError', 'debug': 'Traceback (most recent call last):\\n  File \"/home/odoo/src/odoo/odoo/http.py\", line 2167, in _transactioning\\n    return service_model.retrying(func, env=self.env)\\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 157, in retrying\\n    result = func()\\n             ^^^^^^\\n  F
```

---

### J4-Q3 — Recommendation Flow

- **Step**: J4-Q3 (Q3: details of first lead (context-dependent ID))
- **Question**: أعطيني تفاصيل أكتر عن أول واحد
- **Intent classified**: `lead_details_by_id`
- **Data type**: `error`
- **Failure reason**: clarification response (phrase: 'لا تتوفر')
- **Diagnosis**: lead_details_by_id — if Q1 response mentioned a lead ID (e.g., 707758), parser may extract it from context. Depends on session context quality.
- **Proposed fix**: Recommendation response should explicitly surface lead IDs so context-dependent follow-ups can extract them.

**AI Response** (first 400 chars):

```
لا تتوفر لديّ بيانات كافية لهذا السؤال تحديداً. جرّب أحد هذه:

- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده
```

**Data snapshot**:

```json
{
  "type": "error",
  "message": "Odoo query error: {'code': 200, 'message': 'Odoo Server Error', 'data': {'name': 'builtins.ValueError', 'debug': 'Traceback (most recent call last):\\n  File \"/home/odoo/src/odoo/odoo/http.py\", line 2167, in _transactioning\\n    return service_model.retrying(func, env=self.env)\\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 157, in retrying\\n    result = func()\\n             ^^^^^^\\n  F
```

---

### J5-Q3 — Data Quality Investigation

- **Step**: J5-Q3 (Q3: what type of problem)
- **Question**: إيه نوع المشكلة بالضبط؟
- **Intent classified**: `unknown`
- **Data type**: `clarification_needed`
- **Failure reason**: clarification response (phrase: 'لم أفهم')
- **Diagnosis**: data_quality_summary or free_form_analysis — should describe missing phone/email/stage categories
- **Proposed fix**: N/A

**AI Response** (first 400 chars):

```
عذراً، لم أفهم سؤالك بشكل كافٍ لأجيب بدقة. هل يمكنك إعادة صياغته؟ على سبيل المثال:

- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- كم lead في مرحلة Follow up؟
- اقترح عليّ leads أتصل بيهم النهارده
```

**Data snapshot**:

```json
{
  "type": "clarification_needed"
}
```

---

### J7-Q3 — Mixed Language

- **Step**: J7-Q3 (Q3: details of top salesperson in Re-Distribution (no salesperson-in-stage intent))
- **Question**: Show me details of the top salesperson there
- **Intent classified**: `unknown`
- **Data type**: `clarification_needed`
- **Failure reason**: clarification response (phrase: 'not sure i understood')
- **Diagnosis**: No intent for 'top salesperson in [previously mentioned stage]'. May map to list_overdue_by_salesperson (ignoring stage) or unknown.
- **Proposed fix**: Add stage filter to list_overdue_by_salesperson. Or use free_form_analysis to answer from context.

**AI Response** (first 400 chars):

```
I'm not sure I understood that well enough to answer accurately. Could you rephrase it? For example:

- 'Show me the top 5 sales employees with the most overdue leads'
- 'How many leads are in Follow up stage?'
- 'Recommend leads for me to call today'
```

**Data snapshot**:

```json
{
  "type": "clarification_needed"
}
```

