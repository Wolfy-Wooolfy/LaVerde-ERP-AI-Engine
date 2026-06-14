"""
Marketing Attribution — domain configuration & invariants (LOCKED DECISIONS).

This module holds ONLY config (the two gates) and pure invariants (stage->group
mapping). The campaign->buyer MAP values (which buyer, what concentration) are
DERIVED at runtime from live data — never hardcoded here. See §3.2 of the
mission and docs/MARKETING_ATTRIBUTION_DECISIONS.md.

Arabic terminology rule: always "موظف مبيعات" / "موظفي مبيعات" — never "مندوب".
"""

# ── The two gates (LOCKED — §3.4). KEYED BY CAMPAIGN NAME, not id. ────────────
#
# Keying decision (§6b): the gates store utm.campaign *names* (human-stable,
# shown in the Odoo UI and every discovery doc), resolved to campaign_id(s) at
# runtime against the live utm.campaign table. Integer ids are internal and
# would shift on a re-import; names are what Khaled edits. Names are NOT assumed
# unique — each configured name resolves to the SET of matching ids (A3).

# Confirmed media-buyer channels. A campaign attributes IFF it qualifies
# (concentration >= 0.90), is in this set, and is NOT in the denylist (§3.3).
# (Yomna IS a real media buyer despite the "Outsource" label — do NOT exclude.)
CONFIRMED_BUYER_CAMPAIGNS: frozenset[str] = frozenset(
    {
        "FB-AY",        # -> Ahmed Aymen   (documented dominant buyer; see below)
        "FB-AM",        # -> Abdallah Maher
        "Outsource-Y",  # -> Yomna Musaad
        "FB-LA",        # -> Ali shaban
    }
)

# Channel owners, NOT media buyers. Excluded even at 100% concentration (§3.4).
# EXACT campaign identity — never a substring/pattern match. Do NOT exclude a
# campaign merely because its name contains "Outsource", "Daima", "BV" or
# "Website" — only these specific campaigns.
# NOTE: these are the EXACT live utm.campaign names — spaces around the hyphen
# ("BV - Daima" id 1802, "Website - Daima" id 1803), corrected 2026-06-14 after
# the live run surfaced a whitespace mismatch against the originally-locked
# "BV-Daima"/"Website-Daima". Both resolve to dominant buyer "Mahmoud Mohsen"
# at 100% (channel owner) — confirming the §3.4 intent.
DENYLIST_CAMPAIGNS: frozenset[str] = frozenset(
    {
        "BV - Daima",
        "Website - Daima",
    }
)

# Documented dominant buyer per confirmed campaign (§3.4). REFERENCE / SANITY
# ONLY — this dict is NOT used by the attribution logic (the dominant buyer is
# always derived from live data). It exists so the live-verification script can
# assert the DERIVED dominant buyer matches the documented one (amendment A5).
DOCUMENTED_DOMINANT_BUYER: dict[str, str] = {
    "FB-AY": "Ahmed Aymen",
    "FB-AM": "Abdallah Maher",
    "Outsource-Y": "Yomna Musaad",
    "FB-LA": "Ali shaban",
}

# ── Attribution model fields (LOCKED — §3.1 / §3.2) ───────────────────────────
CAMPAIGN_FIELD = "campaign_id"      # utm.campaign m2o — the attribution KEY (§3.1)
BUYER_FIELD = "media_buyer_id"      # res.users m2o — the MAP basis only (§3.2)

# Concentration gate (§3.4). ">= exactly 0.90 qualifies." Compared with integer
# math at the call site (dominant*100 >= total*90) to avoid float-boundary
# ambiguity at exactly 90%.
CONCENTRATION_THRESHOLD = 0.90

# ── 4-group outcome mapping (LOCKED — §3.7) ───────────────────────────────────
# The 4 group labels, in fixed output order. Arabic per the terminology rule.
GROUP_NEW = "جديد"            # New
GROUP_INTERESTED = "مهتم"      # Interested
GROUP_WON = "اشترى"           # Won — defined by crm.stage.is_won = True (dynamic)
GROUP_NO_RESULT = "بلا نتيجة"  # No result

GROUP_ORDER: tuple[str, ...] = (GROUP_NEW, GROUP_INTERESTED, GROUP_WON, GROUP_NO_RESULT)

# Stage names that map to جديد / مهتم by NAME (§3.7). اشترى is NOT name-based —
# it is read from crm.stage.is_won at runtime. Everything not matched by name or
# is_won (and not a null stage) falls through to بلا نتيجة.
NEW_STAGE_NAMES: frozenset[str] = frozenset({"New", "New X"})
INTERESTED_STAGE_NAMES: frozenset[str] = frozenset({"Follow up", "Interested"})


def classify_stage(stage_id, stage_info: dict[int, dict]) -> str:
    """Map a lead's stage to one of the 4 outcome groups (§3.7).

    Args:
        stage_id: the lead's stage id, or a falsey value (False/None/0) when the
            lead has no stage. A null stage -> جديد (§3.7).
        stage_info: {stage_id: {"name": str, "is_won": bool}} read from live
            crm.stage. اشترى is defined by is_won=True (dynamic), never by name.

    Returns one of GROUP_ORDER. The mapping is total: every input lands in
    exactly one group, so per-buyer group counts always reconcile to the total.
    """
    if not stage_id:
        return GROUP_NEW                       # (no stage) -> جديد
    info = stage_info.get(stage_id)
    if info is None:
        return GROUP_NO_RESULT                 # unknown stage id -> بلا نتيجة (safe)
    if info.get("is_won"):
        return GROUP_WON                       # is_won=True -> اشترى (dynamic)
    name = info.get("name")
    if name in NEW_STAGE_NAMES:
        return GROUP_NEW
    if name in INTERESTED_STAGE_NAMES:
        return GROUP_INTERESTED
    return GROUP_NO_RESULT                      # all remaining stages -> بلا نتيجة
