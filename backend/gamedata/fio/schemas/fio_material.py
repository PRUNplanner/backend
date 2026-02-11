from pydantic import BaseModel, Field


class FIOMaterialSchema(BaseModel):
    material_id: str = Field(..., alias='MaterialId')
    category_name: str = Field(..., alias='CategoryName')
    category_id: str = Field(..., alias='CategoryId')
    name: str = Field(..., alias='Name')
    ticker: str = Field(..., alias='Ticker')
    weight: float = Field(..., alias='Weight')
    volume: float = Field(..., alias='Volume')
