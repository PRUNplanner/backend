from typing import Literal

from planning.schemas.planning_cx_data import CX_EXCHANGES
from planning.schemas.planning_plan_data import PLANNING_COGC_TYPES
from pydantic import BaseModel, ConfigDict, Field

# far above anything a plan can reach (plan_data allows 500 buildings of 100,000 each), so a save never fails on
# its summary; they keep overflowing values out of storage and the card
MAX_MONEY = 1e15
MAX_DAYS = 1e6
MAX_AREA = 10**12

# where the plan's sold outputs are priced: a CX exchange option, or the user's fixed prices
PRICE_BASIS = CX_EXCHANGES | Literal['CUSTOM']


class PlanSummaryOutput_V1(BaseModel):
    model_config = ConfigDict(extra='forbid')

    ticker: str = Field(..., min_length=1, max_length=3)
    per_day: float = Field(..., ge=0, le=MAX_MONEY, allow_inf_nan=False)


class PlanSummary_V1(BaseModel):
    """The plan's numbers as the frontend calculated them at save time. All money in ȼ per day."""

    model_config = ConfigDict(extra='forbid')

    v: Literal[1]
    profit: float = Field(..., ge=-MAX_MONEY, le=MAX_MONEY, allow_inf_nan=False)
    revenue: float = Field(..., ge=-MAX_MONEY, le=MAX_MONEY, allow_inf_nan=False)
    cost: float = Field(..., ge=-MAX_MONEY, le=MAX_MONEY, allow_inf_nan=False)
    # null when the plan makes no profit
    payback_days: float | None = Field(..., ge=0, le=MAX_DAYS, allow_inf_nan=False)
    plan_cost: float = Field(..., ge=0, le=MAX_MONEY, allow_inf_nan=False)
    area_used: int = Field(..., ge=0, le=MAX_AREA)
    area_total: int = Field(..., ge=0, le=MAX_AREA)
    # sold value descending
    outputs: list[PlanSummaryOutput_V1] = Field(..., max_length=4)
    price_basis: PRICE_BASIS | None
    cogc: PLANNING_COGC_TYPES | None
