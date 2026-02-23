from datetime import UTC, datetime

from drf_spectacular.utils import inline_serializer
from pydantic import BaseModel, Field, computed_field
from rest_framework import serializers


class FIOUserSiteBuildingMaterial(BaseModel):
    MaterialId: str = Field(..., min_length=32, max_length=32)
    MaterialName: str
    MaterialTicker: str = Field(..., min_length=1, max_length=3)
    MaterialAmount: int = Field(ge=0)


class FIOUserSiteBuildingSchema(BaseModel):
    SiteBuildingId: str = Field(..., min_length=65, max_length=65)
    BuildingId: str = Field(..., min_length=32, max_length=32)
    BuildingCreated: int
    BuildingName: str
    BuildingTicker: str = Field(..., min_length=2, max_length=3)
    BuildingLastRepair: datetime | None = Field(default=None)
    Condition: float = Field(ge=0.0)

    ReclaimableMaterials: list[FIOUserSiteBuildingMaterial] | None = Field(default=None)
    RepairMaterials: list[FIOUserSiteBuildingMaterial] | None = Field(default=None)

    @computed_field
    @property
    def AgeDays(self) -> int | None:
        if self.BuildingLastRepair is None:
            return None

        delta = datetime.now(UTC) - self.BuildingLastRepair
        return delta.days


class FIOUserSiteSchema(BaseModel):
    SiteId: str = Field(..., min_length=32, max_length=32)
    PlanetId: str = Field(..., min_length=32, max_length=32)
    PlanetIdentifier: str = Field(..., min_length=7)
    PlanetName: str | None = Field(default=None)
    PlanetFoundedEpochMs: int = Field(...)
    InvestedPermits: int
    MaximumPermits: int
    UserNameSubmitted: str = Field(...)
    Timestamp: datetime = Field(...)

    Buildings: list[FIOUserSiteBuildingSchema] = Field(default=[])

    @property
    def map_entry(self) -> tuple[str, str]:
        return self.SiteId, self.PlanetIdentifier

    def to_game_storage_dict(self):
        return self.model_dump(
            exclude={
                'SiteId': True,
                'PlanetId': True,
                'PlanetFoundedEpochMs': True,
                'UserNameSubmitted': True,
                'Timestamp': True,
                'Buildings': {
                    '__all__': {
                        'SiteBuildingId': True,
                        'BuildingId': True,
                        'BuildingCreated': True,
                        'BuildingName': True,
                        'ReclaimableMaterials': {'__all__': {'MaterialId': True, 'MaterialName': True}},
                        'RepairMaterials': {'__all__': {'MaterialId': True, 'MaterialName': True}},
                    }
                },
            },
            exclude_none=True,
        )


drf_sites_schema = inline_serializer(
    name='FIOUserSiteResponse',
    fields={
        'PlanetIdentifier': serializers.CharField(min_length=7),
        'PlanetName': serializers.CharField(allow_null=True),
        'PlanetFoundedEpochMs': serializers.IntegerField(),
        'InvestedPermits': serializers.IntegerField(),
        'MaximumPermits': serializers.IntegerField(),
        'Buildings': inline_serializer(
            name='FIOUserSiteBuildingResponse',
            many=True,
            fields={
                'BuildingTicker': serializers.CharField(min_length=2, max_length=3),
                'BuildingLastRepair': serializers.IntegerField(allow_null=True),
                'Condition': serializers.FloatField(min_value=0.0),
                'ReclaimableMaterials': inline_serializer(
                    name='FIOUserSiteMaterialResponse',
                    many=True,
                    fields={
                        'MaterialTicker': serializers.CharField(min_length=1, max_length=3),
                        'MaterialAmount': serializers.IntegerField(min_value=0),
                    },
                ),
                'RepairMaterials': inline_serializer(
                    name='FIOUserSiteMaterialResponseRef',
                    many=True,
                    fields={
                        'MaterialTicker': serializers.CharField(min_length=1, max_length=3),
                        'MaterialAmount': serializers.IntegerField(min_value=0),
                    },
                ),
            },
        ),
    },
)
