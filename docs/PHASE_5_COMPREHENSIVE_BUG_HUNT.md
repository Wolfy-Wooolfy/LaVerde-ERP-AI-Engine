# Phase 5: Comprehensive Bug Hunt Report

**Generated**: 2026-05-12 15:27:11  
**Status**: Complete (2026-05-12 15:41:06)

## Summary

| Metric | Value |
|--------|-------|
| Tests run | 107 |
| Failures | 15 |
| Total cost | $0.038724 |

## Failures

---

### B-08-V1: lead_details_by_id — Clarification fallback detected (matched 'لا تتوفر')

- **Test ID**: B-08-V1
- **Section**: B
- **Intent (expected)**: `lead_details_by_id`
- **Intent (classified)**: `lead_details_by_id`
- **Language**: ar
- **Question sent**: عرضلي تفاصيل العميل رقم 196854
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
  "message": "Lead ID required and AI must be enabled"
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
- **Question sent**: Show me details for lead ID 196854
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
  "message": "Lead ID required and AI must be enabled"
}
```

**Root cause category**: lead_details_overdue_only

---

### B-09-V1: leads_with_site_visit_signal — Clarification fallback detected (matched 'لا تتوفر')

- **Test ID**: B-09-V1
- **Section**: B
- **Intent (expected)**: `leads_with_site_visit_signal`
- **Intent (classified)**: `leads_with_site_visit_signal`
- **Language**: ar
- **Question sent**: عرضلي العملاء اللي طلبوا معاينة
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
  "type": "lead_list",
  "signal": "site_visit",
  "rows": [],
  "total": 0
}
```

**Root cause category**: site_visit_signal_empty_overdue_leads

---

### B-09-V2: leads_with_site_visit_signal — Clarification fallback detected (matched 'لا تتوفر')

- **Test ID**: B-09-V2
- **Section**: B
- **Intent (expected)**: `leads_with_site_visit_signal`
- **Intent (classified)**: `leads_with_site_visit_signal`
- **Language**: ar
- **Question sent**: مين العملاء اللي عندهم اهتمام بمعاينة الموقع؟
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
  "type": "lead_list",
  "signal": "site_visit",
  "rows": [],
  "total": 0
}
```

**Root cause category**: site_visit_signal_empty_overdue_leads

---

### B-09-V3: leads_with_site_visit_signal — Clarification fallback detected (matched 'try one of these')

- **Test ID**: B-09-V3
- **Section**: B
- **Intent (expected)**: `leads_with_site_visit_signal`
- **Intent (classified)**: `leads_with_site_visit_signal`
- **Language**: en
- **Question sent**: Show me clients who want to visit the property
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
  "type": "lead_list",
  "signal": "site_visit",
  "rows": [],
  "total": 0
}
```

**Root cause category**: site_visit_signal_empty_overdue_leads

---

### B-10-V3: leads_with_phone_attempt_signal — Clarification fallback detected (matched "I'm not sure I understood")

- **Test ID**: B-10-V3
- **Section**: B
- **Intent (expected)**: `leads_with_phone_attempt_signal`
- **Intent (classified)**: `unknown`
- **Language**: en
- **Question sent**: leads with failed phone contact attempts
- **Failure reason**: Clarification fallback detected (matched "I'm not sure I understood")

**Full AI response:**
```
I'm not sure I understood that well enough to answer accurately. Could you rephrase it? For example:

- 'Show me the top 5 sales employees with the most overdue leads'
- 'How many leads are in Follow up stage?'
- 'Recommend leads for me to call today'
```

**Suggested follow-ups returned:**
- Which 5 sales employees have the most overdue leads?
- Recommend 3 leads for me to contact today

**Data snapshot from handler:**
```json
{
  "type": "clarification_needed"
}
```

**Root cause category**: phone_signal_empty_overdue_leads

---

### D-1-D2-1-1: (follow-up-depth-2) — Depth-2 follow-up returned clarification (matched 'لم أفهم')

- **Test ID**: D-1-D2-1-1
- **Section**: D
- **Intent (expected)**: `(follow-up-depth-2)`
- **Intent (classified)**: `count_overdue_by_stage`
- **Language**: ar
- **Question sent**: كم عدد التأخيرات لكل مرحلة من مراحل المبيعات؟
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

