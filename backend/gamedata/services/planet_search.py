import time
from typing import TypedDict, cast

import structlog
from django.db.models import Count, Q
from gamedata.models import GamePlanet, GamePlanetCOGCStatusChoices, GamePlanetEnvironmentChoices, queryset_gameplanet

logger = structlog.get_logger(__name__)


class SearchRequestType(TypedDict):
    materials: list[str]
    cogc_programs: list[str]
    must_be_fertile: bool
    must_have_localmarket: bool
    must_have_chamberofcommerce: bool
    must_have_warehouse: bool
    must_have_administrationcenter: bool
    must_have_shipyard: bool
    environment_rocky: bool
    environment_gaseous: bool
    environment_low_gravity: bool
    environment_high_gravity: bool
    environment_low_pressure: bool
    environment_high_pressure: bool
    environment_low_temperature: bool
    environment_high_temperature: bool


class GamePlanetSearchService:
    @staticmethod
    def search(search_request: SearchRequestType) -> list[GamePlanet]:
        start_time = time.perf_counter()
        log = logger.bind(search_request=search_request)
        log.info('planet_search_started')

        # annotated with active_cogc_program_type
        queryset = queryset_gameplanet()

        # resources filter: planet must have ALL searched resources
        materials = search_request.get('materials', [])
        if materials and len(materials) > 0:
            queryset = queryset.annotate(
                matched_materials=Count(
                    'resources',
                    filter=Q(resources__material_ticker__in=materials),
                    distinct=True,
                )
            ).filter(matched_materials=len(materials))

        # cogc programs filter:
        # - planet must have ANY of searched cogc programs
        # - cogc program must be active
        cogc_programs = search_request.get('cogc_programs', [])
        if cogc_programs and len(cogc_programs) > 0:
            queryset = queryset.filter(
                active_cogc_program_type__in=cogc_programs, cogc_program_status=GamePlanetCOGCStatusChoices.Active
            )

        # planet must be fertile, if requested
        if search_request['must_be_fertile']:
            queryset = queryset.filter(fertility_type=True)

        # environment checks on Surface
        rocky = search_request['environment_rocky']
        gaseous = search_request['environment_gaseous']

        if rocky and not gaseous:
            queryset = queryset.filter(surface=True)
        elif gaseous and not rocky:
            queryset = queryset.filter(surface=False)

        # gravity, pressure, temperature
        env_fields = [
            ('gravity', 'environment_low_gravity', 'environment_high_gravity'),
            ('pressure', 'environment_low_pressure', 'environment_high_pressure'),
            ('temperature', 'environment_low_temperature', 'environment_high_temperature'),
        ]

        # cast, so mypy is happy
        environment_req = cast(dict, search_request)

        for env_type, low_key, high_key in env_fields:
            choices = [GamePlanetEnvironmentChoices.NORMAL]

            if environment_req[low_key]:
                choices.append(GamePlanetEnvironmentChoices.LOW)
            if environment_req[high_key]:
                choices.append(GamePlanetEnvironmentChoices.HIGH)

            queryset = queryset.filter(**{f'{env_type}_type__in': choices})

        # local infrastructure checks
        if search_request.get('must_have_localmarket', False):
            queryset = queryset.filter(has_localmarket=True)
        if search_request.get('must_have_chamberofcommerce', False):
            queryset = queryset.filter(has_chamberofcommerce=True)
        if search_request.get('must_have_warehouse', False):
            queryset = queryset.filter(has_warehouse=True)
        if search_request.get('must_have_administrationcenter', False):
            queryset = queryset.filter(has_administrationcenter=True)
        if search_request.get('must_have_shipyard', False):
            queryset = queryset.filter(has_shipyard=True)

        # execute, transform to list
        data = list(queryset)

        duration = (time.perf_counter() - start_time) * 1000

        log.info('planet_search_completed', results=len(data), duration=duration)

        return data

    @staticmethod
    def search_by_planet_natural_id(planet_natural_ids: list[str]) -> list[GamePlanet]:
        queryset = queryset_gameplanet()
        queryset = queryset.filter(planet_natural_id__in=planet_natural_ids)

        return list(queryset)

    @staticmethod
    def search_by_term(search_term: str) -> list[GamePlanet]:
        if not search_term:
            return []

        queryset = queryset_gameplanet()

        queryset = queryset.filter(
            Q(planet_natural_id__icontains=search_term) | Q(planet_name__icontains=search_term)
        ).distinct()

        return list(queryset)
