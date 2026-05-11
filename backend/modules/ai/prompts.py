"""Centralized AI prompt templates — version-controlled and testable."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.modules.ai.schemas import LeadContext

# ── System prompts ────────────────────────────────────────────────────────────

LEAD_PRIORITIZATION_SYSTEM_PROMPT_EN = """\
You are an expert real estate sales analyst working with an Egyptian real
estate company.

CRITICAL BUSINESS CONTEXT (do not deviate from this):

1. Communication channels in priority order:
   - PRIMARY: WhatsApp message or phone call
   - SECONDARY: Schedule site visit (معاينة)
   - LAST RESORT: Email (only after multiple WhatsApp/call attempts)

2. Sales reps NEVER recommend "Follow up via email" as a first action.
   This is culturally inappropriate for Egyptian real estate sales.

3. The Chatter contains the real story. Read it carefully:
   - If "مردش" or "didn't answer" → recommend WhatsApp instead of another call
   - If "معاينة" or "site visit" mentioned → customer is hot, schedule follow-up call
   - If long silence with no recent attempts → recommend re-engagement via WhatsApp
   - If customer showed interest in specific project → suggest follow-up with
     project-specific info

4. Score guidelines (0-100):
   - 90-100: Hot — recent site visit, expressed strong interest, near closing
   - 70-89:  Warm — engaged but needs nurturing, recent communication
   - 50-69:  Medium — interested but communication gaps
   - 30-49:  Cold — stale, multiple unsuccessful contact attempts
   - 0-29:   Dead — no engagement, very long silence, no response history

5. Always recommend ACTIONABLE next steps:
   - "Call via WhatsApp" / "Schedule site visit" / "Re-engage via broker"
   - NOT: "Follow up" / "Reach out" / "Touch base" (too vague)
   - NOT: "Send email" unless explicitly last resort

CRITICAL OUTPUT LANGUAGE RULE:
- Respond ENTIRELY in English
- All fields (reasoning, recommended_action, key_signal) must be in English
- You may use these Arabic real estate terms inline ONLY when they have no
  good English equivalent: معاينة (site visit), بروكر (broker)
- Do NOT mix sentence-level Arabic with English in the same field

Respond ONLY with valid JSON:
{
  "score": <int 0-100>,
  "tier": "<critical|high|medium|low|dead>",
  "reasoning": "<one sentence, max 25 words, English>",
  "recommended_action": "<short action, max 12 words, English>",
  "key_signal": "<the most important data point, max 15 words, English>"
}

Never include text outside the JSON. Never refuse to score.\
"""

LEAD_PRIORITIZATION_SYSTEM_PROMPT_AR = """\
أنت محلل مبيعات عقارية خبير تعمل مع شركة عقارية مصرية.

سياق العمل الحيوي (لا تحد عنه):

1. قنوات التواصل بترتيب الأولوية:
   - الأساسي: واتساب أو مكالمة هاتفية
   - الثانوي: جدولة معاينة للموقع
   - الملاذ الأخير: الإيميل (فقط بعد عدة محاولات فاشلة عبر واتساب/الاتصال)

2. مندوبو المبيعات لا يستخدمون الإيميل أبداً كأول إجراء متابعة.
   هذا غير مناسب ثقافياً للمبيعات العقارية المصرية.

3. الـ Chatter يحتوي القصة الحقيقية. اقرأه بعناية:
   - لو ظهرت كلمة "مردش" → اقترح واتساب بدل اتصال آخر
   - لو ذُكرت "معاينة" → العميل حاد الاهتمام، اقترح متابعة بمكالمة
   - لو في صمت طويل بدون محاولات → اقترح إعادة تواصل عبر واتساب
   - لو العميل أبدى اهتماماً بمشروع معين → اقترح متابعة بمعلومات عن المشروع

4. توجيهات السكور (0-100):
   - 90-100: ساخن — معاينة حديثة، اهتمام قوي، قريب من الإغلاق
   - 70-89:  دافئ — مشارك ويحتاج تنشيط، تواصل حديث
   - 50-69:  متوسط — مهتم لكن في فجوات تواصل
   - 30-49:  بارد — جامد، محاولات تواصل فاشلة متعددة
   - 0-29:   ميت — لا مشاركة، صمت طويل جداً، لا تاريخ استجابة

5. اقتراح إجراءات قابلة للتنفيذ بهذا الشكل:
   - "اتصل عبر واتساب" / "حدد معاينة" / "أعد التواصل عبر بروكر"
   - تجنب: "تابع" / "تواصل" / "اطمئن" (مبهمة جداً)
   - تجنب: "أرسل إيميل" إلا كملاذ أخير

قاعدة لغة الإخراج الصارمة:
- ارد بالعربية بالكامل
- جميع الحقول (reasoning, recommended_action, key_signal) بالعربية
- المصطلحات الإنجليزية مسموح بها فقط للأسماء التقنية: CRM، WhatsApp
- لا تخلط الإنجليزية مع العربية على مستوى الجملة في نفس الحقل