### D-1-D1-3: (follow-up-depth-1) — Depth-1 follow-up returned clarification (matched 'لا تتوفر')

- **Test ID**: D-1-D1-3
- **Section**: D
- **Intent (expected)**: `(follow-up-depth-1)`
- **Intent (classified)**: `lead_details_by_id`
- **Language**: ar
- **Question sent**: ما هي تفاصيل العملاء المتأخرين مع أحمد عادل؟
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
  "message": "Lead ID required and AI must be enabled"
}
```

**Root cause category**: followup_unanswerable_depth1

---

### D-4-D1-1: (follow-up-depth-1) — Depth-1 follow-up returned clarification (matched 'لا تتوفر')

- **Test ID**: D-4-D1-1
- **Section**: D
- **Intent (expected)**: `(follow-up-depth-1)`
- **Intent (classified)**: `lead_details_by_id`
- **Language**: ar
- **Question sent**: ما هي تفاصيل العميل 707758؟
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
  "message": "Lead ID required and AI must be enabled"
}
```

**Root cause category**: followup_unanswerable_depth1

---

### E-STG-Contact_in_the_: count_by_stage — Count mismatch for 'Contact in the Future': AI=9050, Odoo=9051

- **Test ID**: E-STG-Contact_in_the_
- **Section**: E
- **Intent (expected)**: `count_by_stage`
- **Intent (classified)**: `count_by_stage`
- **Language**: ar
- **Question sent**: كم lead في مرحلة Contact in the Future؟
- **Failure reason**: Count mismatch for 'Contact in the Future': AI=9050, Odoo=9051

**Full AI response:**
```
**9050 leads**  
يوجد في مرحلة "Contact in the Future" عدد كبير من 9050 lead. هذه المرحلة تعكس اهتمام العملاء المحتملين الذين قد يحتاجون إلى تواصل لاحق، مما يشير إلى فرصة كبيرة لموظفي المبيعات للتفاعل معهم في المستقبل.
```

**Suggested follow-ups returned:**
- كم عدد leads في مرحلة "New"؟
- كم عدد leads المتأخرة في مرحلة "Contact in the Future"؟
- كم عدد leads التي تم تعيينها لكل موظف مبيعات؟

**Data snapshot from handler:**
```json
{
  "type": "stage_count",
  "stage_name": "Contact in the Future",
  "count": 9050,
  "overdue_only": false,
  "matched_count": 1
}
```

**Root cause category**: data_accuracy_stage_count

---

### E-STG-Lost: count_by_stage — Count mismatch for 'Lost': AI=5387, Odoo=5385

- **Test ID**: E-STG-Lost
- **Section**: E
- **Intent (expected)**: `count_by_stage`
- **Intent (classified)**: `count_by_stage`
- **Language**: ar
- **Question sent**: كم lead في مرحلة Lost؟
- **Failure reason**: Count mismatch for 'Lost': AI=5387, Odoo=5385

**Full AI response:**
```
**5387 leads** في مرحلة Lost. هذه المرحلة تشير إلى أن هذه الفرص لم تُحقق النجاح المطلوب، مما يعني أنه يجب علينا تحليل الأسباب والعمل على تحسين استراتيجياتنا في المستقبل.
```

**Suggested follow-ups returned:**
- كم عدد الـ leads في مرحلة "New"؟
- كم عدد الـ leads في مرحلة "Follow up"؟

**Data snapshot from handler:**
```json
{
  "type": "stage_count",
  "stage_name": "Lost",
  "count": 5387,
  "overdue_only": false,
  "matched_count": 1
}
```

**Root cause category**: data_accuracy_stage_count

---

### E-STG-Follow_up: count_by_stage — Count mismatch for 'Follow up': AI=4757, Odoo=4753

- **Test ID**: E-STG-Follow_up
- **Section**: E
- **Intent (expected)**: `count_by_stage`
- **Intent (classified)**: `count_by_stage`
- **Language**: ar
- **Question sent**: كم lead في مرحلة Follow up؟
- **Failure reason**: Count mismatch for 'Follow up': AI=4757, Odoo=4753

**Full AI response:**
```
**4757 lead** في مرحلة "Follow up". هذه المرحلة تحتوي على عدد كبير من العملاء المحتملين الذين يحتاجون إلى متابعة من موظفي المبيعات لضمان تحويلهم إلى عملاء فعليين.
```

