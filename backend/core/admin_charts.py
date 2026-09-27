"""
The admin's one place for chart colours and chart configs.

Builders return the `data`/`options` JSON strings that Unfold's `unfold/components/chart/line.html` and `bar.html`
take. Rules baked in: one y-axis per chart, counts start at 0, 2 px lines without point markers, index tooltips,
a legend only with two or more series. Unfold's own options are replaced when `options` is given, so every config
carries `scales.x.grid`/`scales.y.grid` for Unfold's dark-mode grid recolouring.
"""

import json
from collections.abc import Sequence
from typing import Literal, TypedDict

# single series and sparklines
LIME = '#c0e219'
# multi-series, fixed order, never cycled (validated on the #1e1e1e card surface)
SERIES = ('#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#9085e9')
# reserved for status, always shown with an icon and a label
STATUS = {'good': '#0ca30c', 'warning': '#fab219', 'serious': '#ec835a', 'critical': '#d03b3b'}
# "no data" cells and neutral text on the dark surface
NEUTRAL = '#4d4d4b'
TICK = '#8a8a86'

ALL_COLOURS = frozenset({LIME, *SERIES, *STATUS.values(), NEUTRAL, TICK})

type Number = int | float | None
type ChartKind = Literal['line', 'bar']


class ChartConfig(TypedDict):
    data: str
    options: str


class Chart(TypedDict):
    title: str
    kind: ChartKind
    config: ChartConfig
    height: int


class Change(TypedDict):
    text: str
    arrow: str
    colour: str


class Tile(TypedDict, total=False):
    label: str
    value: str
    sub: str
    href: str
    change: Change
    spark: ChartConfig


class Segment(TypedDict):
    label: str
    icon: str
    value: int
    pct: float
    colour: str
    href: str


class Summary(TypedDict, total=False):
    """A changelist strip: at most 4 tiles and 1 chart (plus an optional status split)."""

    tiles: list[Tile]
    charts: list[Chart]
    segments_title: str
    segments: list[Segment]


class Fact(TypedDict, total=False):
    """One entry of a change-page header."""

    label: str
    value: str
    href: str
    badge: str  # an Unfold label variant: success, info, warning, danger
    block: bool  # full width, pre-wrapped (long errors)


def series_colours(count: int) -> list[str]:
    """Lime for one series, else the fixed series order. More than six series must be folded into "Other"."""
    if count == 1:
        return [LIME]
    if count > len(SERIES):
        raise ValueError(f'{count} series, fold the 7th and later into "Other"')
    return list(SERIES[:count])


def fold_other(items: Sequence[tuple[str, int]], keep: int = len(SERIES) - 1) -> list[tuple[str, int]]:
    """Keeps the `keep` largest items and sums the rest into "Other", so a category chart never cycles colours."""
    ordered = sorted(items, key=lambda item: item[1], reverse=True)
    if len(ordered) <= keep + 1:
        return ordered
    return [*ordered[:keep], ('Other', sum(value for _, value in ordered[keep:]))]


def _axis(*, show: bool, grid: bool, begin_at_zero: bool = False) -> dict[str, object]:
    axis: dict[str, object] = {
        'display': show,
        'border': {'display': False},
        'grid': {'display': grid, 'drawTicks': False},
        'ticks': {'color': TICK, 'maxTicksLimit': 8, 'precision': 0},
    }
    if begin_at_zero:
        axis['beginAtZero'] = True
    return axis


def _options(*, legend: bool, horizontal: bool = False, stacked: bool = False, axes: bool = True) -> dict[str, object]:
    # the value axis carries the hairline grid and starts at zero; the category axis stays bare
    value_axis = _axis(show=axes, grid=axes, begin_at_zero=True)
    category_axis = _axis(show=axes, grid=False)
    if stacked:
        value_axis['stacked'] = True
        category_axis['stacked'] = True

    options: dict[str, object] = {
        'responsive': True,
        'maintainAspectRatio': False,
        'animation': False,
        'interaction': {'mode': 'index', 'intersect': False},
        'plugins': {
            'legend': {
                'display': legend,
                'align': 'end',
                'position': 'top',
                'labels': {'color': TICK, 'boxWidth': 8, 'boxHeight': 8, 'usePointStyle': True},
            },
            'tooltip': {'enabled': True, 'mode': 'index', 'intersect': False},
        },
        'elements': {'line': {'borderWidth': 2, 'tension': 0.25}, 'point': {'radius': 0, 'hoverRadius': 4}},
        'scales': {'x': value_axis, 'y': category_axis} if horizontal else {'x': category_axis, 'y': value_axis},
    }
    if horizontal:
        options['indexAxis'] = 'y'
    return options


def _config(labels: Sequence[str], datasets: list[dict[str, object]], options: dict[str, object]) -> ChartConfig:
    return {
        'data': json.dumps({'labels': list(labels), 'datasets': datasets}),
        'options': json.dumps(options),
    }


def line(labels: Sequence[str], series: Sequence[tuple[str, Sequence[Number]]]) -> ChartConfig:
    """Cumulative totals and rates. `None` values leave a gap instead of a false zero."""
    datasets: list[dict[str, object]] = [
        {'label': name, 'data': list(values), 'borderColor': colour, 'backgroundColor': colour, 'spanGaps': False}
        for (name, values), colour in zip(series, series_colours(len(series)), strict=True)
    ]
    return _config(labels, datasets, _options(legend=len(series) >= 2))


def bar(
    labels: Sequence[str],
    series: Sequence[tuple[str, Sequence[Number]]],
    *,
    horizontal: bool = False,
    stacked: bool = False,
) -> ChartConfig:
    """Daily counts and category splits."""
    datasets: list[dict[str, object]] = [
        {'label': name, 'data': list(values), 'backgroundColor': colour, 'borderRadius': 2, 'maxBarThickness': 28}
        for (name, values), colour in zip(series, series_colours(len(series)), strict=True)
    ]
    return _config(labels, datasets, _options(legend=len(series) >= 2, horizontal=horizontal, stacked=stacked))


def sparkline(values: Sequence[Number]) -> ChartConfig:
    datasets: list[dict[str, object]] = [
        {'label': 'value', 'data': list(values), 'borderColor': LIME, 'backgroundColor': LIME, 'spanGaps': True}
    ]
    options = _options(legend=False, axes=False)
    options['plugins'] = {'legend': {'display': False}, 'tooltip': {'enabled': False}}
    return _config([str(i) for i in range(len(values))], datasets, options)


def change(current: float | None, previous: float | None) -> Change | None:
    """Period change: arrow + signed number + %, coloured and with an arrow so colour is never the only cue."""
    if current is None or previous is None:
        return None
    delta = current - previous
    pct = f' ({delta / previous * 100:+.1f}%)' if previous else ''
    if delta > 0:
        return {'text': f'{delta:+,.0f}{pct}', 'arrow': '▲', 'colour': STATUS['good']}
    if delta < 0:
        return {'text': f'{delta:+,.0f}{pct}', 'arrow': '▼', 'colour': STATUS['critical']}
    return {'text': '±0', 'arrow': '▶', 'colour': TICK}
