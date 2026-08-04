"""Unit tests for the content-hash static-asset manifest (cache busting).

The manifest must be CONTENT-derived, never mtime-derived: a fresh git clone
rewrites every mtime (mass false bust) while an mtime-preserving deploy copy
would fail to bust genuinely changed content. test_hash_ignores_mtime encodes
that decision.
"""

import os
from pathlib import Path

from loguru import logger

from backend.core.static_manifest import MANIFEST, build_manifest, static_url


def test_hash_is_content_derived(tmp_path: Path) -> None:
    """Same bytes → same fingerprint; different bytes → different fingerprint."""
    (tmp_path / "a.js").write_bytes(b"alert(1);")
    first = build_manifest(tmp_path)["a.js"]

    (tmp_path / "a.js").write_bytes(b"alert(2);")
    changed = build_manifest(tmp_path)["a.js"]

    (tmp_path / "a.js").write_bytes(b"alert(1);")
    restored = build_manifest(tmp_path)["a.js"]

    assert first != changed
    assert restored == first


def test_hash_ignores_mtime(tmp_path: Path) -> None:
    """Rewriting identical bytes with a different mtime must not change the
    fingerprint — mtime was rejected as a fingerprint source by design."""
    f = tmp_path / "a.js"
    f.write_bytes(b"alert(1);")
    before = build_manifest(tmp_path)["a.js"]

    os.utime(f, (1_000_000_000, 1_000_000_000))
    after = build_manifest(tmp_path)["a.js"]

    assert after == before


def test_fonts_excluded_and_real_assets_present() -> None:
    """The import-time manifest covers the real assets but never the font files;
    fonts.css itself IS fingerprinted (it carries the font URLs, so its own
    fingerprint busts the chain when a font is swapped via rename)."""
    assert "js/app.js" in MANIFEST
    assert "css/app.css" in MANIFEST
    assert "vendor/fonts.css" in MANIFEST
    assert not any(key.startswith("vendor/fonts/") for key in MANIFEST)


def test_static_url_emits_versioned_url() -> None:
    assert static_url("js/app.js") == f"/static/js/app.js?v={MANIFEST['js/app.js']}"


def test_static_url_missing_file_falls_back_to_bare_url() -> None:
    """A manifest miss must emit the bare URL and log a warning — never raise.
    Bare URLs are served Cache-Control: no-cache, so the failure mode is lost
    cache efficiency, never staleness."""
    messages: list[str] = []
    handler_id = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        url = static_url("js/__not_a_real_file__.js")
    finally:
        logger.remove(handler_id)
    assert url == "/static/js/__not_a_real_file__.js"
    assert any("__not_a_real_file__" in m for m in messages)
