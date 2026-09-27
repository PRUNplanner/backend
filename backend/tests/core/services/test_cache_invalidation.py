"""
One case per cache namespace: the second GET is a hit, and after the namespace's real
trigger the next GET is rebuilt with the new data. Private namespaces also check that
user A's trigger leaves user B's entry alone.
"""

from collections.abc import Callable
from dataclasses import dataclass
from unittest.mock import patch

import pytest
from analytics.tasks import analytics_bulk_materialize_empire_snapshots
from django.urls import reverse
from gamedata.fio.importers import save_buildings, save_materials, save_planets, save_recipes
from gamedata.fio.schemas.fio_webhook import FIOWebhookExchangeEndpointSchema
from gamedata.models import GameFIOPlayerData, GamePlanet
from gamedata.services.fio_webhook_handlers import FIOCXWebhookHandler
from gamedata.tasks import refresh_exchange_analytics
from model_bakery import baker
from planning.models import PlanningPlan
from rest_framework.test import APIClient
from tests.fixtures.fxt_fio_ship_data import fio_ship_data
from tests.fixtures.fxt_fio_sites_data import fio_sites_data
from tests.fixtures.fxt_fio_storage_data import fio_storage_data
from tests.fixtures.fxt_fio_warehouse_data import fio_warehouse_data
from user.models import User

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('locmem_cache')]

type Change = Callable[[], object]


@dataclass(frozen=True)
class Case:
    namespace: str
    private: bool
    # creates the data for both users, returns the url and the change + trigger
    arrange: Callable[[User, User], tuple[str, Change]]


def _materials(a: User, b: User) -> tuple[str, Change]:
    baker.make('gamedata.GameMaterial')
    return reverse('data:material-list'), lambda: save_materials([])


def _recipes(a: User, b: User) -> tuple[str, Change]:
    baker.make('gamedata.GameRecipe')
    return reverse('data:recipe-list'), lambda: save_recipes([])


def _buildings(a: User, b: User) -> tuple[str, Change]:
    baker.make('gamedata.GameBuilding')
    return reverse('data:building-list'), lambda: save_buildings([])


def _exchanges(a: User, b: User) -> tuple[str, Change]:
    baker.make('gamedata.GameExchangeAnalytics', ticker='FE', exchange_code='NC1')
    baker.make('gamedata.GameExchange', ticker_id='FE.NC1', ticker='FE', exchange_code='NC1', ask=1)

    def change() -> None:
        incoming = FIOWebhookExchangeEndpointSchema.model_construct(material_ticker='FE', exchange_code='NC1', ask=2)
        with (
            patch.object(FIOWebhookExchangeEndpointSchema, 'pubsub_dump', return_value={}),
            patch.object(FIOCXWebhookHandler, '_push_to_redis'),
        ):
            FIOCXWebhookHandler().process([incoming])

    return reverse('data:exchange-list'), change


def _cxpc(a: User, b: User) -> tuple[str, Change]:
    baker.make('gamedata.GameExchangeCXPC', ticker='FE', exchange_code='NC1', date_epoch=1)

    def change() -> None:
        baker.make('gamedata.GameExchangeCXPC', ticker='FE', exchange_code='NC1', date_epoch=2)
        with patch('django.db.connection.cursor'):
            refresh_exchange_analytics()

    return reverse('data:cxpc-market-data-full', kwargs={'ticker': 'FE', 'exchange_code': 'NC1'}), change


def _planet(a: User, b: User) -> tuple[str, Change]:
    planet: GamePlanet = baker.make('gamedata.GamePlanet', planet_natural_id='AB-001c', planet_name='Old')

    def change() -> None:
        planet.planet_name = 'New'
        planet.save()

    return reverse('data:planet-detail', kwargs={'planet_natural_id': 'AB-001c'}), change


def _planet_popr(a: User, b: User) -> tuple[str, Change]:
    planet: GamePlanet = baker.make('gamedata.GamePlanet', planet_natural_id='AB-001c')
    baker.make('gamedata.GamePlanetInfrastructureReport', planet=planet, simulation_period=1)

    def change() -> None:
        baker.make('gamedata.GamePlanetInfrastructureReport', planet=planet, simulation_period=2)
        planet.save()

    return reverse('data:planet-infrastructure', kwargs={'planet_natural_id': 'AB-001c'}), change


def _planet_list(a: User, b: User) -> tuple[str, Change]:
    baker.make('gamedata.GamePlanet', planet_natural_id='AB-001c')

    def change() -> None:
        baker.make('gamedata.GamePlanet', planet_natural_id='AB-002c')
        save_planets([])

    return reverse('data:planet-list'), change


def _storage(a: User, b: User) -> tuple[str, Change]:
    rows = {
        user: baker.make(
            'gamedata.GameFIOPlayerData',
            user=user,
            schema_version=1,
            storage_data=fio_storage_data,
            site_data=fio_sites_data,
            warehouse_data=fio_warehouse_data,
            ship_data=fio_ship_data,
        )
        for user in (a, b)
    }

    def change() -> None:
        row: GameFIOPlayerData = rows[a]
        row.storage_data = [{**fio_storage_data[0], 'Timestamp': '2030-01-01T00:00:00Z'}, *fio_storage_data[1:]]
        row.save()

    return reverse('data:storage-retrieve'), change


def _planning(a: User, b: User) -> tuple[str, Change]:
    plan: PlanningPlan = baker.make('planning.PlanningPlan', user=a, plan_name='Old')
    baker.make('planning.PlanningPlan', user=b)

    def change() -> None:
        plan.plan_name = 'New'
        plan.save()

    return reverse('planning:plan'), change


def _materials_insight(a: User, b: User) -> tuple[str, Change]:
    def change() -> None:
        state = {'empire_total': {'H2O': {'p': 5.0, 'c': 1.0, 'd': 4.0}}}
        baker.make('planning.PlanningEmpire', user=a, empire_state=state, needs_state_sync=True)
        analytics_bulk_materialize_empire_snapshots()

    return reverse('analytics:planning-insight-materials'), change


CASES = [
    Case('gamedata:materials', False, _materials),
    Case('gamedata:recipes', False, _recipes),
    Case('gamedata:buildings', False, _buildings),
    Case('gamedata:exchanges', False, _exchanges),
    Case('gamedata:cxpc', False, _cxpc),
    Case('gamedata:planet', False, _planet),
    Case('gamedata:planet (popr)', False, _planet_popr),
    Case('gamedata:planet-list', False, _planet_list),
    Case('gamedata:storage', True, _storage),
    Case('planning', True, _planning),
    Case('analytics:materials', False, _materials_insight),
]


@pytest.mark.parametrize('case', CASES, ids=[c.namespace for c in CASES])
def test_trigger_rebuilds_the_entry(
    case: Case, api_client: APIClient, user_factory: Callable[..., User], django_capture_on_commit_callbacks
) -> None:
    a, b = user_factory(), user_factory()
    url, change = case.arrange(a, b)

    def get(user: User) -> tuple[str, bytes]:
        api_client.force_authenticate(user)
        response = api_client.get(url)
        assert response.status_code == 200, response.content
        return response['X-Cache-Hit'], response.content

    first = get(a)
    assert get(a) == ('1', first[1])
    other = get(b) if case.private else None

    with django_capture_on_commit_callbacks(execute=True):
        change()

    hit, body = get(a)
    assert hit == '0'
    assert body != first[1]
    if other:
        assert get(b) == ('1', other[1])
