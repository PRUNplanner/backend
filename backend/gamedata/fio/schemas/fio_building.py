from typing import Literal

from pydantic import BaseModel, Field, computed_field


class FIOBuildingCostSchema(BaseModel):
    material_ticker: str = Field(..., min_length=1, max_length=3, alias='CommodityTicker')
    material_amount: int = Field(..., ge=0, alias='Amount')


class FIOBuildingSchema(BaseModel):
    building_id: str = Field(..., alias='BuildingId')
    building_name: str = Field(..., alias='Name')
    building_ticker: str = Field(..., alias='Ticker')
    expertise: (
        Literal[
            'AGRICULTURE',
            'CHEMISTRY',
            'CONSTRUCTION',
            'ELECTRONICS',
            'FOOD_INDUSTRIES',
            'MANUFACTURING',
            'METALLURGY',
            'RESOURCE_EXTRACTION',
            'FUEL_REFINING',
        ]
        | None
    ) = Field(None, alias='Expertise')
    pioneers: int = Field(..., ge=0, alias='Pioneers')
    settlers: int = Field(..., ge=0, alias='Settlers')
    technicians: int = Field(..., ge=0, alias='Technicians')
    engineers: int = Field(..., ge=0, alias='Engineers')
    scientists: int = Field(..., ge=0, alias='Scientists')
    area_cost: int = Field(..., ge=0, alias='AreaCost')

    building_costs: list[FIOBuildingCostSchema] = Field(..., alias='BuildingCosts')

    @computed_field
    @property
    def building_type(self) -> Literal['PLANETARY', 'INFRASTRUCTURE', 'PRODUCTION']:
        if 'planetaryProject' in self.building_name:
            return 'PLANETARY'
        elif self.expertise is None:
            return 'INFRASTRUCTURE'
        else:
            return 'PRODUCTION'
