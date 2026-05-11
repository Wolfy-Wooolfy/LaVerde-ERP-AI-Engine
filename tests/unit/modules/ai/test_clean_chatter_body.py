"""Unit tests for clean_chatter_body HTML-stripping helper."""

import pytest

from backend.modules.ai.chatter import clean_chatter_body


def test_strips_html_tags():
    assert clean_chatter_body("<p>Hello world</p>") == "Hello world"


def test_strips_nested_html():
    assert clean_chatter_body("<div><b>Bold</b> text</div>") == "Bold text"


def test_unescapes_html_entities():
    assert clean_chatter_body("&amp;amp; &lt;tag&gt; &quot;quoted&quot;") == '&amp; <tag> "quoted"'


def test_collapses_whitespace():
    assert clean_chatter_body("<p>  too   many   spaces  </p>") == "too many spaces"


def test_collapses_newlines_and_tabs():
    result = clean_chatter_body("<p>line1</p>\n\t<p>line2</p>")
    assert "\n" not in result
    assert "\t" not in result
    assert "line1" in result and "line2" in result


def test_empty_string_returns_empty():
    assert clean_chatter_body("") == ""


def test_none_like_falsy_returns_empty():
    assert clean_chatter_body(None) == ""  # type: ignore[arg-type]


def test_truncates_long_text():
    long_text = "A" * 400
    result = clean_chatter_body(long_text)
    assert result.endswith("...")
    assert len(result) == 303  # 300 chars + "..."


def test_exact_300_chars_not_truncated():
    text = "B" * 300
    result = clean_chatter_body(text)
    assert not result.endswith("...")
    assert len(result) == 300


def test_preserves_arabic_text():
    arabic = "تم التواصل مع العميل وأبدى اهتمامه بالمشروع"
    result = clean_chatter_body(f"<p>{arabic}</p>")
    assert result == arabic


def test_preserves_emoji():
    assert clean_chatter_body("<p>Good job 🎉</p>") == "Good job 🎉"


def test_mixed_arabic_english_html():
    html = "<div><b>Follow up:</b> اتصل بالعميل على WhatsApp</div>"
    result = clean_chatter_body(html)
    assert "Follow up:" in result
    assert "WhatsApp" in result
    assert "اتصل بالعميل" in result
    assert "<" not in result
