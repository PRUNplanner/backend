from django.db.models import Prefetch
from django.utils import timezone

from gamedata.api.serializer import GamePlanetSerializer, PlanetSearchIndexSerializer
from gamedata.models import GamePlanet, GamePlanetCOGCProgram, queryset_gameplanet


def build_planet_list() -> object:
    """The PLANET_LIST 'list' payload, shared by the view and the warm task."""
    return GamePlanetSerializer(queryset_gameplanet(), many=True).data


def build_planet_search_index() -> object:
    """The PLANET_SEARCH_INDEX 'index' payload, shared by the view and the warm task."""
    now_ms = int(timezone.now().timestamp() * 1000)
    queryset = GamePlanet.objects.prefetch_related(
        'resources',
        Prefetch('cogc_programs', queryset=GamePlanetCOGCProgram.objects.filter(end_epochms__gte=now_ms)),
    )
    return PlanetSearchIndexSerializer(queryset, many=True).data
