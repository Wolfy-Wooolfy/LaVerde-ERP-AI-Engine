"""Content-hash fingerprints for static assets (cache busting).

Built once at module import — the load_translations() precedent — so the
manifest exists in every runtime mode, including no-lifespan test clients.
Hashes are CONTENT-derived, never mtime: a fresh git clone rewrites every
mtime (mass false bust) while an mtime-preserving deploy copy would fail to
bust genuinely changed content.

Fonts under vendor/fonts/ are deliberately excluded: they have never changed
in repo history and are referenced from inside fonts.css, which IS
fingerprinted — a future font swap is handled by shipping the new font under
a new filename and editing fonts.css, whose own fingerprint then busts the
chain correctly.
"""

import hashlib
from pathlib import Path

from loguru import logger

STATIC_DIR = Path("frontend/static")
_EXCLUDED_PREFIX = "vendor/fonts/"
_FINGERPRINT_LEN = 12


def build_manifest(static_dir: Path = STATIC_DIR) -> dict[str, str]:
    """Map every file under static_dir (path relative to it, posix) to a short
    sha256 content fingerprint. Files under vendor/fonts/ are skipped."""
    manifest: dict[str, str] = {}
    if not static_dir.is_dir():
        logger.warning(
            f"Static manifest: directory '{static_dir}' not found — every "
            f"static_url() will emit a bare (unfingerprinted) URL"
        )
        return manifest
    for file in sorted(static_dir.rglob("*")):
        if not file.is_file():
            continue
        rel = file.relative_to(static_dir).as_posix()
        if rel.startswith(_EXCLUDED_PREFIX):
            continue
        digest = hashlib.sha256(file.read_bytes()).hexdigest()[:_FINGERPRINT_LEN]
        manifest[rel] = digest
    return manifest


MANIFEST: dict[str, str] = build_manifest()


def static_url(path: str) -> str:
    """Return /static/<path>?v=<content-hash> for a manifest-known asset.

    On a miss, emit the bare URL and log a warning — never raise: a missing
    asset must stay a per-asset 404/log, not a page-wide 500. Bare /static
    URLs are served Cache-Control: no-cache, so the degradation is lost cache
    efficiency, never staleness.
    """
    key = path.lstrip("/")
    fingerprint = MANIFEST.get(key)
    if fingerprint is None:
        logger.warning(f"static_url: '{path}' is not in the static manifest — emitting bare URL")
        return f"/static/{key}"
    return f"/static/{key}?v={fingerprint}"
