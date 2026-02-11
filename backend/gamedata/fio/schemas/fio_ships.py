from pydantic import BaseModel, Field


class FIOUserShipRepairMaterialSchema(BaseModel):
    ShipRepairMaterialId: str
    MaterialName: str
    MaterialId: str = Field(..., min_length=32, max_length=32)
    MaterialTicker: str = Field(..., min_length=1, max_length=3)
    Amount: int = Field(ge=0)


class FIOUserShipAddressLineSchema(BaseModel):
    LineId: str
    LineType: str
    NaturalId: str
    Name: str


class FIOUserShipSiteSchema(BaseModel):
    ShipId: str = Field(..., min_length=32, max_length=32)
    StoreId: str = Field(..., min_length=32, max_length=32)
    StlFuelStoreId: str = Field(..., min_length=32, max_length=32)
    FtlFuelStoreId: str = Field(..., min_length=32, max_length=32)
    Registration: str
    Name: str | None = Field(default=None)
    CommissioningTimeEpochMs: int
    Condition: float
    LastRepairEpochMs: int | None = Field(default=None)
    Location: str

    RepairMaterials: list[FIOUserShipRepairMaterialSchema] | None = Field(default=None)
    AddressLines: list[FIOUserShipAddressLineSchema] | None = Field(default=None)

    @property
    def map_entry(self) -> tuple[str, str]:
        return self.StoreId, self.Registration
