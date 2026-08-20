"""
core/text_sanitize.py
-----------------------
Pure text-sanitization helpers used by the PDF report (and safe to reuse
anywhere else that needs to guarantee a string is renderable by a given
font). Deliberately has ZERO dependency on fpdf/streamlit so it can be
unit tested on its own — see tests/test_text_sanitize.py.
"""

import re
import unicodedata
from typing import Any

# Last-resort fallback map, used only when we're stuck on a Latin-1-only
# core font (i.e. the embedded Unicode TTF failed to load).
ASCII_TYPOGRAPHIC_MAP = {
    "\u2014": "-", "\u2013": "-", "\u2026": "...",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2082": "2", "\u2083": "3", "\u2084": "4", "\u2081": "1", "\u2080": "0",
    "\u2192": "->",
}

# DejaVu Sans (the Unicode font we embed) has no Arabic glyphs, so this is
# stripped even when the Unicode font IS loaded.
ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]+")


def sanitize_for_unicode_font(text: Any) -> str:
    """For use when a broad Unicode TTF (e.g. DejaVu Sans) is loaded —
    only strips scripts that font genuinely can't render."""
    if text is None:
        return ""
    text = str(text)
    return ARABIC_RE.sub("[non-Latin text]", text)


def sanitize_for_core_font(text: Any) -> str:
    """Full Latin-1-only fallback, used only if the TTF font failed to load."""
    if text is None:
        return ""
    text = str(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    for k, v in ASCII_TYPOGRAPHIC_MAP.items():
        text = text.replace(k, v)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def has_non_latin_text(*fields: Any) -> bool:
    """True if any field contains Arabic-script text — used to proactively
    warn the user before PDF generation rather than let them discover a
    simplified field silently in the output."""
    for f in fields:
        if f and ARABIC_RE.search(str(f)):
            return True
    return False
