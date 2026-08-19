"""Server rendered SVG charts.

Charts are built as SVG strings in Python rather than by a JavaScript charting
library. The dashboard therefore has no CDN dependency, no vendored bundle, and
no network access of its own: it renders identically on a laptop that is
offline, and every chart is a pure function that tests can assert on.

Colors come from CSS custom properties defined in the stylesheet, so light and
dark themes both work without regenerating anything.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Callable, Sequence

Number = float | int


@dataclass
class Series:
    label: str
    points: Sequence[tuple[str, float | None]]
    color: str = "var(--series-1)"
    width: float = 2.0
    dashed: bool = False


def _escape(text: object) -> str:
    return html.escape(str(text), quote=True)


def _nice_bounds(values: Sequence[float], pad_ratio: float = 0.08) -> tuple[float, float]:
    if not values:
        return 0.0, 1.0
    low, high = min(values), max(values)
    if low == high:
        return (low - 1.0, high + 1.0) if low else (0.0, 1.0)
    padding = (high - low) * pad_ratio
    return low - padding, high + padding


def _ticks(low: float, high: float, count: int = 4) -> list[float]:
    if high <= low:
        return [low]
    step = (high - low) / count
    return [low + step * index for index in range(count + 1)]


def empty_chart(message: str, height: int = 220) -> str:
    return (
        f'<svg class="chart" viewBox="0 0 900 {height}" preserveAspectRatio="none" '
        f'role="img" aria-label="{_escape(message)}">'
        f'<rect x="0" y="0" width="900" height="{height}" fill="var(--surface-2)" rx="8"/>'
        f'<text x="450" y="{height // 2}" text-anchor="middle" class="chart-empty">'
        f"{_escape(message)}</text></svg>"
    )


def line_chart(
    series: Sequence[Series],
    *,
    height: int = 260,
    width: int = 900,
    value_format: Callable[[float], str] = lambda value: f"{value:,.1f}",
    y_label: str = "",
    zero_based: bool = False,
    max_x_labels: int = 8,
) -> str:
    """Multi series line chart over a shared, ordered, categorical x axis."""
    series = [item for item in series if item.points]
    if not series:
        return empty_chart("No data for this range")

    labels = [label for label, _ in series[0].points]
    values = [value for item in series for _, value in item.points if value is not None]
    if not values:
        return empty_chart("No data for this range")

    low, high = _nice_bounds(values)
    if zero_based:
        low = min(0.0, low)

    left, right, top, bottom = 62, 16, 16, 34
    plot_width = width - left - right
    plot_height = height - top - bottom
    span = max(1, len(labels) - 1)

    def x_of(index: int) -> float:
        return left + plot_width * (index / span)

    def y_of(value: float) -> float:
        return top + plot_height * (1 - (value - low) / (high - low or 1))

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{_escape(y_label or "chart")}">'
    ]

    for tick in _ticks(low, high):
        y = y_of(tick)
        parts.append(
            f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"/>'
            f'<text class="axis" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">'
            f"{_escape(value_format(tick))}</text>"
        )

    step = max(1, len(labels) // max_x_labels)
    for index in range(0, len(labels), step):
        parts.append(
            f'<text class="axis" x="{x_of(index):.1f}" y="{height - 12}" '
            f'text-anchor="middle">{_escape(labels[index])}</text>'
        )

    for item in series:
        segments: list[list[str]] = [[]]
        for index, (_, value) in enumerate(item.points):
            if value is None:
                # Break the line rather than bridging a gap in the data.
                if segments[-1]:
                    segments.append([])
                continue
            segments[-1].append(f"{x_of(index):.1f},{y_of(value):.1f}")
        for segment in segments:
            if len(segment) > 1:
                parts.append(
                    f'<polyline class="line" points="{" ".join(segment)}" '
                    f'stroke="{item.color}" stroke-width="{item.width}" '
                    f'{"stroke-dasharray=\'5 4\'" if item.dashed else ""}/>'
                )
            elif segment:
                x, y = segment[0].split(",")
                parts.append(f'<circle cx="{x}" cy="{y}" r="2.5" fill="{item.color}"/>')

        for index, (label, value) in enumerate(item.points):
            if value is None:
                continue
            parts.append(
                f'<circle class="dot" cx="{x_of(index):.1f}" cy="{y_of(value):.1f}" r="6" '
                f'fill="transparent"><title>{_escape(label)}: '
                f"{_escape(value_format(value))}</title></circle>"
            )

    parts.append("</svg>")
    legend = ""
    if len(series) > 1:
        chips = "".join(
            f'<span class="legend-item"><span class="swatch" style="background:{item.color}">'
            f"</span>{_escape(item.label)}</span>"
            for item in series
        )
        legend = f'<div class="legend">{chips}</div>'
    return "".join(parts) + legend


def stacked_bar_chart(
    categories: Sequence[str],
    stacks: Sequence[Sequence[tuple[str, float, str]]],
    *,
    height: int = 300,
    width: int = 900,
    value_format: Callable[[float], str] = lambda value: f"{value:,.0f}",
    y_label: str = "",
    max_x_labels: int = 12,
) -> str:
    """One bar per category. Each stack is a list of (name, value, color)."""
    if not categories or not any(stacks):
        return empty_chart("No data for this range", height)

    totals = [sum(value for _, value, _ in stack) for stack in stacks]
    high = max(totals) or 1.0
    left, right, top, bottom = 62, 16, 16, 34
    plot_width = width - left - right
    plot_height = height - top - bottom
    slot = plot_width / max(1, len(categories))
    bar_width = max(2.0, min(slot * 0.72, 28.0))

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{_escape(y_label or "stacked bars")}">'
    ]
    for tick in _ticks(0, high):
        y = top + plot_height * (1 - tick / high)
        parts.append(
            f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"/>'
            f'<text class="axis" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">'
            f"{_escape(value_format(tick))}</text>"
        )

    step = max(1, len(categories) // max_x_labels)
    for index, (category, stack) in enumerate(zip(categories, stacks)):
        center = left + slot * (index + 0.5)
        cursor = top + plot_height
        for name, value, color in stack:
            if not value:
                continue
            bar_height = plot_height * (value / high)
            cursor -= bar_height
            parts.append(
                f'<rect x="{center - bar_width / 2:.1f}" y="{cursor:.1f}" '
                f'width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{color}">'
                f"<title>{_escape(category)} {_escape(name)}: "
                f"{_escape(value_format(value))}</title></rect>"
            )
        if index % step == 0:
            parts.append(
                f'<text class="axis" x="{center:.1f}" y="{height - 12}" '
                f'text-anchor="middle">{_escape(category)}</text>'
            )
    parts.append("</svg>")

    names: list[tuple[str, str]] = []
    for stack in stacks:
        for name, _value, color in stack:
            if (name, color) not in names:
                names.append((name, color))
    chips = "".join(
        f'<span class="legend-item"><span class="swatch" style="background:{color}"></span>'
        f"{_escape(name)}</span>"
        for name, color in names
    )
    return "".join(parts) + f'<div class="legend">{chips}</div>'


def scatter_chart(
    points: Sequence[tuple[float, float, str]],
    *,
    x_label: str,
    y_label: str,
    fit: tuple[float, float] | None = None,
    height: int = 340,
    width: int = 900,
    x_format: Callable[[float], str] = lambda value: f"{value:,.0f}",
    y_format: Callable[[float], str] = lambda value: f"{value:,.1f}",
) -> str:
    if len(points) < 2:
        return empty_chart("Not enough paired days to plot", height)

    xs = [x for x, _, _ in points]
    ys = [y for _, y, _ in points]
    x_low, x_high = _nice_bounds(xs)
    y_low, y_high = _nice_bounds(ys)

    left, right, top, bottom = 70, 20, 20, 48
    plot_width = width - left - right
    plot_height = height - top - bottom

    def x_of(value: float) -> float:
        return left + plot_width * (value - x_low) / (x_high - x_low or 1)

    def y_of(value: float) -> float:
        return top + plot_height * (1 - (value - y_low) / (y_high - y_low or 1))

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{_escape(x_label)} against {_escape(y_label)}">'
    ]
    for tick in _ticks(y_low, y_high):
        y = y_of(tick)
        parts.append(
            f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"/>'
            f'<text class="axis" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">'
            f"{_escape(y_format(tick))}</text>"
        )
    for tick in _ticks(x_low, x_high):
        x = x_of(tick)
        parts.append(
            f'<text class="axis" x="{x:.1f}" y="{height - 26}" text-anchor="middle">'
            f"{_escape(x_format(tick))}</text>"
        )

    if fit:
        slope, intercept = fit
        parts.append(
            f'<line class="fit" x1="{x_of(x_low):.1f}" y1="{y_of(slope * x_low + intercept):.1f}" '
            f'x2="{x_of(x_high):.1f}" y2="{y_of(slope * x_high + intercept):.1f}"/>'
        )

    for x, y, label in points:
        parts.append(
            f'<circle class="point" cx="{x_of(x):.1f}" cy="{y_of(y):.1f}" r="4">'
            f"<title>{_escape(label)}</title></circle>"
        )

    parts.append(
        f'<text class="axis-title" x="{left + plot_width / 2:.1f}" y="{height - 6}" '
        f'text-anchor="middle">{_escape(x_label)}</text>'
        f'<text class="axis-title" transform="rotate(-90 14 {top + plot_height / 2:.1f})" '
        f'x="14" y="{top + plot_height / 2:.1f}" text-anchor="middle">{_escape(y_label)}</text>'
        "</svg>"
    )
    return "".join(parts)


ZONE_COLORS = {
    "LIGHT": "var(--zone-light)",
    "MODERATE": "var(--zone-moderate)",
    "VIGOROUS": "var(--zone-vigorous)",
    "PEAK": "var(--zone-peak)",
}


def intraday_heartrate_chart(
    samples: Sequence[tuple[float, int, str]],
    zones: Sequence[dict[str, object]],
    *,
    height: int = 340,
    width: int = 900,
) -> str:
    """Minute level heart rate across one local day, with zone shading.

    `samples` is (hours since local midnight, bpm, tooltip). Every sample the
    API returned is drawn; nothing is downsampled, so the chart shows the true
    granularity of the device.
    """
    if not samples:
        return empty_chart("No intraday heart rate for this day", height)

    bpms = [bpm for _, bpm, _ in samples]
    zone_tops = [
        float(zone["max_bpm"]) for zone in zones if zone.get("max_bpm") not in (None, "")
    ]
    low = max(30.0, min(bpms) - 8)
    high = max(max(bpms) + 8, max(zone_tops) if zone_tops else 0)

    left, right, top, bottom = 56, 16, 16, 34
    plot_width = width - left - right
    plot_height = height - top - bottom

    def x_of(hours: float) -> float:
        return left + plot_width * min(max(hours, 0.0), 24.0) / 24.0

    def y_of(bpm: float) -> float:
        return top + plot_height * (1 - (bpm - low) / (high - low or 1))

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Intraday heart rate">'
    ]

    for zone in zones:
        zone_min = zone.get("min_bpm")
        zone_max = zone.get("max_bpm")
        if zone_min in (None, "") or zone_max in (None, ""):
            continue
        y_top = y_of(min(float(zone_max), high))
        y_bottom = y_of(max(float(zone_min), low))
        if y_bottom <= y_top:
            continue
        color = ZONE_COLORS.get(str(zone.get("zone")), "var(--zone-light)")
        parts.append(
            f'<rect class="zone" x="{left}" y="{y_top:.1f}" width="{plot_width:.1f}" '
            f'height="{y_bottom - y_top:.1f}" fill="{color}">'
            f'<title>{_escape(zone.get("zone"))}: {_escape(zone_min)} to '
            f"{_escape(zone_max)} bpm</title></rect>"
        )

    for tick in _ticks(low, high):
        y = y_of(tick)
        parts.append(
            f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"/>'
            f'<text class="axis" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">'
            f"{tick:.0f}</text>"
        )
    for hour in range(0, 25, 3):
        parts.append(
            f'<text class="axis" x="{x_of(hour):.1f}" y="{height - 12}" text-anchor="middle">'
            f"{hour:02d}:00</text>"
        )

    # Break the trace wherever the watch stopped reporting for more than ten
    # minutes, so a gap reads as a gap rather than a straight line.
    segments: list[list[str]] = [[]]
    previous_hours: float | None = None
    for hours, bpm, _ in samples:
        if previous_hours is not None and hours - previous_hours > 10 / 60:
            segments.append([])
        segments[-1].append(f"{x_of(hours):.1f},{y_of(bpm):.1f}")
        previous_hours = hours
    for segment in segments:
        if len(segment) > 1:
            parts.append(f'<polyline class="line hr" points="{" ".join(segment)}"/>')

    parts.append("</svg>")
    chips = "".join(
        f'<span class="legend-item"><span class="swatch" '
        f'style="background:{ZONE_COLORS.get(str(zone.get("zone")), "var(--zone-light)")}">'
        f'</span>{_escape(zone.get("zone"))} {_escape(zone.get("min_bpm"))} to '
        f'{_escape(zone.get("max_bpm"))} bpm</span>'
        for zone in zones
        if zone.get("min_bpm") not in (None, "")
    )
    return "".join(parts) + (f'<div class="legend">{chips}</div>' if chips else "")
