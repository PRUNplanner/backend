from datetime import datetime

from pydantic import BaseModel, Field


class FIOUserSiteWarehouseSchema(BaseModel):
    WarehouseId: str = Field(..., min_length=65, max_length=65)
    StoreId: str = Field(..., min_length=32, max_length=32)
    Units: int
    WeightCapacity: float
    VolumeCapacity: float
    NextPaymentTimestampEpochMs: int
    FeeAmount: float | None = Field(default=None)
    FeeCurrency: str | None = Field(default=None)
    FeeCollectorId: str | None = Field(default=None)
    FeeCollectorName: str | None = Field(default=None)
    FeeCollectorCode: str | None = Field(default=None)
    LocationName: str
    LocationNaturalId: str
    UserNameSubmitted: str
    Timestamp: datetime = Field(...)

    @property
    def map_entry(self) -> tuple[str, str]:
        return self.StoreId, self.LocationNaturalId
