import json

import pytest
from core.admin_charts import (
    ALL_COLOURS,
    LIME,
    SERIES,
    STATUS,
    ChartConfig,
    bar,
    change,
    fold_other,
    line,
    series_colours,
    sparkline,
)

LABELS = ['a', 'b', 'c']
ONE = [('One', [1, 2, 3])]
THREE = [('A', [1, 2, 3]), ('B', [3, 2, 1]), ('C', [None, 1, 2])]

CONFIGS: dict[str, ChartConfig] = {
    'line-1': line(LABELS, ONE),
    'line-3': line(LABELS, THREE),
    'bar-1': bar(LABELS, ONE),
    'bar-3': bar(LABELS, THREE),
    'hbar': bar(LABELS, ONE, horizontal=True),
    'sparkline': sparkline([1, 2, 3]),
}


def colours_in(config: ChartConfig) -> set[str]:
    data = json.loads(config['data'])
    found: set[str] = set()
    for dataset in data['datasets']:
        for key in ('borderColor', 'backgroundColor'):
            value = dataset.get(key)
            if isinstance(value, list):
                found.update(str(item) for item in value)
            elif value:
                found.add(str(value))
    options = json.loads(config['options'])
    for axis in options['scales'].values():
        found.add(axis['ticks']['color'])
    return found


class TestChartRules:
    """AC19: one y-axis per chart, and every colour from core/admin_charts.py."""

    @pytest.mark.parametrize('name', CONFIGS)
    def test_single_value_axis(self, name: str) -> None:
        options = json.loads(CONFIGS[name]['options'])

        assert set(options['scales']) == {'x', 'y'}
        for dataset in json.loads(CONFIGS[name]['data'])['datasets']:
            assert 'yAxisID' not in dataset and 'xAxisID' not in dataset

    @pytest.mark.parametrize('name', CONFIGS)
    def test_colours_come_from_the_palette(self, name: str) -> None:
        assert colours_in(CONFIGS[name]) <= ALL_COLOURS

    @pytest.mark.parametrize('name', CONFIGS)
    def test_grid_objects_exist_for_unfold(self, name: str) -> None:
        # Unfold's dark-mode hook writes scales.x.grid.color and scales.y.grid.color
        scales = json.loads(CONFIGS[name]['options'])['scales']
        assert 'grid' in scales['x'] and 'grid' in scales['y']

    def test_counts_start_at_zero_on_the_value_axis(self) -> None:
        vertical = json.loads(CONFIGS['bar-1']['options'])['scales']
        horizontal = json.loads(CONFIGS['hbar']['options'])

        assert vertical['y']['beginAtZero'] is True
        assert horizontal['indexAxis'] == 'y'
        assert horizontal['scales']['x']['beginAtZero'] is True

    def test_legend_only_with_two_or_more_series(self) -> None:
        assert json.loads(CONFIGS['line-1']['options'])['plugins']['legend']['display'] is False
        assert json.loads(CONFIGS['line-3']['options'])['plugins']['legend']['display'] is True

    def test_gaps_stay_gaps(self) -> None:
        data = json.loads(CONFIGS['line-3']['data'])

        assert data['datasets'][2]['data'][0] is None


class TestPalette:
    def test_single_series_is_lime_and_multi_series_keeps_its_order(self) -> None:
        assert series_colours(1) == [LIME]
        assert series_colours(3) == list(SERIES[:3])

    def test_seven_series_must_be_folded(self) -> None:
        with pytest.raises(ValueError):
            series_colours(7)

    def test_fold_other(self) -> None:
        items = [(str(i), i) for i in range(1, 9)]

        folded = fold_other(items, keep=5)

        assert [label for label, _ in folded] == ['8', '7', '6', '5', '4', 'Other']
        assert folded[-1][1] == 1 + 2 + 3


class TestChange:
    def test_up_down_flat_and_unknown(self) -> None:
        up, down, flat = change(110, 100), change(90, 100), change(5, 5)

        assert up == {'text': '+10 (+10.0%)', 'arrow': '▲', 'colour': STATUS['good']}
        assert down is not None and down['arrow'] == '▼' and down['colour'] == STATUS['critical']
        assert flat is not None and flat['text'] == '±0'
        assert change(None, 3) is None and change(3, None) is None

    def test_no_percentage_from_zero(self) -> None:
        moved = change(4, 0)

        assert moved is not None and moved['text'] == '+4'
