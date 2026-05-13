# Phase 5: Comprehensive Bug Hunt Report

**Generated**: 2026-05-13 11:32:18  
**Status**: Complete (2026-05-13 11:43:53)

## Summary

| Metric | Value |
|--------|-------|
| Tests run | 105 |
| Failures | 11 |
| Total cost | $0.044721 |

## Failures

---

### B-04-V1: count_by_stage — No numeric value in response (expected a count)

- **Test ID**: B-04-V1
- **Section**: B
- **Intent (expected)**: `count_by_stage`
- **Intent (classified)**: `count_by_stage`
- **Language**: ar
- **Question sent**: كم lead في مرحلة Contact in the Future؟
- **Failure reason**: No numeric value in response (expected a count)

**Full AI response:**
```
لم أجد مرحلة باسم 'unknown' بشكل دقيق. الأسماء الصحيحة للمراحل: New, No Answer, Follow up, Interested, Contact in the Future, Re-Distribution, Reservation, Down Payment Confirm & Contracted.
```

**Suggested follow-ups returned:**
- كم عدد العملاء في مرحلة Reservation؟
- كم lead متأخر في مرحلة Follow up؟

**Data snapshot from handler:**
```json
{
  "type": "stage_not_found",
  "requested_stage": "unknown"
}
```

**Root cause category**: TBD

---

### B-05-V1: count_overdue_by_stage — Clarification fallback detected (matched 'لم أفهم')

- **Test ID**: B-05-V1
- **Section**: B
- **Intent (expected)**: `count_overdue_by_stage`
- **Intent (classified)**: `count_overdue_by_stage`
- **Language**: ar
- **Question sent**: كم lead متأخر في مرحلة Contact in the Future؟
- **Failure reason**: Clarification fallback detected (matched 'لم أفهم')

**Full AI response:**
```
عذراً، لم أفهم سؤالك بشكل كافٍ لأجيب بدقة. هل يمكنك إعادة صياغته؟ على سبيل المثال:

- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- كم lead في مرحلة Follow up؟
- اقترح عليّ leads أتصل بيهم النهارده
```

**Suggested follow-ups returned:**
- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده

**Data snapshot from handler:**
```json
{
  "type": "clarification_needed",
  "message": "Stage name required"
}
```

**Root cause category**: TBD

---

### B-08-V1: lead_details_by_id — Clarification fallback detected (matched 'لا تتوفر')

- **Test ID**: B-08-V1
- **Section**: B
- **Intent (expected)**: `lead_details_by_id`
- **Intent (classified)**: `lead_details_by_id`
- **Language**: ar
- **Question sent**: عرضلي تفاصيل العميل رقم 683893
- **Failure reason**: Clarification fallback detected (matched 'لا تتوفر')

**Full AI response:**
```
لا تتوفر لديّ بيانات كافية لهذا السؤال تحديداً. جرّب أحد هذه:

- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده
```

**Suggested follow-ups returned:**
- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده

