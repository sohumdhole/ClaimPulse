"""Shared chart style. Categorical slots 1-3 identify the three logs (validated
all-pairs for colour-vision deficiency); status colours are reserved for RAG and
always ship with a text label."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

LOG_COLOURS = {"DD": "#2a78d6", "ID": "#eb6834", "RFP": "#1baf7a"}
LOG_LABELS = {"DD": "Domestic declarations", "ID": "International declarations", "RFP": "Requests for payment"}
STATUS = {"GREEN": "#0ca30c", "AMBER": "#fab219", "RED": "#d03b3b"}
SEQ_BLUE = "#3987e5"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e4e3df"


def style():
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
        "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False, "axes.titleweight": "bold",
        "axes.titlesize": 12, "axes.titlelocation": "left", "font.size": 10, "lines.linewidth": 2,
        "legend.frameon": False, "figure.dpi": 100, "savefig.dpi": 150, "savefig.bbox": "tight",
    })


def save(fig, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return str(path)
