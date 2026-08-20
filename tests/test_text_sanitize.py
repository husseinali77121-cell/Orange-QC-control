"""
tests/test_text_sanitize.py
-----------------------------
Regression tests for the bug found in review: fpdf2's core "Helvetica"
font can't encode an em-dash or the subscript rule names, and would raise
FPDFUnicodeEncodingException the moment a report contained either — which
is basically every real report. These tests lock in the fix.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.text_sanitize import sanitize_for_unicode_font, sanitize_for_core_font, has_non_latin_text
from core.westgard_engine import RULE_INFO


def test_core_font_sanitizer_handles_em_dash():
    text = "QC / Westgard Summary Report — Branch: La Cité"
    out = sanitize_for_core_font(text)
    out.encode("latin-1")  # must not raise
    assert "—" not in out
    assert "-" in out


def test_core_font_sanitizer_handles_subscript_rule_names():
    for rule_id, info in RULE_INFO.items():
        out = sanitize_for_core_font(info["name"])
        out.encode("latin-1")  # must not raise -> this is the exact P0 bug scenario


def test_core_font_sanitizer_handles_x_bar_combining_mark():
    out = sanitize_for_core_font("10x\u0304")  # "10x̄" — x + combining macron
    out.encode("latin-1")
    assert "x" in out


def test_unicode_font_sanitizer_passes_through_normal_text():
    text = "QC / Westgard Summary Report — Branch: La Cité"
    out = sanitize_for_unicode_font(text)
    # DejaVu Sans can render em-dash and accented Latin natively -> unchanged
    assert out == text


def test_unicode_font_sanitizer_strips_arabic_only():
    text = "Operator: أحمد - QC Report"
    out = sanitize_for_unicode_font(text)
    assert "أحمد" not in out
    assert "[non-Latin text]" in out
    assert "QC Report" in out


def test_unicode_font_sanitizer_leaves_rule_subscripts_alone():
    # DejaVu Sans DOES support subscript digits -> no need to mangle them
    for rule_id, info in RULE_INFO.items():
        out = sanitize_for_unicode_font(info["name"])
        assert out == info["name"]


def test_has_non_latin_text_detects_arabic():
    assert has_non_latin_text("حسين علي") is True
    assert has_non_latin_text("Hussein Ali") is False
    assert has_non_latin_text("Dr. Tarek El-Shafei") is False
    assert has_non_latin_text(None, "", "Orange Lab") is False


def test_sanitizers_never_raise_on_empty_or_none():
    assert sanitize_for_core_font(None) == ""
    assert sanitize_for_unicode_font(None) == ""
    assert sanitize_for_core_font("") == ""
    assert sanitize_for_unicode_font(123) == "123"  # non-str input tolerated


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
