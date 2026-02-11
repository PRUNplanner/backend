from datetime import datetime

from pydantic import BaseModel, Field


class FIOUserStorageItemSchema(BaseModel):
    MaterialId: str = Field(..., min_length=32, max_length=32)
    MaterialName: str | None = Field(None)
    MaterialTicker: str | None = Field(None)
    MaterialCategory: str | None = Field(None)
    MaterialWeight: float = Field(...)
    MaterialVolume: float = Field(...)
    MaterialAmount: int = Field(...)
    MaterialValue: float = Field(...)
    MaterialValueCurrency: str | None = Field(None)
    Type: str = Field(...)
    TotalWeight: float = Field(..., ge=0.0)
    TotalVolume: float = Field(..., ge=0.0)


class FIOUserStorageSchema(BaseModel):
    StorageId: str = Field(..., min_length=32, max_length=32)
    AddressableId: str = Field(..., min_length=32, max_length=32)
    Name: str | None = Field(None)
    Type: str = Field(...)
    UserNameSubmitted: str = Field(...)
    Timestamp: datetime = Field(...)

    WeightCapacity: float = Field(...)
    VolumeCapacity: float = Field(...)
    WeightLoad: float = Field(...)
    VolumeLoad: float = Field(...)

    # 1:n
    StorageItems: list[FIOUserStorageItemSchema] | None = Field(None)

    def to_game_storage_dict(self):
        return self.model_dump(
            exclude={
                'StorageId': True,
                'AddressableId': True,
                'Type': True,
                'UserNameSubmitted': True,
                'Timestamp': True,
                'StorageItems': {
                    '__all__': {
                        'MaterialId': True,
                        'MaterialName': True,
                        'MaterialCategory': True,
                        'MaterialWeight': True,
                        'MaterialVolume': True,
                        'MaterialValue': True,
                        'MaterialValueCurrency': True,
                        'Type': True,
                        'TotalWeight': True,
                        'TotalVolume': True,
                    }
                },
            },
            exclude_none=True,
        )
