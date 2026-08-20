"""
core/pdf_report.py
--------------------
Generates a printable QC summary report (PDF) for a test + control level
over a date range: header/branding, control info, results table with
Z-scores and rule status, the Levey-Jennings chart, a violation summary,
and sign-off lines (Operator / Technical Director).

FONT / UNICODE HANDLING (fixed after review)
---------------------------------------------
The first version of this file used fpdf2's built-in "Helvetica" core
font, which only supports Latin-1/WinAnsi encoding. That crashed on:
  - the em-dash "—" used in the header ("... Report — Branch: ..."), and
  - the subscript-digit rule names ("1₂s", "2₂s", "10x̄", ...) that show
    up in the results table the moment any rule fires.
i.e. clicking "Generate PDF" could fail on almost any real report.

The fix is to embed a real Unicode TTF font (DejaVu Sans, bundled in
`assets/fonts/` — it ships with matplotlib so no network download was
needed) instead of only patching the specific characters that happened to
trigger the crash. DejaVu Sans covers Latin/Greek/Cyrillic + most
typographic punctuation + subscripts + combining marks, so rule names and
dashes render correctly and natively.

DejaVu Sans does NOT include Arabic glyphs. If `assets/fonts/*.ttf` is
ever missing (e.g. not copied into a deployment), or if a field contains
a script the embedded font can't render (Arabic names, emoji, etc.), a
sanitizer strips or transliterates the offending text so the PDF can
NEVER crash — worst case some text is simplified with a clear
[non-Latin text] marker, but the report always generates. Callers
(pages/5_Reports.py) proactively warn the user if this is likely to
happen, so it's not a silent surprise.

Known fpdf2 gotcha carried over from the Orange Price List project:
`multi_cell` moves the Y cursor in a way that can drift if you don't
reset X/Y afterwards — every multi_cell call below is followed by an
explicit `set_xy`/`ln` reset rather than relying on the implicit cursor
position.

Logo: if a file exists at ASSET_LOGO_PATH, it is embedded in the header.
Otherwise the header falls back to a plain text wordmark. Drop your saved
`Orange_Logo_transparent.png` into the `assets/` folder to enable it.
"""

import os
from datetime import datetime
from typing import List, Dict, Any, Optional

from fpdf import FPDF

from core.text_sanitize import sanitize_for_unicode_font, sanitize_for_core_font, has_non_latin_text  # noqa: F401

ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
ASSET_LOGO_PATH = os.path.join(ASSET_DIR, "Orange_Logo_transparent.png")
FONT_DIR = os.path.join(ASSET_DIR, "fonts")
FONT_REGULAR_PATH = os.path.join(FONT_DIR, "DejaVuSans.ttf")
FONT_BOLD_PATH = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")

STATUS_LABEL = {"in_control": "In control", "warning": "Warning", "reject": "REJECT"}
STATUS_RGB = {
    "in_control": (46, 125, 50),
    "warning": (249, 168, 37),
    "reject": (198, 40, 40),
}


class QCReportPDF(FPDF):
    def __init__(self, lab_name: str, branch: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.lab_name = lab_name
        self.branch = branch
        self.set_auto_page_break(auto=True, margin=18)

        self.unicode_font_available = False
        self.font_family = "Helvetica"
        try:
            if os.path.exists(FONT_REGULAR_PATH) and os.path.exists(FONT_BOLD_PATH):
                self.add_font("DejaVu", "", FONT_REGULAR_PATH)
                self.add_font("DejaVu", "B", FONT_BOLD_PATH)
                self.font_family = "DejaVu"
                self.unicode_font_available = True
        except Exception:
            # Any font-loading problem -> silently fall back to the core
            # font + the stricter sanitizer. The report must still generate.
            self.font_family = "Helvetica"
            self.unicode_font_available = False

    def safe(self, text: Any) -> str:
        return sanitize_for_unicode_font(text) if self.unicode_font_available else sanitize_for_core_font(text)

    def sfont(self, style: str = "", size: float = 9):
        self.set_font(self.font_family, style, size)

    def header(self):
        y0 = self.get_y()
        if os.path.exists(ASSET_LOGO_PATH):
            self.image(ASSET_LOGO_PATH, x=10, y=8, w=32)
            self.set_xy(45, 10)
        else:
            self.set_xy(10, 10)
        self.sfont("B", 14)
        self.cell(0, 7, self.safe(self.lab_name), ln=1)
        self.set_x(45 if os.path.exists(ASSET_LOGO_PATH) else 10)
        self.sfont("", 9)
        self.cell(0, 5, self.safe(f"QC / Westgard Summary Report — Branch: {self.branch}"), ln=1)
        self.set_y(max(self.get_y(), y0 + 24))
        self.set_draw_color(230, 126, 34)
        self.set_line_width(0.6)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.sfont("", 7)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, self.safe(
            f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} — "
            f"Orange Lab BK-280 QC Program — Page {self.page_no()}"
        ), align="C")
        self.set_text_color(0, 0, 0)


def _section_title(pdf: QCReportPDF, text: str):
    pdf.sfont("B", 11)
    pdf.set_fill_color(255, 237, 220)
    pdf.cell(0, 7, f"  {pdf.safe(text)}", ln=1, fill=True)
    pdf.ln(1)


