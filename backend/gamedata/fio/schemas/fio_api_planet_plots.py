from datetime import datetime

from pydantic import BaseModel, Field

# FIO API (api.fnar.net), not FIO REST: only the fields the plots refresh reads, the rest is ignored


class FIOAPIPlanetPlotsSchema(BaseModel):
    Plots: int = Field(ge=0)
    SupportsGhostSites: bool


class FIOAPIPlanetSiteSchema(BaseModel):
    GhostPlot: bool
    Timestamp: datetime
    Type: str
