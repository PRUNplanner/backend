import copy
from typing import get_args

import pytest
from planning.schemas.planning_plan_data import PLANNING_INFRASTRUCTURE_TYPES, PlanningPlanData_V1
from pydantic import ValidationError
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis


def _plan_data(**overrides: object) -> dict:
    return {**copy.deepcopy(plan_data_vallis), **overrides}


def test_accepts_all_infrastructure_codes() -> None:
    infrastructure = [{'building': code, 'amount': 1} for code in get_args(PLANNING_INFRASTRUCTURE_TYPES)]

    assert len(infrastructure) == 14
    PlanningPlanData_V1.model_validate(_plan_data(infrastructure=infrastructure))


def test_rejects_unknown_infrastructure_code() -> None:
    with pytest.raises(ValidationError):
        PlanningPlanData_V1.model_validate(_plan_data(infrastructure=[{'building': 'XYZ', 'amount': 1}]))


@pytest.mark.parametrize('name', ['', 'C'])
def test_rejects_short_building_name(name: str) -> None:
    buildings = [{'name': name, 'amount': 1, 'active_recipes': []}]

    with pytest.raises(ValidationError):
        PlanningPlanData_V1.model_validate(_plan_data(buildings=buildings))


def test_accepts_two_char_building_name() -> None:
    PlanningPlanData_V1.model_validate(_plan_data(buildings=[{'name': 'FP', 'amount': 1, 'active_recipes': []}]))


@pytest.mark.parametrize(
    'overrides',
    [
        pytest.param({'buildings': [{'name': 'FP', 'amount': 100_001, 'active_recipes': []}]}, id='building'),
        pytest.param({'buildings': [{'name': 'FP', 'amount': 10**20, 'active_recipes': []}]}, id='building-1e20'),
        pytest.param(
            {'buildings': [{'name': 'FP', 'amount': 1, 'active_recipes': [{'recipeid': 'FP#x', 'amount': 100_001}]}]},
            id='recipe',
        ),
        pytest.param({'infrastructure': [{'building': 'HB1', 'amount': 100_001}]}, id='infrastructure'),
        pytest.param({'experts': [{'type': 'Agriculture', 'amount': 101}]}, id='experts'),
        pytest.param({'buildings': [{'name': 'FP', 'amount': 1, 'active_recipes': []}] * 501}, id='list'),
    ],
)
def test_rejects_out_of_bounds(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        PlanningPlanData_V1.model_validate(_plan_data(**overrides))


def test_accepts_amounts_at_the_caps() -> None:
    PlanningPlanData_V1.model_validate(
        _plan_data(
            buildings=[{'name': 'FP', 'amount': 100_000, 'active_recipes': [{'recipeid': 'FP#x', 'amount': 100_000}]}],
            infrastructure=[{'building': 'HB1', 'amount': 100_000}],
            experts=[{'type': 'Agriculture', 'amount': 100}],
        )
    )