**Suggested follow-ups returned:**
- كم عدد الlead في مرحلة "Interested"؟
- كم عدد الlead المتأخرة في مرحلة "Follow up"؟
- ما هي تفاصيل الlead التي تحمل رقم معين؟

**Data snapshot from handler:**
```json
{
  "type": "stage_count",
  "stage_name": "Follow up",
  "count": 4757,
  "overdue_only": false,
  "matched_count": 1
}
```

**Root cause category**: data_accuracy_stage_count

---

### E-STG-New_X: count_by_stage — Clarification fallback detected (matched 'لم أفهم'); Count mismatch for 'New X':

- **Test ID**: E-STG-New_X
- **Section**: E
- **Intent (expected)**: `count_by_stage`
- **Intent (classified)**: `unknown`
- **Language**: ar
- **Question sent**: كم lead في مرحلة New X؟
- **Failure reason**: Clarification fallback detected (matched 'لم أفهم'); Count mismatch for 'New X': AI=5, Odoo=2921

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

**Root cause category**: data_accuracy_stage_count

---

### E-TM-Mirna_Medhat_Te: count_by_team — Clarification fallback detected (matched 'لم أفهم'); Count mismatch for 'Mirna M

- **Test ID**: E-TM-Mirna_Medhat_Te
- **Section**: E
- **Intent (expected)**: `count_by_team`
- **Intent (classified)**: `unknown`
- **Language**: ar
- **Question sent**: كام lead متأخر في فريق Mirna Medhat Team؟
- **Failure reason**: Clarification fallback detected (matched 'لم أفهم'); Count mismatch for 'Mirna Medhat Team': AI=5, Odoo=69

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

### E-TM-Mohamed_Osama_T: count_by_team — Clarification fallback detected (matched 'لم أفهم'); Count mismatch for 'Mohamed

- **Test ID**: E-TM-Mohamed_Osama_T
- **Section**: E
- **Intent (expected)**: `count_by_team`
- **Intent (classified)**: `unknown`
- **Language**: ar
- **Question sent**: كام lead متأخر في فريق Mohamed Osama Team؟
- **Failure reason**: Clarification fallback detected (matched 'لم أفهم'); Count mismatch for 'Mohamed Osama Team': AI=5, Odoo=1

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

## Phase 4: Root Cause Diagnosis

### True system bugs (require code fixes)

#### BUG-1: lead_details_by_id permanently broken

**Tests failed**: B-08-V1, B-08-V2, D-1-D1-3, D-4-D1-1

**Root cause (primary)**: The intent parser system prompt filter schema lists only , , , , . There is NO  field. So when the parser classifies a question as , the lead ID number is never written into . Handler checks  -> always  -> returns  ->  -> clarification fallback.

**Root cause (secondary)**: Even if  were passed, the handler only searches overdue leads via . Non-overdue leads are always .

**User impact**: CRITICAL. The AI actively recommends specific lead IDs and generates follow-ups asking for their details (confirmed in D-SEED4 which produced "details of lead 707758"). Every such follow-up silently returns "not enough data".

**Proposed fix**:
1. Add  field to the intent parser filter schema in  in 
2. Rewrite  in  to use direct Odoo  instead of searching through prioritizer overdue list

---

#### BUG-2: leads_with_site_visit_signal always returns empty

**Tests failed**: B-09-V1, B-09-V2, B-09-V3 (3/3 — 100% failure rate)

**Root cause**: Handler calls  which fetches only the top 50 overdue leads. Then filters for  (set by chatter keyword matching). None of the 388 currently-overdue leads have site-visit keywords ("معاينة", "زيارة", etc.) in their chatter. Result:  ->  returns True -> "لا تتوفر" fallback.

**User impact**: HIGH. This feature is listed in the dashboard suggested questions ("عرضلي العملاء اللي طلبوا معاينة"). It has never returned data in production. This is the exact Bug A reported before this session.

**Proposed fix**: Replace the overdue-only prioritizer search with a direct Odoo chatter search: query  for messages containing site-visit keywords where . Return the associated leads with their basic details.

---

#### BUG-3: "New X" stage (2,921 leads = 9.4% of pipeline) invisible to intent parser