**Data snapshot from handler:**
```json
{
  "type": "error",
  "message": "Odoo query error: {'code': 200, 'message': 'Odoo Server Error', 'data': {'name': 'builtins.ValueError', 'debug': 'Traceback (most recent call last):\\n  File \"/home/odoo/src/odoo/odoo/http.py\", line 2167, in _transactioning\\n    return service_model.retrying(func, env=self.env)\\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 157, in retrying\\n    result = func()\\n             ^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/http.py\", line 2134, in _serve_ir_http\\n    response = self.dispatcher.dispatch(rule.endpoint, args)\\n               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/http.py\", line 2382, in dispatch\\n    result = self.request.registry[\\'ir.http\\']._dispatch(endpoint)\\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/addons/base/models/ir_http.py\", line 333, in _dispatch\\n    result = endpoint(**request.params)\\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/http.py\", line 754, in route_wrapper\\n    result = endpoint(self, *args, **params_ok)\\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/addons/base/controllers/rpc.py\", line 178, in jsonrpc\\n    return dispatch_rpc(service, method, args)\\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/http.py\", line 412, in dispatch_rpc\\n    return dispatch(method, params)\\n           ^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 59, in dispatch\\n    res = execute_kw(db, uid, *params[3:])\\n          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 82, in execute_kw\\n    return execute(db, uid, obj, method, *args, **kw or {})\\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 88, in execute\\n    res = execute_cr(cr, uid, obj, method, *args, **kw)\\n          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 73, in execute_cr\\n    result = retrying(partial(odoo.api.call_kw, recs, method, args, kw), env)\\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 157, in retrying\\n    result = func()\\n             ^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/api.py\", line 535, in call_kw\\n    result = getattr(recs, name)(*args, **kwargs)\\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/models.py\", line 6143, in search_read\\n    records = self.search_fetch(domain or [], fields, offset=offset, limit=limit, order=order)\\n              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/addons/crm/models/crm_lead.py\", line 826, in search_fetch\\n    return super().search_fetch(domain, field_names, offset, limit, order)\\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/models.py\", line 1784, in search_fetch\\n    fields_to_fetch = self._determine_fields_to_fetch(field_names)\\n                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/models.py\", line 4192, in _determine_fields_to_fetch\\n    raise ValueError(f\"Invalid field {field_name!r} on model {self._name!r}\")\\nValueError: Invalid field \\'planned_revenue\\' on model \\'crm.lead\\'\\n', 'message': \"Invalid field 'planned_revenue' on model 'crm.lead'\", 'arguments': [\"Invalid field 'planned_revenue' on model 'crm.lead'\"], 'context': {}}}"
}
```

**Root cause category**: lead_details_overdue_only

---

### B-08-V2: lead_details_by_id — Clarification fallback detected (matched 'try one of these')

- **Test ID**: B-08-V2
- **Section**: B
- **Intent (expected)**: `lead_details_by_id`
- **Intent (classified)**: `lead_details_by_id`
- **Language**: en
- **Question sent**: Show me details for lead ID 683893
- **Failure reason**: Clarification fallback detected (matched 'try one of these')

**Full AI response:**
```
I don't have enough specific data to answer that. Try one of these:

- Which 5 sales employees have the most overdue leads?
- Recommend 3 leads for me to contact today
```

**Suggested follow-ups returned:**
- Which 5 sales employees have the most overdue leads?
- Recommend 3 leads for me to contact today

**Data snapshot from handler:**
```json
{
  "type": "error",
  "message": "Odoo query error: {'code': 200, 'message': 'Odoo Server Error', 'data': {'name': 'builtins.ValueError', 'debug': 'Traceback (most recent call last):\\n  File \"/home/odoo/src/odoo/odoo/http.py\", line 2167, in _transactioning\\n    return service_model.retrying(func, env=self.env)\\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 157, in retrying\\n    result = func()\\n             ^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/http.py\", line 2134, in _serve_ir_http\\n    response = self.dispatcher.dispatch(rule.endpoint, args)\\n               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/http.py\", line 2382, in dispatch\\n    result = self.request.registry[\\'ir.http\\']._dispatch(endpoint)\\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/addons/base/models/ir_http.py\", line 333, in _dispatch\\n    result = endpoint(**request.params)\\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/http.py\", line 754, in route_wrapper\\n    result = endpoint(self, *args, **params_ok)\\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/addons/base/controllers/rpc.py\", line 178, in jsonrpc\\n    return dispatch_rpc(service, method, args)\\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/http.py\", line 412, in dispatch_rpc\\n    return dispatch(method, params)\\n           ^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 59, in dispatch\\n    res = execute_kw(db, uid, *params[3:])\\n          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 82, in execute_kw\\n    return execute(db, uid, obj, method, *args, **kw or {})\\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 88, in execute\\n    res = execute_cr(cr, uid, obj, method, *args, **kw)\\n          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 73, in execute_cr\\n    result = retrying(partial(odoo.api.call_kw, recs, method, args, kw), env)\\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/service/model.py\", line 157, in retrying\\n    result = func()\\n             ^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/api.py\", line 535, in call_kw\\n    result = getattr(recs, name)(*args, **kwargs)\\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/models.py\", line 6143, in search_read\\n    records = self.search_fetch(domain or [], fields, offset=offset, limit=limit, order=order)\\n              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/addons/crm/models/crm_lead.py\", line 826, in search_fetch\\n    return super().search_fetch(domain, field_names, offset, limit, order)\\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/models.py\", line 1784, in search_fetch\\n    fields_to_fetch = self._determine_fields_to_fetch(field_names)\\n                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\\n  File \"/home/odoo/src/odoo/odoo/models.py\", line 4192, in _determine_fields_to_fetch\\n    raise ValueError(f\"Invalid field {field_name!r} on model {self._name!r}\")\\nValueError: Invalid field \\'planned_revenue\\' on model \\'crm.lead\\'\\n', 'message': \"Invalid field 'planned_revenue' on model 'crm.lead'\", 'arguments': [\"Invalid field 'planned_revenue' on model 'crm.lead'\"], 'context': {}}}"
}
```

