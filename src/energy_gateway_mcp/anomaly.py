from . import formatting, gateway_client


class AnomalyError(Exception):
    """Raised when the input series is invalid."""


def find_anomaly(x_labels, series, threshold_pct=15.0):
    """Return the single largest year-over-year %-change anomaly across
    all series, or None if nothing exceeds threshold_pct. Raises
    AnomalyError if a series' length doesn't match x_labels."""
    best = None
    for name, values in series.items():
        if len(values) != len(x_labels):
            raise AnomalyError(
                f"Series '{name}' has {len(values)} values but there are "
                f"{len(x_labels)} x_labels; they must match."
            )
        for i in range(1, len(values)):
            prev, curr = values[i - 1], values[i]
            if prev == 0:
                continue
            pct_change = (curr - prev) / abs(prev) * 100
            if abs(pct_change) >= threshold_pct and (
                best is None or abs(pct_change) > abs(best["pct_change"])
            ):
                best = {
                    "series_name": name,
                    "from_label": x_labels[i - 1],
                    "to_label": x_labels[i],
                    "from_value": prev,
                    "to_value": curr,
                    "pct_change": pct_change,
                }
    return best


def explain_anomaly(topic, x_labels, series, threshold_pct=15.0):
    """Find the biggest anomaly in the given series and look up what the
    SFOE knowledge base says about the year it lands on."""
    anomaly = find_anomaly(x_labels, series, threshold_pct)
    if anomaly is None:
        return (
            f"No year-over-year change of at least {threshold_pct:.0f}% was "
            f"found in the provided series for '{topic}'."
        )

    direction = "increased" if anomaly["pct_change"] > 0 else "decreased"
    summary = (
        f"{anomaly['series_name']} {direction} by {abs(anomaly['pct_change']):.1f}% "
        f"between {anomaly['from_label']} and {anomaly['to_label']} "
        f"({anomaly['from_value']:g} -> {anomaly['to_value']:g})."
    )

    query = f"{topic} {anomaly['to_label']}"
    try:
        raw = gateway_client.retrieve(query)
        narrative = formatting.format_answer(query, raw)
    except (gateway_client.AuthError, gateway_client.GatewayError) as e:
        narrative = f"(Could not retrieve narrative context: {e})"

    return (
        f"{summary}\n\n"
        f"## What the knowledge base says about {anomaly['to_label']}\n"
        f"{narrative}"
    )
