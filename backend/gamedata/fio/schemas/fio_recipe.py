from pydantic import BaseModel, Field


class FIORecipeMaterialSchema(BaseModel):
    material_ticker: str = Field(..., min_length=1, max_length=3, alias='Ticker')
    material_amount: int = Field(..., ge=0, alias='Amount')


class FIORecipeSchema(BaseModel):
    standard_recipe_name: str = Field(..., alias='StandardRecipeName')
    recipe_name: str = Field(..., alias='RecipeName')
    building_ticker: str = Field(..., min_length=1, max_length=3, alias='BuildingTicker')
    time_ms: int = Field(..., ge=0, alias='TimeMs')
    inputs: list[FIORecipeMaterialSchema] = Field(..., alias='Inputs')
    outputs: list[FIORecipeMaterialSchema] = Field(..., alias='Outputs')
