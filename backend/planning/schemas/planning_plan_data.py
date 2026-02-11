from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PLANNING_EXPERT_TYPES = Literal[
    'Agriculture',
    'Chemistry',
    'Construction',
    'Electronics',
    'Food_Industries',
    'Fuel_Refining',
    'Manufacturing',
    'Metallurgy',
    'Resource_Extraction',
]

PLANNING_WORKFORCE_TYPES = Literal['pioneer', 'settler', 'technician', 'engineer', 'scientist']

PLANNING_COGC_TYPES = Literal[
    'AGRICULTURE',
    'CHEMISTRY',
    'CONSTRUCTION',
    'ELECTRONICS',
    'FOOD_INDUSTRIES',
    'FUEL_REFINING',
    'MANUFACTURING',
    'METALLURGY',
    'RESOURCE_EXTRACTION',
    'PIONEERS',
    'SETTLERS',
    'TECHNICIANS',
    'ENGINEERS',
    'SCIENTISTS',
]


class PlanningPlanData_Legacy(BaseModel):
    class PlanningPlanData_Legacy_Building(BaseModel):
        class PlanningPlanData_Legacy_Building_Recipe(BaseModel):
            recipeid: str = Field(...)
            amount: int = Field(..., ge=0)

        name: str = Field(..., min_length=1, max_length=3)
        amount: int = Field(..., ge=0)
        active_recipes: list[PlanningPlanData_Legacy_Building_Recipe]

    class PlanningPlanData_Legacy_Infrastructure(BaseModel):
        building: str = Field(..., min_length=3, max_length=3)
        amount: int = Field(..., ge=0)

    class PlanningPlanData_Legacy_Planet(BaseModel):
        class PlanningPlanData_Legacy_Planet_Experts(BaseModel):
            type: PLANNING_EXPERT_TYPES = Field(...)
            amount: int = Field(..., ge=0)

        class PlanningPlanData_Legacy_Planet_Workforce(BaseModel):
            type: PLANNING_WORKFORCE_TYPES = Field(...)
            lux1: bool = Field(...)
            lux2: bool = Field(...)

        planetid: str = Field(...)
        permits: int = Field(..., ge=0)
        corphq: bool = Field(...)
        cogc: PLANNING_COGC_TYPES | None = Field(None)
        experts: list[PlanningPlanData_Legacy_Planet_Experts]
        workforce: list[PlanningPlanData_Legacy_Planet_Workforce]

        @field_validator('cogc', mode='before')
        def normalize_uppercase(cls: Any, v: str) -> str | None:
            if isinstance(v, str):
                if v == '---':
                    return None

                upped = v.upper().replace(' ', '_')

                if upped in ['PIONEER', 'SETTLER', 'TECHNICIAN', 'ENGINEER', 'SCIENTIST']:
                    upped = f'{upped}S'

                return upped
            return v

    planet: PlanningPlanData_Legacy_Planet
    infrastructure: list[PlanningPlanData_Legacy_Infrastructure]
    buildings: list[PlanningPlanData_Legacy_Building]


class PlanningPlanData_V1(BaseModel):
    class PlanningPlanData_V1_Experts(BaseModel):
        type: PLANNING_EXPERT_TYPES = Field(...)
        amount: int = Field(..., ge=0)

    class PlanningPlanData_V1_Workforce(BaseModel):
        type: PLANNING_WORKFORCE_TYPES = Field(...)
        lux1: bool = Field(...)
        lux2: bool = Field(...)

    class PlanningPlanData_V1_Infrastructure(BaseModel):
        building: str = Field(..., min_length=3, max_length=3)
        amount: int = Field(..., ge=0)

    class PlanningPlanData_V1_Building(BaseModel):
        class PlanningPlanData_V1_Building_Recipe(BaseModel):
            recipeid: str = Field(...)
            amount: int = Field(..., ge=0)

        name: str = Field(..., min_length=1, max_length=3)
        amount: int = Field(..., ge=0)
        active_recipes: list[PlanningPlanData_V1_Building_Recipe]

    experts: list[PlanningPlanData_V1_Experts]
    workforce: list[PlanningPlanData_V1_Workforce]
    infrastructure: list[PlanningPlanData_V1_Infrastructure]
    buildings: list[PlanningPlanData_V1_Building]
