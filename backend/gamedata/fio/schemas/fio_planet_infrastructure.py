from math import floor

from pydantic import BaseModel, Field, computed_field


class FIOInfrastructureReport(BaseModel):
    infrastructure_report_id: str = Field(min_length=32, alias='InfrastructureReportId')
    explorers_grace_enabled: bool = Field(alias='ExplorersGraceEnabled')
    simulation_period: int = Field(ge=0, alias='SimulationPeriod')

    # Next Population
    next_population_pioneer: int = Field(alias='NextPopulationPioneer')
    next_population_settler: int = Field(alias='NextPopulationSettler')
    next_population_technician: int = Field(alias='NextPopulationTechnician')
    next_population_engineer: int = Field(alias='NextPopulationEngineer')
    next_population_scientist: int = Field(alias='NextPopulationScientist')

    # Population Difference
    population_difference_pioneer: int = Field(alias='PopulationDifferencePioneer')
    population_difference_settler: int = Field(alias='PopulationDifferenceSettler')
    population_difference_technician: int = Field(alias='PopulationDifferenceTechnician')
    population_difference_engineer: int = Field(alias='PopulationDifferenceEngineer')
    population_difference_scientist: int = Field(alias='PopulationDifferenceScientist')

    # Unemployment Rate
    unemployment_rate_pioneer: float = Field(alias='UnemploymentRatePioneer')
    unemployment_rate_settler: float = Field(alias='UnemploymentRateSettler')
    unemployment_rate_technician: float = Field(alias='UnemploymentRateTechnician')
    unemployment_rate_engineer: float = Field(alias='UnemploymentRateEngineer')
    unemployment_rate_scientist: float = Field(alias='UnemploymentRateScientist')

    # Open Jobs
    open_jobs_pioneer: float = Field(alias='OpenJobsPioneer')
    open_jobs_settler: float = Field(alias='OpenJobsSettler')
    open_jobs_technician: float = Field(alias='OpenJobsTechnician')
    open_jobs_engineer: float = Field(alias='OpenJobsEngineer')
    open_jobs_scientist: float = Field(alias='OpenJobsScientist')

    # Average Happiness
    average_happiness_pioneer: float = Field(alias='AverageHappinessPioneer')
    average_happiness_settler: float = Field(alias='AverageHappinessSettler')
    average_happiness_technician: float = Field(alias='AverageHappinessTechnician')
    average_happiness_engineer: float = Field(alias='AverageHappinessEngineer')
    average_happiness_scientist: float = Field(alias='AverageHappinessScientist')

    # Need Fulfillment
    need_fulfillment_life_support: float = Field(alias='NeedFulfillmentLifeSupport')
    need_fulfillment_safety: float = Field(alias='NeedFulfillmentSafety')
    need_fulfillment_health: float = Field(alias='NeedFulfillmentHealth')
    need_fulfillment_comfort: float = Field(alias='NeedFulfillmentComfort')
    need_fulfillment_culture: float = Field(alias='NeedFulfillmentCulture')
    need_fulfillment_education: float = Field(alias='NeedFulfillmentEducation')

    # Free Population
    @computed_field
    @property
    def free_pioneer(self) -> int:
        return floor(self.next_population_pioneer * self.unemployment_rate_pioneer)

    @computed_field
    @property
    def free_settler(self) -> int:
        return floor(self.next_population_settler * self.unemployment_rate_settler)

    @computed_field
    @property
    def free_technician(self) -> int:
        return floor(self.next_population_technician * self.unemployment_rate_technician)

    @computed_field
    @property
    def free_engineer(self) -> int:
        return floor(self.next_population_engineer * self.unemployment_rate_engineer)

    @computed_field
    @property
    def free_scientist(self) -> int:
        return floor(self.next_population_scientist * self.unemployment_rate_scientist)


class FIOPlanetInfrastructure(BaseModel):
    infrastructure_reports: list[FIOInfrastructureReport] = Field(alias='InfrastructureReports')
