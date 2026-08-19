"""
core/charts.py
---------------
Levey-Jennings chart builders.

- `build_lj_plotly(...)`  -> interactive chart for the Streamlit page
- `build_lj_matplotlib(...)` -> static PNG bytes, used when embedding the
  chart into the PDF report (fpdf2 needs a raster image, not a live widget)

Both take the same shape of input: a list of dicts with
  {date, run_number, result, z, status, rule_names}
already computed by the Westgard engine / calling page, plus mean/sd for
the axis bands.
"""

from io import BytesIO
from typing import List, Dict, Any

import plotly.graph_objects as go
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

STATUS_COLORS = {
    "in_control": "#2e7d32",   # green
    "warning": "#f9a825",      # amber
    "reject": "#c62828",       # red
}


def _x_labels(points: List[Dict[str, Any]]) -> List[str]:
    return [f"{p['date']} (run {p['run_number']})" for p in points]


def build_lj_plotly(points: List[Dict[str, Any]], mean: float, sd: float, title: str) -> go.Figure:
    x = _x_labels(points)
    y = [p["result"] for p in points]
    colors = [STATUS_COLORS.get(p["status"], "#1976d2") for p in points]
    hover = [
        f"{p['date']} run {p['run_number']}<br>Result: {p['result']}<br>"
        f"Z: {p['z']:.2f}<br>Status: {p['status'].upper()}"
        + (f"<br>Rules: {', '.join(p['rule_names'])}" if p.get("rule_names") else "")
        for p in points
    ]

    fig = go.Figure()

    # SD bands
    band_specs = [(3, "rgba(198,40,40,0.06)"), (2, "rgba(249,168,37,0.08)"), (1, "rgba(46,125,50,0.06)")]
    for n, color in band_specs:
        fig.add_hrect(y0=mean - n * sd, y1=mean + n * sd, fillcolor=color, line_width=0)

    for n in (1, 2, 3):
        fig.add_hline(y=mean + n * sd, line_dash="dot", line_color="gray", opacity=0.5,
                       annotation_text=f"+{n}SD", annotation_position="right")
        fig.add_hline(y=mean - n * sd, line_dash="dot", line_color="gray", opacity=0.5,
                       annotation_text=f"-{n}SD", annotation_position="right")
    fig.add_hline(y=mean, line_color="black", line_width=1.5,
                  annotation_text="Mean", annotation_position="right")

    fig.add_trace(go.Scatter(
        x=x, y=y, mode="lines+markers",
        marker=dict(color=colors, size=10, line=dict(width=1, color="white")),
        line=dict(color="rgba(100,100,100,0.4)"),
        hovertext=hover, hoverinfo="text",
        name="QC result",
    ))

    # annotate rejects/warnings with the rule code
    for p, label in zip(points, x):
        if p["status"] in ("reject", "warning") and p.get("rule_names"):
            fig.add_annotation(
                x=label, y=p["result"],
                text="/".join(p["rule_names"]),
                showarrow=True, arrowhead=1, yshift=18,
                font=dict(color=STATUS_COLORS[p["status"]], size=11),
            )

    fig.update_layout(
        title=title,
        xaxis_title="Run",
        yaxis_title="Result",
        showlegend=False,
        height=460,
        margin=dict(l=40, r=80, t=60, b=80),
        xaxis=dict(tickangle=-45),
    )
    return fig


def build_lj_matplotlib(points: List[Dict[str, Any]], mean: float, sd: float, title: str) -> bytes:
    x = list(range(len(points)))
    y = [p["result"] for p in points]
    colors = [STATUS_COLORS.get(p["status"], "#1976d2") for p in points]
    labels = _x_labels(points)

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=150)

    for n, alpha in ((3, 0.05), (2, 0.08), (1, 0.05)):
        ax.axhspan(mean - n * sd, mean + n * sd, color="gray", alpha=alpha)
    for n in (1, 2, 3):
        ax.axhline(mean + n * sd, color="gray", linestyle=":", linewidth=0.8)
        ax.axhline(mean - n * sd, color="gray", linestyle=":", linewidth=0.8)
        ax.text(len(x) - 0.5, mean + n * sd, f"+{n}SD", fontsize=7, va="bottom", color="gray")
        ax.text(len(x) - 0.5, mean - n * sd, f"-{n}SD", fontsize=7, va="top", color="gray")
    ax.axhline(mean, color="black", linewidth=1.2)
    ax.text(len(x) - 0.5, mean, "Mean", fontsize=7, va="bottom")

    ax.plot(x, y, color="#999999", linewidth=0.8, zorder=1)
    ax.scatter(x, y, c=colors, s=45, zorder=2, edgecolors="white", linewidths=0.6)

    for xi, p in zip(x, points):
        if p["status"] in ("reject", "warning") and p.get("rule_names"):
            ax.annotate("/".join(p["rule_names"]), (xi, p["result"]),
                        textcoords="offset points", xytext=(0, 8),
                        fontsize=6.5, color=STATUS_COLORS[p["status"]], ha="center")

    ax.set_title(title, fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=6.5)
    ax.set_ylabel("Result")
    fig.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
