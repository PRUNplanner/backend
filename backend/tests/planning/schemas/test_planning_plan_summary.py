import pytest
from planning.schemas.planning_plan_summary import PlanSummary_V1
from pydantic import ValidationError
from tests.fixtures.planning.fxt_plan_summary import plan_summary


def test_valid_summary() -> None:
    summary = PlanSummary_V1.model_validate(plan_summary)
    assert summary.outputs[0].ticker == 'FE'
    assert summary.price_basis == 'CI1_30D'


@pytest.mark.parametrize(
    'override',
    [
        {'payback_days': None, 'price_basis': None, 'cogc': None},
        {'price_basis': 'CUSTOM'},
        {'price_basis': 'UNIVERSE_7D'},
        {'outputs': []},
        # an overbuilt plan still saves
        {'area_used': 2_000_000, 'area_total': 500, 'plan_cost': 1e13, 'profit': 1e13},
    ],
)
def test_optional_values(override: dict[str, object]) -> None:
    PlanSummary_V1.model_validate({**plan_summary, **override})


@pytest.mark.parametrize(
    'override',
    [
        {'v': 2},
        {'profit': float('inf')},
        {'profit': 1e16},
        {'payback_days': -1},
        {'area_used': -1},
        {'outputs': [{'ticker': 'FE', 'per_day': 1}] * 5},
        {'outputs': [{'ticker': 'FEOO', 'per_day': 1}]},
        {'price_basis': 'MOON_7D'},
        {'cogc': 'NOPE'},
        {'extra': 1},
    ],
)
def test_rejects_invalid(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PlanSummary_V1.model_validate({**plan_summary, **override})


def test_rejects_missing_field() -> None:
    data = dict(plan_summary)
    del data['profit']
    with pytest.raises(ValidationError):
        PlanSummary_V1.model_validate(data)