**Root cause category**: lead_details_overdue_only

---

### B-16-V1: recommendation_for_salesperson — Clarification fallback detected (matched 'لا تتوفر')

- **Test ID**: B-16-V1
- **Section**: B
- **Intent (expected)**: `recommendation_for_salesperson`
- **Intent (classified)**: `recommendation_for_salesperson`
- **Language**: ar
- **Question sent**: اقترح عملاء لـ Ahmed ElSayed يتصل بيهم
- **Failure reason**: Clarification fallback detected (matched 'لا تتوفر')

**Full AI response:**
```
لا تتوفر لديّ بيانات كافية لهذا السؤال تحديداً. جرّب أحد هذه:

- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده
```

**Suggested follow-ups returned:**
- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده

**Data snapshot from handler:**
```json
{
  "type": "recommendations",
  "salesperson_filter": "Ahmed ElSayed",
  "leads": []
}
```

**Root cause category**: recommendation_for_sp_filter_ignored

---

### C-02: count_by_stage — Clarification fallback detected (matched 'لم أفهم')

- **Test ID**: C-02
- **Section**: C
- **Intent (expected)**: `count_by_stage`
- **Intent (classified)**: `unknown`
- **Language**: ar
- **Question sent**: Show me التأخرات في stage Contact in the Future
- **Failure reason**: Clarification fallback detected (matched 'لم أفهم')

**Full AI response:**
```
عذراً، لم أفهم سؤالك بشكل كافٍ لأجيب بدقة. هل يمكنك إعادة صياغته؟ على سبيل المثال:

- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- كم lead في مرحلة Follow up؟
- اقترح عليّ leads أتصل بيهم النهارده
```

**Suggested follow-ups returned:**
- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده

**Data snapshot from handler:**
```json
{
  "type": "clarification_needed"
}
```

**Root cause category**: mixed_language_parsing

---

### D-1-D2-1-1: (follow-up-depth-2) — Depth-2 follow-up returned clarification (matched 'لم أفهم')

- **Test ID**: D-1-D2-1-1
- **Section**: D
- **Intent (expected)**: `(follow-up-depth-2)`
- **Intent (classified)**: `count_overdue_by_stage`
- **Language**: ar
- **Question sent**: عدد التأخيرات لكل مرحلة من مراحل المبيعات.
- **Failure reason**: Depth-2 follow-up returned clarification (matched 'لم أفهم')

**Full AI response:**
```
عذراً، لم أفهم سؤالك بشكل كافٍ لأجيب بدقة. هل يمكنك إعادة صياغته؟ على سبيل المثال:

- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- كم lead في مرحلة Follow up؟
- اقترح عليّ leads أتصل بيهم النهارده
```

**Suggested follow-ups returned:**
- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده

**Data snapshot from handler:**
```json
{
  "type": "clarification_needed",
  "message": "Stage name required"
}
```

**Root cause category**: followup_unanswerable_depth2

---

### D-4-D1-2: (follow-up-depth-1) — Depth-1 follow-up returned clarification (matched 'لا تتوفر')

