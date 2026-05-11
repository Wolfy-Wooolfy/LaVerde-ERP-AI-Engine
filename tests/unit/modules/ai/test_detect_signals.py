"""Unit tests for detect_signals keyword detection."""

from datetime import datetime, timezone

import pytest

from backend.modules.ai.chatter import detect_signals
from backend.modules.ai.schemas import ChatterMessage


def _msg(text: str) -> ChatterMessage:
    return ChatterMessage(
        date=datetime(2026, 4, 1, tzinfo=timezone.utc),
        author="Test",
        body_text=text,
        message_type="comment",
    )


def test_no_signals_on_empty_list():
    result = detect_signals([])
    assert result["has_site_visit"] is False
    assert result["has_phone_attempt"] is False


def test_no_signals_on_unrelated_text():
    result = detect_signals([_msg("عرضنا على العميل الأسعار وسيرد لاحقًا")])
    assert result["has_site_visit"] is False
    assert result["has_phone_attempt"] is False


# ── Site visit ────────────────────────────────────────────────────────────────

def test_detects_site_visit_arabic_معاينة():
    result = detect_signals([_msg("تم عمل معاينة للوحدة")])
    assert result["has_site_visit"] is True


def test_detects_site_visit_arabic_زيارة():
    result = detect_signals([_msg("قام العميل بزيارة الموقع")])
    assert result["has_site_visit"] is True


def test_detects_site_visit_english():
    result = detect_signals([_msg("Customer came for a site visit today")])
    assert result["has_site_visit"] is True


def test_detects_site_visit_viewing():
    result = detect_signals([_msg("Scheduled a viewing for next week")])
    assert result["has_site_visit"] is True


# ── Phone attempt ─────────────────────────────────────────────────────────────

def test_detects_phone_attempt_arabic_مردش():
    result = detect_signals([_msg("اتصلت به مردش")])
    assert result["has_phone_attempt"] is True


def test_detects_phone_attempt_arabic_مغلق():
    result = detect_signals([_msg("الرقم مغلق")])
    assert result["has_phone_attempt"] is True


def test_detects_phone_attempt_english_no_answer():
    result = detect_signals([_msg("Called 3 times, no answer")])
    assert result["has_phone_attempt"] is True


def test_detects_phone_attempt_english_no_response():
    result = detect_signals([_msg("No response from customer")])
    assert result["has_phone_attempt"] is True


# ── Multi-message detection ───────────────────────────────────────────────────

def test_detects_across_multiple_messages():
    msgs = [
        _msg("اتصلت مردش"),
        _msg("Customer interested in unit 3B"),
    ]
    result = detect_signals(msgs)
    assert result["has_phone_attempt"] is True
    assert result["has_site_visit"] is False


def test_both_signals_can_be_true():
    msgs = [
        _msg("معاينة تمت بنجاح"),
        _msg("Called but no answer"),
    ]
    result = detect_signals(msgs)
    assert result["has_site_visit"] is True
    assert result["has_phone_attempt"] is True


def test_detection_is_case_insensitive():
    result = detect_signals([_msg("SITE VISIT completed")])
    assert result["has_site_visit"] is True
