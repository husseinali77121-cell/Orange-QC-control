"""
core/pdf_report.py
--------------------
Generates a printable QC summary report (PDF) for a test + control level
over a date range: header/branding, control info, results table with
Z-scores and rule status, the Levey-Jennings chart, a violation summary,
and sign-off lines (Operator / Technical Director).

Uses fpdf2 (same library already used in the Orange Price List invoice
generator). Known gotcha carried over from that project: fpdf2's
`multi_cell` moves the Y cursor in a way that can drift if you don't reset
X/Y afterwards — every multi_cell call below is followed by an explicit
`set_xy`/`ln` reset rather than relying on the implicit cursor position.

Logo: if a file exists at ASSET_LOGO_PATH, it is embedded in the header.
Otherwise the header falls back to a plain text wordmark so the report
still looks correct with zero setup. Drop your saved
`Orange_Logo_transparent.png` into the `assets/` folder to enable it
(image assets don't persist between Claude sessions — copy it in once,
locally, when you set the project up).
"""

import os
from datetime import datetime
from typing import List, Dict, Any, Optional

from fpdf import FPDF

ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
ASSET_LOGO_PATH = os.path.join(ASSET_DIR, "Orange_Logo_transparent.png")

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

    def header(self):
        y0 = self.get_y()
        if os.path.exists(ASSET_LOGO_PATH):
            self.image(ASSET_LOGO_PATH, x=10, y=8, w=32)
            self.set_xy(45, 10)
        else:
            self.set_xy(10, 10)
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 7, self.lab_name, ln=1)
        self.set_x(45 if os.path.exists(ASSET_LOGO_PATH) else 10)
        self.set_font("Helvetica", "", 9)
        self.cell(0, 5, f"QC / Westgard Summary Report — Branch: {self.branch}", ln=1)
        self.set_y(max(self.get_y(), y0 + 24))
        self.set_draw_color(230, 126, 34)
        self.set_line_width(0.6)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} — "
                         f"Orange Lab BK-280 QC Program — Page {self.page_no()}", align="C")
        self.set_text_color(0, 0, 0)


def _section_title(pdf: QCReportPDF, text: str):
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(255, 237, 220)
    pdf.cell(0, 7, f"  {text}", ln=1, fill=True)
    pdf.ln(1)


def _kv_row(pdf: QCReportPDF, pairs: List[tuple]):
    pdf.set_font("Helvetica", "", 9)
    col_w = 190 / len(pairs)
    for label, value in pairs:
        x, y = pdf.get_x(), pdf.get_y()
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(col_w, 5, label, ln=0)
        pdf.set_xy(x, y + 5)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(col_w, 5, str(value))
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
    already sorted chronologically (oldest -> newest).
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
    pdf.set_font("Helvetica", "", 9.5)
    pdf.multi_cell(
        0, 5.5,
        f"In control: {n_ok}    |    Warning (1-2s trigger only): {n_warn}    |    "
        f"Rejected runs: {n_reject}   ({(n_reject / len(records) * 100 if records else 0):.1f}% reject rate)"
    )
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
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(245, 245, 245)
    for h, w in zip(headers, widths):
        pdf.cell(w, 6, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for r in records:
        rgb = STATUS_RGB.get(r["status"], (0, 0, 0))
        row = [
            r["date"], str(r["run_number"]), f"{r['result']:g}", f"{r['z']:.2f}",
            STATUS_LABEL.get(r["status"], r["status"]),
            ", ".join(r.get("rule_names", [])) or "-",
        ]
        if pdf.get_y() > 265:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_fill_color(245, 245, 245)
            for h, w in zip(headers, widths):
                pdf.cell(w, 6, h, border=1, fill=True, align="C")
            pdf.ln()
            pdf.set_font("Helvetica", "", 8)
        for i, (val, w) in enumerate(zip(row, widths)):
            if i == 4:
                pdf.set_text_color(*rgb)
            pdf.cell(w, 6, str(val), border=1, align="C" if i != 5 else "L")
            pdf.set_text_color(0, 0, 0)
        pdf.ln()

    pdf.ln(8)
    _section_title(pdf, "Sign-off")
    y = pdf.get_y()
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(10, y)
    pdf.cell(90, 5, f"Prepared by: {prepared_by}")
    pdf.set_xy(110, y)
    pdf.cell(90, 5, f"Reviewed & approved by: {director_name}")
    pdf.set_xy(10, y + 14)
    pdf.cell(90, 0, "", border="T")
    pdf.set_xy(110, y + 14)
    pdf.cell(90, 0, "", border="T")
    pdf.set_xy(10, y + 15)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.cell(90, 5, "Signature / Date")
    pdf.set_xy(110, y + 15)
    pdf.cell(90, 5, "Signature / Date (Laboratory Director)")

    return bytes(pdf.output())