**Tests failed**: E-STG-New_X (intent classified as )

**Root cause**: "New X" is not mentioned anywhere in . The parser sees an unfamiliar stage name and falls back to . The stage holds 2,921 leads (the data quality category — needs classification).

**User impact**: HIGH. Khaled cannot query his data-quality backlog by name.

**Proposed fix**: Add "New X" to the stage examples in . No normalisation needed — "New X" is the exact Odoo name.

---

#### BUG-4: Re-Distribution has no Arabic alias — 63% of overdue leads unreachable in Arabic

**Tests**: B-RD-03 passed gracefully (no crash, returned stage_not_found message) but proved the Arabic phrasing returns no data.

**Root cause**:  in  has no entry for "إعادة التوزيع". A user asking in Arabic gets . Re-Distribution holds 2,437 leads and 243 of 386 total overdue leads (63%). This is the single most impactful stage for daily follow-up management.

**User impact**: HIGH. The most critical overdue stage is unreachable via natural Arabic language.

**Proposed fix**: Add to :
- "إعادة التوزيع" -> "Re-Distribution"
- "اعادة التوزيع" -> "Re-Distribution" (without diacritic)
- "توزيع" -> "Re-Distribution" (short form)

Also add to system prompt stage mapping section.

---

#### BUG-5: STAGE_AR_TO_EN maps to 3 stage names that do not exist in live Odoo

**Tests**: C-16 (التفاوض -> Negotiation) passed gracefully — handled as stage_not_found, not crash.

**Root cause**: Three mappings point to non-existent stages:
- "التفاوض" / "تفاوض" -> "Negotiation" (no such stage in Odoo)
- "فاز" / "مغلق" -> "Won" (no such stage in Odoo)
- "معاينة" in stage context -> "Site Visit" (no such stage; معاينة is a chatter signal, not a stage)

Real stages that have no Arabic alias but should: "No Answer", "Contact in the Future", "Unqualified".

**User impact**: MEDIUM. Questions about these concepts always return stage_not_found even when Khaled is asking something legitimate.

**Proposed fix**: Remove the three broken mappings. Add aliases for real stages: "لا يوجد رد" -> "No Answer", "غير مؤهل" -> "Unqualified".

---

#### BUG-6: English "phone contact attempt" phrasing not recognised by intent parser

**Tests failed**: B-10-V3 ("leads with failed phone contact attempts" -> )

**Root cause**: System prompt has only Arabic keyword examples for . Formal English phrasing not in parser examples.

**User impact**: LOW. Only English edge-case phrasing affected. Arabic phrasing works (B-10-V1, V2 passed).

**Proposed fix**: Add one English example to the system prompt for this intent.

---

### False positives (NOT system bugs)

**FP-1 — Section E stage count race condition** (3 failures: E-STG-Contact_in_the_, E-STG-Lost, E-STG-Follow_up)

The AI correctly reported what Odoo returned at query time (confirmed:  matches the AI response in every case). The discrepancy is because Section A ground truth was fetched ~14 minutes before Section E ran. Leads were created/moved in the live CRM during the test. The system is working correctly. Test fix: re-fetch ground truth immediately before comparison.

**FP-2 — Section E team phrasing gap** (2 failures: E-TM-Mirna_Medhat_Te, E-TM-Mohamed_Osama_T)

"كام lead متأخر في فريق X؟" mixing متأخر + فريق confused the parser into . Test phrasing issue. The correct phrasing would be "عرضلي التأخرات في فريق X" which the parser handles correctly.

---

## Fix order by user impact

| # | Bug | Files | Lines | Impact |
|---|-----|-------|-------|--------|
| 1 | BUG-1: lead_details_by_id | ,  | ~3 lines each | CRITICAL |
| 2 | BUG-2: site_visit_signal |  | ~30 lines | HIGH |
| 3 | BUG-4: Re-Distribution alias | ,  | ~5 lines each | HIGH |
| 4 | BUG-3: New X stage |  | ~2 lines | HIGH |
| 5 | BUG-5: Non-existent stage aliases | ,  | ~5 lines each | MEDIUM |
| 6 | BUG-6: English phone signal |  | ~1 line | LOW |
| 7 | FP-1: Section E race condition |  | ~10 lines | Test only |