- **Test ID**: D-4-D1-2
- **Section**: D
- **Intent (expected)**: `(follow-up-depth-1)`
- **Intent (classified)**: `lead_details_by_id`
- **Language**: ar
- **Question sent**: ما هي تفاصيل العميل Marwa's opportunity؟
- **Failure reason**: Depth-1 follow-up returned clarification (matched 'لا تتوفر')

**Full AI response:**
```
لا تتوفر لديّ بيانات كافية لهذا السؤال تحديداً. جرّب أحد هذه:

- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده
```

**Suggested follow-ups returned:**
- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده

**Data snapshot from handler:**
```json
{
  "type": "error",
  "message": "Lead ID required — include the numeric lead ID in your question"
}
```

**Root cause category**: followup_unanswerable_depth1

---

### E-STG-Contact_in_the_: count_by_stage — No numeric value in response (expected a count); No number in AI response for st

- **Test ID**: E-STG-Contact_in_the_
- **Section**: E
- **Intent (expected)**: `count_by_stage`
- **Intent (classified)**: `count_by_stage`
- **Language**: ar
- **Question sent**: كم lead في مرحلة Contact in the Future؟
- **Failure reason**: No numeric value in response (expected a count); No number in AI response for stage 'Contact in the Future' (Odoo count=9119)

**Full AI response:**
```
لم أجد مرحلة باسم 'unknown' بشكل دقيق. الأسماء الصحيحة للمراحل: New, No Answer, Follow up, Interested, Contact in the Future, Re-Distribution, Reservation, Down Payment Confirm & Contracted.
```

**Suggested follow-ups returned:**
- كم عدد العملاء في مرحلة Reservation؟
- كم lead متأخر في مرحلة Follow up؟

**Data snapshot from handler:**
```json
{
  "type": "stage_not_found",
  "requested_stage": "unknown"
}
```

**Root cause category**: data_accuracy_stage_count

---

### E-TM-Mirna_Medhat_Te: count_by_team — Clarification fallback detected (matched 'لم أفهم'); Count mismatch for 'Mirna M

- **Test ID**: E-TM-Mirna_Medhat_Te
- **Section**: E
- **Intent (expected)**: `count_by_team`
- **Intent (classified)**: `unknown`
- **Language**: ar
- **Question sent**: كام lead متأخر في فريق Mirna Medhat Team؟
- **Failure reason**: Clarification fallback detected (matched 'لم أفهم'); Count mismatch for 'Mirna Medhat Team': AI=5, Odoo=29

**Full AI response:**
```
عذراً، لم أفهم سؤالك بشكل كافٍ لأجيب بدقة. هل يمكنك إعادة صياغته؟ على سبيل المثال:

- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- كم lead في مرحلة Follow up؟
- اقترح عليّ leads أتصل بيهم النهارده
```

**Suggested follow-ups returned:**
- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده

**Data snapshot from handler:**
```json
{
  "type": "clarification_needed"
}
```

**Root cause category**: data_accuracy_team_overdue_count

---

### E-TM-Merna_Essam_Tea: count_by_team — Clarification fallback detected (matched 'لم أفهم'); Count mismatch for 'Merna E

- **Test ID**: E-TM-Merna_Essam_Tea
- **Section**: E
- **Intent (expected)**: `count_by_team`
- **Intent (classified)**: `unknown`
- **Language**: ar
- **Question sent**: كام lead متأخر في فريق Merna Essam Team؟
- **Failure reason**: Clarification fallback detected (matched 'لم أفهم'); Count mismatch for 'Merna Essam Team': AI=5, Odoo=1

**Full AI response:**
```
عذراً، لم أفهم سؤالك بشكل كافٍ لأجيب بدقة. هل يمكنك إعادة صياغته؟ على سبيل المثال:

- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- كم lead في مرحلة Follow up؟
- اقترح عليّ leads أتصل بيهم النهارده
```

**Suggested follow-ups returned:**
- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟
- اقترح عليّ 3 عملاء أتواصل معاهم النهارده

**Data snapshot from handler:**
```json
{
  "type": "clarification_needed"
}
```

**Root cause category**: data_accuracy_team_overdue_count

