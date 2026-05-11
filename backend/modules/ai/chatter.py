"""Chatter HTML stripping and activity-signal detection."""

from __future__ import annotations

import re
from html import unescape

# ── Signal keyword lists ──────────────────────────────────────────────────────

SITE_VISIT_KEYWORDS: list[str] = [
    # Arabic
    "معاينة", "زيارة", "دخل", "اتفرج", "شاف الموقع",
    # English
    "site visit", "visited", "viewing", "tour",
]

PHONE_ATTEMPT_KEYWORDS: list[str] = [
    # Arabic
    "مردش", "مرد", "مغلق", "اتصلت", "كلمته",
    # English
    "didn't answer", "no response", "called", "no answer",
]


# ── HTML cleaning ─────────────────────────────────────────────────────────────


def clean_chatter_body(html: str, max_len: int = 300) -> str:
    """Strip HTML tags, unescape entities, collapse whitespace, truncate."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len] + "..." if len(text) > max_len else text


# ── Signal detection ──────────────────────────────────────────────────────────


def detect_signals(messages: list) -> dict[str, bool]:
    """Return site-visit and phone-attempt flags based on chatter keywords."""
    combined = " ".join(m.body_text for m in messages).lower()
    return {
        "has_site_visit": any(kw in combined for kw in SITE_VISIT_KEYWORDS),
        "has_phone_attempt": any(kw in combined for kw in PHONE_ATTEMPT_KEYWORDS),
    }
