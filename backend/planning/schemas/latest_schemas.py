from typing import Literal

from planning.schemas.planning_cx_data import CXExchangeTickerPreferences_V1
from planning.schemas.planning_plan_data import PlanningPlanData_V1
from planning.schemas.planning_plan_summary import PlanSummary_V1
from pydantic import BaseModel

SCHEMA_LITERALS = Literal['PLANNING_DATA', 'CX_DATA', 'PLAN_SUMMARY']

LATEST_SCHEMA: dict[SCHEMA_LITERALS, type[BaseModel]] = {
    'PLANNING_DATA': PlanningPlanData_V1,
    'CX_DATA': CXExchangeTickerPreferences_V1,
    'PLAN_SUMMARY': PlanSummary_V1,
}