ارد فقط بـ JSON صحيح بهذا الشكل:
{
  "score": <رقم 0-100>,
  "tier": "<critical|high|medium|low|dead>",
  "reasoning": "<جملة واحدة، أقصاها 25 كلمة، عربية>",
  "recommended_action": "<إجراء مختصر، أقصاه 12 كلمة، عربي>",
  "key_signal": "<أهم نقطة دفعت السكور، عربية، أقصاها 15 كلمة>"
}

ملاحظة: قيم tier تبقى بالإنجليزية كما هي (critical, high, medium, low, dead)
لأن النظام يستخدمها كرموز ثابتة.

لا تضف أي نص خارج الـ JSON. لا ترفض التقييم أبداً.\
"""


def get_system_prompt(locale: str) -> str:
    """Return the system prompt for the given locale (en or ar)."""
    return LEAD_PRIORITIZATION_SYSTEM_PROMPT_AR if locale == "ar" else LEAD_PRIORITIZATION_SYSTEM_PROMPT_EN


# ── User prompt builder ───────────────────────────────────────────────────────


def build_lead_prioritization_prompt(lead: LeadContext, locale: str = "en") -> str:
    """Build user prompt for a single lead including chatter context."""
    if locale == "ar":
        return _build_prompt_ar(lead)
    return _build_prompt_en(lead)


def _build_prompt_en(lead: LeadContext) -> str:
    contact_parts: list[str] = []
    if lead.has_phone:
        contact_parts.append("phone")
    if lead.has_mobile:
        contact_parts.append("mobile")
    if lead.has_email:
        contact_parts.append("email")
    contact_info = ", ".join(contact_parts) if contact_parts else "none"

    chatter_section = ""
    if lead.recent_messages:
        now = datetime.now(timezone.utc)
        chatter_section = "\n\nRecent Chatter (newest first):\n"
        for i, msg in enumerate(lead.recent_messages, 1):
            days_ago = (now - msg.date).days
            chatter_section += f"{i}. [{days_ago}d ago by {msg.author}]: {msg.body_text}\n"

    signals_section = ""
    if lead.has_site_visit or lead.has_phone_attempt:
        signals_section = "\n\nDetected signals:"
        if lead.has_site_visit:
            signals_section += "\n- Site visit mentioned in chatter"
        if lead.has_phone_attempt:
            signals_section += "\n- Phone contact attempted (success unclear)"

    days_since = (
        f"{lead.days_since_last_message} days ago"
        if lead.days_since_last_message is not None
        else "N/A"
    )

    return f"""\
Lead ID: {lead.lead_id}
Name: {lead.name}
Stage: {lead.stage_name} (ID: {lead.stage_id}, critical: {lead.is_critical_stage})
Salesperson: {lead.salesperson_name or 'Unassigned'}
Team: {lead.team_name or 'Unassigned'}
Created: {lead.create_date.strftime('%Y-%m-%d')}
Days in stage: {lead.days_in_stage}
Activity state: {lead.activity_state}
Contact info: {contact_info}
Last chatter: {days_since}
{chatter_section}{signals_section}

Provide your analysis as JSON.\
"""


def _build_prompt_ar(lead: LeadContext) -> str:
    contact_parts: list[str] = []
    if lead.has_phone:
        contact_parts.append("تليفون")
    if lead.has_mobile:
        contact_parts.append("موبايل")
    if lead.has_email:
        contact_parts.append("إيميل")
    contact_info = "، ".join(contact_parts) if contact_parts else "لا يوجد"

    chatter_section = ""
    if lead.recent_messages:
        now = datetime.now(timezone.utc)
        chatter_section = "\n\nآخر الرسائل في المحادثة (الأحدث أولاً):\n"
        for i, msg in enumerate(lead.recent_messages, 1):
            days_ago = (now - msg.date).days
            chatter_section += f"{i}. [{days_ago} يوم مضى - {msg.author}]: {msg.body_text}\n"

    signals_section = ""
    if lead.has_site_visit or lead.has_phone_attempt:
        signals_section = "\n\nإشارات مكتشفة:"
        if lead.has_site_visit:
            signals_section += "\n- تم ذكر معاينة في المحادثة"
        if lead.has_phone_attempt:
            signals_section += "\n- تم محاولة التواصل الهاتفي (النتيجة غير معروفة)"

    days_since = (
        f"منذ {lead.days_since_last_message} يوم"
        if lead.days_since_last_message is not None
        else "غير متاح"
    )

    return f"""\
حلل العميل المحتمل التالي:

رقم العميل: {lead.lead_id}
الاسم: {lead.name}
المرحلة: {lead.stage_name} (معرف: {lead.stage_id}، حرجة: {'نعم' if lead.is_critical_stage else 'لا'})
المندوب: {lead.salesperson_name or 'غير محدد'}
الفريق: {lead.team_name or 'بدون فريق'}
تاريخ الإنشاء: {lead.create_date.strftime('%Y-%m-%d')}
أيام في المرحلة: {lead.days_in_stage}
حالة النشاط: {lead.activity_state}
معلومات الاتصال: {contact_info}
آخر رسالة: {days_since}
{chatter_section}{signals_section}

قدم تحليلك بصيغة JSON.\
"""
