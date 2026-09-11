import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from mcp.server.mcpserver import Image

VALID_CHART_TYPES = ("line", "bar")


class ChartError(Exception):
    """Raised when chart input is invalid."""


def render_chart(title, x_labels, series, chart_type="line", y_label=""):
    """Render a line or (grouped) bar chart PNG from one or more named
    numeric series aligned to x_labels. Returns an mcp Image, ready to be
    returned directly from an MCP tool."""
    if chart_type not in VALID_CHART_TYPES:
        raise ChartError(
            f"Unsupported chart_type '{chart_type}'; use 'line' or 'bar'."
        )
    if not series:
        raise ChartError("series must contain at least one named data series.")
    if not x_labels:
        raise ChartError("x_labels must not be empty.")

    for name, values in series.items():
        if len(values) != len(x_labels):
            raise ChartError(
                f"Series '{name}' has {len(values)} values but there are "
                f"{len(x_labels)} x_labels; they must match."
            )

    fig, ax = plt.subplots(figsize=(8, 5))
    n_series = len(series)

    if chart_type == "bar":
        x = np.arange(len(x_labels))
        width = 0.8 / n_series
        for i, (name, values) in enumerate(series.items()):
            offset = (i - (n_series - 1) / 2) * width
            ax.bar(x + offset, values, width, label=name)
        ax.set_xticks(x)
        ax.set_xticklabels(x_labels)
    else:
        for name, values in series.items():
            ax.plot(x_labels, values, marker="o", label=name)

    ax.set_title(title)
    if y_label:
        ax.set_ylabel(y_label)
    if n_series > 1:
        ax.legend()
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)

    return Image(data=buf.getvalue(), format="png")
