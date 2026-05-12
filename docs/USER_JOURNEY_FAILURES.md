# User Journey Failures

**Generated**: 2026-05-12 17:30:10  
**Note**: Site-visit chatter probe confirmed 0 messages in Odoo — site visit intent returns empty by design (product gap).  

## Summary

| Metric | Value |
|--------|-------|
| Steps run | 42 |
| Failures | 6 |
| Passes | 36 |
| Total cost | $0.0162 |

## Failures

---

### J1-Q2 — Site Visit Investigation

- **Step**: J1-Q2 (Q2: details of first lead (context-dependent))
- **Question**: اعرض تفاصيل أول عميل منهم
- **Intent classified**: `lead_details_by_id`
- **Data type**: `error`
- **Failure reason**: clarification response (phrase: 'لا تتوفر لديّ بيانات')
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

### J1-Q3 — Site Visit Investigation

- **Step**: J1-Q3 (Q3: who is the responsible salesperson (context-dependent))
- **Question**: مين الموظف المسؤول عنه؟
- **Intent classified**: `unknown`
- **Data type**: `clarification_needed`
- **Failure reason**: clarification response (phrase: 'لم أفهم سؤالك')
- **Diagnosis**: No dedicated intent for 'who is the salesperson of a specific lead'. May map to unknown or free_form_analysis.
- **Proposed fix**: If lead_details_by_id is working, salesperson is in the data. AI should answer from context without a new CRM query.

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

### J4-Q2 — Recommendation Flow

- **Step**: J4-Q2 (Q2: who are the salespeople of those leads (no intent + no data))
- **Question**: مين موظفي المبيعات المسؤولين عن العملاء دول؟
- **Intent classified**: `unknown`
- **Data type**: `clarification_needed`
- **Failure reason**: clarification response (phrase: 'لم أفهم سؤالك')
- **Diagnosis**: DOUBLE BUG: (1) No intent for 'salespeople of previously-shown leads'; (2) recommendation handler output does NOT include salesperson_name field — only lead_id, score, tier, reasoning, recommended_action.
- **Proposed fix**: Add salesperson_name to recommendation handler output. Add free_form_analysis fallback that can answer contextual questions from prior response.

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

### J4-Q3 — Recommendation Flow

- **Step**: J4-Q3 (Q3: details of first lead (context-dependent ID))
- **Question**: أعطيني تفاصيل أكتر عن أول واحد
- **Intent classified**: `unknown`
- **Data type**: `clarification_needed`
- **Failure reason**: clarification response (phrase: 'لم أفهم سؤالك')
- **Diagnosis**: lead_details_by_id — if Q1 response mentioned a lead ID (e.g., 707758), parser may extract it from context. Depends on session context quality.
- **Proposed fix**: Recommendation response should explicitly surface lead IDs so context-dependent follow-ups can extract them.

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

### J5-Q3 — Data Quality Investigation

- **Step**: J5-Q3 (Q3: what type of problem)
- **Question**: إيه نوع المشكلة بالضبط؟
- **Intent classified**: `unknown`
- **Data type**: `clarification_needed`
- **Failure reason**: clarification response (phrase: 'لم أفهم سؤالك')
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

### J6-Q3 — Pure Conversational

- **Step**: J6-Q3 (Q3: show me an example question)
- **Question**: اعرضلي مثال على سؤال أقدر أسأله
- **Intent classified**: `help_request`
- **Data type**: `conversational`
- **Failure reason**: response too short (45 non-whitespace chars)
- **Diagnosis**: help_request — should suggest a concrete CRM question
- **Proposed fix**: N/A

**AI Response** (first 400 chars):

```
مرحباً! يمكنك طرح أسئلة حول أداء المبيعات أو العملاء.
```

**Data snapshot**:

```json
{
  "type": "conversational",
  "subtype": "help_request"
}
```

