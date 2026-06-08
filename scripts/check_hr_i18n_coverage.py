"""
check_hr_i18n_coverage.py — Read-only i18n coverage checker for the HR module.

Scans HR Jinja templates and JS controllers for user-facing English strings
that are NOT routed through _t() or window.HR_STRINGS.

Exit 0 = clean (zero bare strings found).
Exit 1 = bare strings detected (list printed to stdout).

A line is considered COVERED (not flagged) if it contains any of the
coverage markers below. The checker is intentionally permissive for
false-positive avoidance; its job is to catch NEW bare strings added
after the initial i18n pass, not to be an AST parser.

Usage:
  python scripts/check_hr_i18n_coverage.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HR_TEMPLATES = [
    ROOT / "frontend/templates/hr/dashboard.html",
    ROOT / "frontend/templates/components/_hr_dept_panel.html",
    ROOT / "frontend/templates/components/_hr_profile_panel.html",
]

HR_JS = [
    ROOT / "frontend/static/js/hr_drilldown.js",
    ROOT / "frontend/static/js/hr_employee_drilldown.js",
]

# ── Template: covered-line patterns ──────────────────────────────────────────
# A line matching ANY of these is skipped (not flagged).
TMPL_SAFE = [
    re.compile(r'_t\s*\('),           # _t("...") call — wrapped
    re.compile(r'\{\{'),              # {{ variable }} — dynamic
    re.compile(r'\{%'),               # Jinja block tag
    re.compile(r'\{#'),               # Jinja comment
    re.compile(r'<!--'),              # HTML comment
    re.compile(r'\bclass\s*='),       # class attribute (Tailwind tokens)
    re.compile(r'\bid\s*='),          # id attribute
    re.compile(r'\bhref\s*='),        # href
    re.compile(r'\bsrc\s*='),         # src
    re.compile(r'\bstyle\s*='),       # style
    re.compile(r'\bdata-'),           # data-* attributes
    re.compile(r'\btype\s*='),        # input type=
    re.compile(r'\bname\s*='),        # name=
    re.compile(r'\bviewBox\s*='),     # SVG viewBox
    re.compile(r'd\s*=\s*"M'),        # SVG path
    re.compile(r'stroke-'),           # SVG stroke attributes
    re.compile(r'fill-rule'),         # SVG
    re.compile(r'clip-rule'),         # SVG
    re.compile(r'LaVerde'),           # brand name
    re.compile(r'Odoo'),              # product name in technical notes
    re.compile(r'^\s*<[a-z/!]'),      # pure HTML tag lines (no user text)
    re.compile(r'^\s*$'),             # blank
]

# A suspicious template line has a quoted string starting with uppercase, 4+ chars
TMPL_FLAG = re.compile(r'"([A-Z][^"]{3,})"|\'([A-Z][^\']{3,})\'')


def is_tmpl_covered(line: str) -> bool:
    return any(p.search(line) for p in TMPL_SAFE)


def scan_template(path: Path) -> list[tuple[int, str]]:
    issues = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if is_tmpl_covered(raw):
            continue
        if TMPL_FLAG.search(raw.strip()):
            issues.append((lineno, raw.rstrip()))
    return issues


# ── JS: covered-line patterns ─────────────────────────────────────────────────
# A line matching ANY of these is skipped.
JS_SAFE = [
    re.compile(r'_s\s*\('),                     # _s() reads HR_STRINGS — covered
    re.compile(r'HR_STRINGS'),                  # direct HR_STRINGS reference
    re.compile(r'\b_t\s*\('),                   # _t() call
    re.compile(r'\|\|\s*[\'"]'),               # || 'fallback' robustness guard
    re.compile(r'^\s*//'),                      # single-line comment
    re.compile(r'console\.(warn|error|log)'),   # debug
    re.compile(r"['\"]\/api\/"),                # API URL
    re.compile(r"same-origin"),                 # fetch credential
    re.compile(r"data-hr-"),                    # data attribute
    re.compile(r"'button'|\"button\""),         # element type attr
    re.compile(r"'hidden'|\"hidden\""),         # DOM utility
    re.compile(r"translate-x"),                 # Tailwind class token
    re.compile(r"'Tab'|'Escape'"),              # keyboard keys
    re.compile(r"'transitionend'|'DOMContent"), # DOM events
    re.compile(r"'click'|'keydown'"),           # DOM events
    re.compile(r"'inert'|'rtl'|'en-EG'"),      # attribute/locale values
    re.compile(r"hr-dd-|hr-pf-"),              # DOM id strings
    re.compile(r"badge\s|class="),              # class attr in innerHTML
    re.compile(r"text-sm|text-xs|font-|dark:"), # Tailwind tokens in innerHTML
    re.compile(r"px-\d|py-\d|mt-|mb-|ms-"),    # Tailwind spacing
    re.compile(r"w-full|flex |items-|shrink-"), # Tailwind layout
    re.compile(r"truncate|rounded|underline"),  # Tailwind utilities
    re.compile(r"focus-visible:"),              # Tailwind interactive
    re.compile(r"min-w-|tabular"),              # Tailwind
    re.compile(r"button:not|tabindex|a\[href"), # focus selector
    re.compile(r"&amp;|&lt;|&gt;|&quot;|&middot;"),  # HTML entities
    re.compile(r"getComputedStyle|readyState"), # JS API
    re.compile(r"^\s*\*"),                      # block comment line
    re.compile(r"/\*|\*/"),                     # block comment delimiters
    re.compile(r"^\s*$"),                       # blank
]

# Suspicious: quoted string starting uppercase, 4+ chars
JS_FLAG = re.compile(r"'([A-Z][^']{3,})'|\"([A-Z][^\"]{3,})\"")


def is_js_covered(line: str) -> bool:
    return any(p.search(line) for p in JS_SAFE)


def scan_js(path: Path) -> list[tuple[int, str]]:
    issues = []
    in_block_comment = False
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if "/*" in raw:
            in_block_comment = True
        if "*/" in raw:
            in_block_comment = False
            continue
        if in_block_comment:
            continue
        if is_js_covered(raw):
            continue
        if JS_FLAG.search(raw.strip()):
            issues.append((lineno, raw.rstrip()))
    return issues


def main() -> int:
    all_issues: list[tuple[str, int, str]] = []

    for tmpl in HR_TEMPLATES:
        if not tmpl.exists():
            print(f"WARNING: template not found: {tmpl}")
            continue
        for lineno, line in scan_template(tmpl):
            all_issues.append((str(tmpl.relative_to(ROOT)), lineno, line))

    for js in HR_JS:
        if not js.exists():
            print(f"WARNING: JS file not found: {js}")
            continue
        for lineno, line in scan_js(js):
            all_issues.append((str(js.relative_to(ROOT)), lineno, line))

    if not all_issues:
        print("check_hr_i18n_coverage: OK — 0 bare HR strings found.")
        return 0

    print(f"check_hr_i18n_coverage: {len(all_issues)} potential bare string(s) found:\n")
    for fpath, lineno, line in all_issues:
        print(f"  {fpath}:{lineno}: {line}")
    print(
        "\nReview each line. False positives: attribute values, brand names, "
        "technical tokens. True positives: wrap in _t() or route through HR_STRINGS."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
