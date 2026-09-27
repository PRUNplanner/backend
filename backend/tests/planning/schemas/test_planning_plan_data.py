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
