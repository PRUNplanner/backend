from typing import Literal

from pydantic import BaseModel, Field, computed_field

ENVIRONMENT_TYPE = Literal['NORMAL', 'LOW', 'HIGH']


def boundary_descriptor(value: float, lower: float, upper: float) -> ENVIRONMENT_TYPE:
    if value < lower:
        return 'LOW'
    elif value > upper:
        return 'HIGH'
    else:
        return 'NORMAL'


class FIOPlanetResourceSchema(BaseModel):
    material_id: str = Field(..., min_length=32, max_length=32, alias='MaterialId')
    resource_type: Literal['LIQUID', 'GASEOUS', 'MINERAL'] = Field(..., alias='ResourceType')
    factor: float = Field(ge=0, alias='Factor')


class FIOPlanetCOGCProgramSchema(BaseModel):
    program_type: (
        Literal[
            'ADVERTISING_AGRICULTURE',
            'ADVERTISING_CHEMISTRY',
            'ADVERTISING_CONSTRUCTION',
            'ADVERTISING_ELECTRONICS',
            'ADVERTISING_FOOD_INDUSTRIES',
            'ADVERTISING_FUEL_REFINING',
            'ADVERTISING_MANUFACTURING',
            'ADVERTISING_METALLURGY',
            'ADVERTISING_RESOURCE_EXTRACTION',
            'WORKFORCE_PIONEERS',
            'WORKFORCE_SETTLERS',
            'WORKFORCE_TECHNICIANS',
            'WORKFORCE_ENGINEERS',
            'WORKFORCE_SCIENTISTS',
        ]
        | None
    ) = Field(None, alias='ProgramType')
    start_epochms: int = Field(..., alias='StartEpochMs')
    end_epochms: int = Field(..., alias='EndEpochMs')


class FIOPlanetProductionFeeSchema(BaseModel):
    category: Literal[
        'AGRICULTURE',
        'CHEMISTRY',
        'CONSTRUCTION',
        'ELECTRONICS',
        'FOOD_INDUSTRIES',
        'MANUFACTURING',
        'METALLURGY',
        'RESOURCE_EXTRACTION',
        'FUEL_REFINING',
    ] = Field(..., alias='Category')
    workforce_level: Literal['PIONEER', 'SETTLER', 'TECHNICIAN', 'SCIENTIST', 'ENGINEER'] = Field(
        ..., alias='WorkforceLevel'
    )
    fee_amount: float = Field(ge=0.0, alias='FeeAmount')
    fee_currency: Literal['NCC', 'CIS', 'ICA', 'AIC'] = Field(..., alias='FeeCurrency')


class FIOPlanetSchema(BaseModel):
    planet_id: str = Field(min_length=32, max_length=32, alias='PlanetId')
    planet_natural_id: str = Field(..., alias='PlanetNaturalId')
    planet_name: str | None = Field(None, alias='PlanetName')
    system_id: str = Field(min_length=32, max_length=32, alias='SystemId')

    magnetic_field: float = Field(..., alias='MagneticField')
    mass: float = Field(..., alias='Mass')
    mass_earth: float = Field(..., alias='MassEarth')
    orbit_semimajor_axis: float = Field(..., alias='OrbitSemiMajorAxis')
    orbit_eccentricity: float = Field(..., alias='OrbitEccentricity')
    orbit_inclination: float = Field(..., alias='OrbitInclination')
    orbit_right_ascension: float = Field(..., alias='OrbitRightAscension')
    orbit_periapsis: float = Field(..., alias='OrbitPeriapsis')
    orbit_index: int = Field(..., alias='OrbitIndex')

    radiation: float = Field(..., alias='Radiation')
    radius: float = Field(..., alias='Radius')
    sunlight: float = Field(..., alias='Sunlight')

    gravity: float = Field(..., alias='Gravity')
    pressure: float = Field(..., alias='Pressure')
    temperature: float = Field(..., alias='Temperature')
    fertility: float = Field(..., alias='Fertility')
    surface: bool = Field(..., alias='Surface')

    has_localmarket: bool = Field(..., alias='HasLocalMarket')
    has_chamberofcommerce: bool = Field(..., alias='HasChamberOfCommerce')
    has_warehouse: bool = Field(..., alias='HasWarehouse')
    has_administrationcenter: bool = Field(..., alias='HasAdministrationCenter')
    has_shipyard: bool = Field(..., alias='HasShipyard')

    faction_code: Literal['NC', 'CI', 'AI', 'IC'] | None = Field(None, alias='FactionCode')
    faction_name: (
        Literal['NEO Charter Exploration', 'Castillo-Ito Mercantile', 'Antares Initiative', 'Insitor Cooperative']
        | None
    ) = Field(None, alias='FactionName')
    currency_code: Literal['NCC', 'CIS', 'AIC', 'ICA'] | None = Field(None, alias='CurrencyCode')
    currency_name: Literal['NCE Coupons', 'Sol', 'Martian Coin', 'Austral'] | None = Field(None, alias='CurrencyName')

    base_localmarket_fee: float = Field(..., alias='BaseLocalMarketFee')
    localmarket_fee_factor: float = Field(..., alias='LocalMarketFeeFactor')
    warehouse_fee: float = Field(..., alias='WarehouseFee')
    establishment_fee: float = Field(..., alias='EstablishmentFee')

    population_id: str = Field(min_length=32, max_length=32, alias='PopulationId')
    cogc_program_status: Literal['ACTIVE', 'PLANNED', 'ON_STRIKE'] | None = Field(None, alias='COGCProgramStatus')

    # computed fields

    @computed_field
    @property
    def gravity_type(self) -> ENVIRONMENT_TYPE:
        return boundary_descriptor(self.gravity, 0.25, 2.5)

    @computed_field
    @property
    def pressure_type(self) -> ENVIRONMENT_TYPE:
        return boundary_descriptor(self.pressure, 0.25, 2.0)

    @computed_field
    @property
    def temperature_type(self) -> ENVIRONMENT_TYPE:
        return boundary_descriptor(self.temperature, -25.0, 75.0)

    @computed_field
    @property
    def fertility_type(self) -> bool:
        if self.fertility > -1.0:
            return True
        else:
            return False

    # related schemas
    resources: list[FIOPlanetResourceSchema] = Field(..., alias='Resources')
    cogc_programs: list[FIOPlanetCOGCProgramSchema] = Field(..., alias='COGCPrograms')
    production_fees: list[FIOPlanetProductionFeeSchema] = Field(..., alias='ProductionFees')