def _kv_row(pdf: QCReportPDF, pairs: List[tuple]):
    pdf.sfont("", 9)
    col_w = 190 / len(pairs)
    for label, value in pairs:
        x, y = pdf.get_x(), pdf.get_y()
        pdf.sfont("B", 8.5)
        pdf.cell(col_w, 5, pdf.safe(label), ln=0)
        pdf.set_xy(x, y + 5)
        pdf.sfont("", 9)
        pdf.cell(col_w, 5, pdf.safe(value))
        pdf.set_xy(x + col_w, y)
    pdf.ln(11)


def generate_qc_summary_pdf(
    lab_name: str,
    branch: str,
    test_name: str,
    unit: str,
    control_name: str,
    lot_number: str,
    mean: float,
    sd: float,
    date_range: str,
    records: List[Dict[str, Any]],
    chart_png_bytes: Optional[bytes],
    prepared_by: str,
    director_name: str,
    extended_rules_enabled: bool,
) -> bytes:
    """
    records: list of dicts with keys
      date, run_number, result, z, status, rule_names(list[str])
    already sorted chronologically (oldest -> newest). `status` and
    `rule_names` MUST be the values that were actually persisted at QC
    entry time (source of truth) — this function does not re-derive a
    verdict, it only prints one that was already decided.
    """
    pdf = QCReportPDF(lab_name=lab_name, branch=branch)
    pdf.add_page()

    _section_title(pdf, f"{test_name} — {control_name}")
    cv = round((sd / mean) * 100, 2) if mean else 0.0
    _kv_row(pdf, [
        ("Unit", unit or "-"),
        ("Lot number", lot_number or "-"),
        ("Mean", f"{mean:g}"),
        ("SD", f"{sd:g}"),
        ("CV%", f"{cv:g}%"),
    ])
    _kv_row(pdf, [
        ("Date range", date_range),
        ("Total runs", str(len(records))),
        ("Extended rules", "ON (8x/9x/12x/7T)" if extended_rules_enabled else "OFF (standard multirule)"),
    ])

    n_reject = sum(1 for r in records if r["status"] == "reject")
    n_warn = sum(1 for r in records if r["status"] == "warning")
    n_ok = len(records) - n_reject - n_warn
    _section_title(pdf, "Summary")
    pdf.sfont("", 9.5)
    pdf.multi_cell(
        0, 5.5,
        pdf.safe(
            f"In control: {n_ok}    |    Warning (1-2s trigger only): {n_warn}    |    "
            f"Rejected runs: {n_reject}   ({(n_reject / len(records) * 100 if records else 0):.1f}% reject rate)"
        )
    )
    pdf.set_xy(10, pdf.get_y())
    pdf.ln(2)

    if chart_png_bytes:
        chart_path = "/tmp/_lj_chart_tmp.png"
        with open(chart_path, "wb") as f:
            f.write(chart_png_bytes)
        _section_title(pdf, "Levey-Jennings Chart")
        pdf.image(chart_path, x=10, w=190)
        pdf.ln(3)

    _section_title(pdf, "Results log")
    headers = ["Date", "Run", "Result", "Z-score", "Status", "Rule(s)"]
    widths = [26, 14, 22, 22, 26, 80]
    pdf.sfont("B", 8)
    pdf.set_fill_color(245, 245, 245)
    for h, w in zip(headers, widths):
        pdf.cell(w, 6, pdf.safe(h), border=1, fill=True, align="C")
    pdf.ln()

    pdf.sfont("", 8)
    for r in records:
        rgb = STATUS_RGB.get(r["status"], (0, 0, 0))
        row = [
            r["date"], str(r["run_number"]), f"{r['result']:g}", f"{r['z']:.2f}",
            STATUS_LABEL.get(r["status"], r["status"]),
            ", ".join(r.get("rule_names", [])) or "-",
        ]
        if pdf.get_y() > 265:
            pdf.add_page()
            pdf.sfont("B", 8)
            pdf.set_fill_color(245, 245, 245)
            for h, w in zip(headers, widths):
                pdf.cell(w, 6, pdf.safe(h), border=1, fill=True, align="C")
            pdf.ln()
            pdf.sfont("", 8)
        for i, (val, w) in enumerate(zip(row, widths)):
            if i == 4:
                pdf.set_text_color(*rgb)
            pdf.cell(w, 6, pdf.safe(val), border=1, align="C" if i != 5 else "L")
            pdf.set_text_color(0, 0, 0)
        pdf.ln()

    pdf.ln(8)
    _section_title(pdf, "Sign-off")
    y = pdf.get_y()
    pdf.sfont("", 9)
    pdf.set_xy(10, y)
    pdf.cell(90, 5, pdf.safe(f"Prepared by: {prepared_by}"))
    pdf.set_xy(110, y)
    pdf.cell(90, 5, pdf.safe(f"Reviewed & approved by: {director_name}"))
    pdf.set_xy(10, y + 14)
    pdf.cell(90, 0, "", border="T")
    pdf.set_xy(110, y + 14)
    pdf.cell(90, 0, "", border="T")
    pdf.set_xy(10, y + 15)
    pdf.sfont("", 7.5)
    pdf.cell(90, 5, "Signature / Date")
    pdf.set_xy(110, y + 15)
    pdf.cell(90, 5, "Signature / Date (Laboratory Director)")

    return bytes(pdf.output())
