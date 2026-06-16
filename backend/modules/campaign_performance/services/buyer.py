"""
Per-campaign media-buyer DISPLAY rule (§7.1, amended) — ONE pure helper.

Factored out of campaign_service.py (Level 1) so both the Level-1 per-campaign
funnel and the Level-2 per-campaign timeline derive the buyer header from the
EXACT same logic — byte-for-byte the 5-state rule, the integer-exact concentration
math, the minimum-sample guard, and the confirmed-drift downgrade + alert text.

DISPLAY only — this does NOT change marketing_attribution's strict attribution
metric. The rule (in order):

    denylist                                  -> excluded_channel (buyer suppressed)
    no both-set leads                          -> no_buyer
    confirmed set AND >=90% AND >=min sample   -> confirmed (buyer shown)
    >= floor (DOMINANT_FLOOR_PCT)  AND >=min   -> dominant   (buyer shown)
    otherwise (has buyer leads)                -> mixed       (no single buyer)

A confirmed campaign that no longer holds >=90% (or no longer has the minimum
sample) is DOWNGRADED to its actual numbers (dominant/mixed) and the function
RETURNS an integrity-alert string (locked-decision drift) for the caller to
surface + log. The function itself is pure: it mutates nothing and does no I/O.
"""

from collections import Counter
from typing import Optional

from backend.modules.campaign_performance.domain import (
    DOMINANT_FLOOR_PCT,
    MIN_BOTH_SET_FOR_BUYER,
)


def _concentration_at_least(dominant_count: int, both_set_count: int, pct: int) -> bool:
    """concentration >= pct%, integer-exact at the boundary (identical math to the
    shipped module's _qualifies, parameterized for the 90% gate and the 50% floor)."""
    if both_set_count <= 0:
        return False
    return dominant_count * 100 >= both_set_count * pct


def derive_buyer_status(
    campaign_id: int,
    campaign_label,
    buyers: Counter,
    buyer_names: dict[int, Optional[str]],
    both_set_count: int,
    *,
    is_confirmed: bool,
    is_denylisted: bool,
) -> tuple[str, Optional[int], Optional[str], Optional[float], int, Optional[str]]:
    """Resolve a campaign's media-buyer display state from its BOTH-SET leads.

    Args:
        campaign_id: the campaign id (used only in the drift-alert text).
        campaign_label: the campaign's display label (name, or the id when no name
            resolved) — rendered with !r in the drift-alert text, matching Level 1.
        buyers: Counter {buyer_id: both_set_lead_count} for this campaign (may be
            empty). The dominant buyer is buyers.most_common(1).
        buyer_names: {buyer_id: name} for rendering the shown buyer.
        both_set_count: total leads with BOTH campaign_id AND media_buyer_id
            (== sum(buyers.values())).
        is_confirmed: campaign_id is in the resolved CONFIRMED gate set.
        is_denylisted: campaign_id is in the resolved DENYLIST gate set.

    Returns:
        (status, buyer_id, buyer_name, concentration, both_set_count, integrity_alert)
        where integrity_alert is a string ONLY when a confirmed campaign drifted
        below the gate (else None). buyer_id/buyer_name/concentration are populated
        ONLY for confirmed/dominant (the states that show a buyer).
    """
    if buyers:
        bid, dom_cnt = buyers.most_common(1)[0]
        bname = buyer_names.get(bid)
    else:
        bid, bname, dom_cnt = None, None, 0
    both = both_set_count

    if is_denylisted:
        return "excluded_channel", None, None, None, both, None
    if both == 0:
        return "no_buyer", None, None, None, 0, None

    conc = round(100.0 * dom_cnt / both, 2)
    enough = both >= MIN_BOTH_SET_FOR_BUYER
    alert: Optional[str] = None
    if is_confirmed:
        if _concentration_at_least(dom_cnt, both, 90) and enough:
            return "confirmed", bid, bname, conc, both, None
        alert = (
            f"INTEGRITY: confirmed campaign {campaign_label!r} "
            f"(id={campaign_id}) dominant buyer {bname!r} holds {conc:.1f}% of {both} "
            f"both-set leads (< 90% gate or < {MIN_BOTH_SET_FOR_BUYER} min "
            f"sample) — shown as non-confirmed. Locked-decision drift."
        )
        # fall through and display by actual numbers
    if _concentration_at_least(dom_cnt, both, DOMINANT_FLOOR_PCT) and enough:
        return "dominant", bid, bname, conc, both, alert
    return "mixed", None, None, None, both, alert
