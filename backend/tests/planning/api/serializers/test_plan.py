from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from gamedata.models.game_planet import GamePlanetCOGCProgramChoices
from model_bakery import baker
from planning.api.serializers.plan import COGC_MAP
from planning.models import PlanningCOGCChoices
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis

pytestmark = pytest.mark.django_db

PLANET = 'OT-580b'


def _program(program_type: str | None, starts_in_days: int, ends_in_days: int) -> None:
    def epoch_ms(days: int) -> int:
        return int((timezone.now() + timedelta(days=days)).timestamp() * 1000)

    planet = baker.make('gamedata.GamePlanet', planet_natural_id=PLANET)
    baker.make(
        'gamedata.GamePlanetCOGCProgram',
        planet=planet,
        program_type=program_type,
        start_epochms=epoch_ms(starts_in_days),
        end_epochms=epoch_ms(ends_in_days),
    )


def _create_plan(api_client, **overrides: str) -> str:
    payload = {
        'plan_name': 'My Plan',
        'planet_natural_id': PLANET,
        'plan_permits_used': 1,
        'plan_corphq': False,
        'plan_data': plan_data_vallis,
        **overrides,
    }
    response = api_client.as_user(baker.make('user.User')).post(reverse('planning:plan'), data=payload, format='json')
    assert response.status_code == 201
    return response.data['plan_cogc']


def test_every_game_cogc_program_maps_to_a_plan_cogc() -> None:
    # a program type the game adds but the map misses would silently plan without its bonus
    assert set(COGC_MAP) == set(GamePlanetCOGCProgramChoices)


class TestPlanCOGCFromPlanet:
    @pytest.mark.parametrize(
        ('program_type', 'starts_in_days', 'ends_in_days', 'expected'),
        [
            (GamePlanetCOGCProgramChoices.Agriculture, -1, 6, PlanningCOGCChoices.AGRICULTURE),
            (GamePlanetCOGCProgramChoices.Workforce_Pioneers, -1, 6, PlanningCOGCChoices.PIONEERS),
            (GamePlanetCOGCProgramChoices.Agriculture, -14, -7, PlanningCOGCChoices.NONE),  # ended
            (GamePlanetCOGCProgramChoices.Agriculture, 1, 8, PlanningCOGCChoices.NONE),  # not started yet
            (None, -1, 6, PlanningCOGCChoices.NONE),  # no program voted
        ],
    )
    def test_create_takes_the_active_program(
        self, api_client, program_type: str | None, starts_in_days: int, ends_in_days: int, expected: str
    ) -> None:
        _program(program_type, starts_in_days, ends_in_days)

        assert _create_plan(api_client) == expected

    def test_unknown_planet_plans_without_cogc(self, api_client) -> None:
        assert _create_plan(api_client) == PlanningCOGCChoices.NONE

    def test_an_explicit_cogc_wins_over_the_planet(self, api_client) -> None:
        _program(GamePlanetCOGCProgramChoices.Agriculture, -1, 6)

        assert _create_plan(api_client, plan_cogc=PlanningCOGCChoices.METALLURGY) == PlanningCOGCChoices.METALLURGY
